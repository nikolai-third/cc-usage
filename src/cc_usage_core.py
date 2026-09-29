"""Core helpers shared by cc-usage, the statusline wrapper and the limit hook.

Usage data comes from the (undocumented) endpoint behind Claude Code's /usage
command, authenticated with the OAuth token Claude Code already stores locally.
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime

INSTALL_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
CACHE = os.path.join(INSTALL_DIR, "cache.json")
LOCK = CACHE + ".lock"
CONFIG = os.path.join(INSTALL_DIR, "config.json")

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
KEYCHAIN_SERVICE = os.environ.get("CC_USAGE_KEYCHAIN_SERVICE", "Claude Code-credentials")

MAX_AGE = 120      # seconds before the cache is considered stale
RETRY_EVERY = 60   # min seconds between background refresh attempts (also covers failures)

LEVELS = ("warn", "stop")  # soft level: finish and warn the user; hard level: handoff and stop

# Limit names: "session" (5-hour), "weekly" (all models), and the lowercased model
# name for model-scoped weekly limits (e.g. "fable"). Each level has a percentage and
# an on/off switch, so turning a level off keeps its percentage for later.
DEFAULT_CONFIG = {
    # out of the box only the 5-hour limit drives the hook
    "limits": {
        "session": {"warn": 85, "warn_on": True, "stop": 95, "stop_on": True, "show": True},
        "weekly": {"warn": 85, "warn_on": False, "stop": 95, "stop_on": False, "show": True},
    },
    # applies to model-scoped limits (and any future limit) without their own entry
    "default_limit": {"warn": 85, "warn_on": False, "stop": 95, "stop_on": False, "show": True},
    "statusline": {
        "style": "percent",  # "percent" (5h 18%) or "bar" (5h ██░░░░░░░░ 18%)
        "bar_width": 10,
        "context": True,     # context window fill (turn off if your statusline shows it)
    },
    "hook": {
        "enabled": True,
        "repeat_warn": 25,     # tool calls between repeated warning reminders
        "repeat_stop": 5,      # tool calls between repeated stop reminders
        "warn_message": None,  # custom instruction text; None = built-in
        "stop_message": None,
    },
    # the statusline command that was configured before cc-usage wrapped it
    "wrapped_statusline": None,
}


class UsageError(Exception):
    pass


def _merge(base, override):
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


def load_config():
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        with open(CONFIG) as f:
            stored = json.load(f)
    except (OSError, ValueError):
        return config
    return _merge(config, _migrate(stored))


def _migrate(stored):
    """Upgrade configs written by earlier releases."""
    # first release: {"thresholds": {"session": [warn, stop], "weekly_all": [warn, stop]}}
    for kind, (warn, stop) in (stored.pop("thresholds", None) or {}).items():
        name = "weekly" if kind == "weekly_all" else kind
        stored.setdefault("limits", {}).setdefault(name, {"warn": warn, "stop": stop})
    # levels without an on/off switch: a number meant on, null meant off
    for entry in list((stored.get("limits") or {}).values()) + [stored.get("default_limit") or {}]:
        for level in LEVELS:
            if level in entry and f"{level}_on" not in entry:
                entry[f"{level}_on"] = entry[level] is not None
            if level in entry and entry[level] is None:
                del entry[level]
    if "context_bar" in stored:
        stored.setdefault("statusline", {})["context"] = stored.pop("context_bar")
    return stored


def _diff(config, defaults):
    out = {}
    for key, value in config.items():
        if isinstance(value, dict) and isinstance(defaults.get(key), dict):
            sub = _diff(value, defaults[key])
            if sub:
                out[key] = sub
        elif key not in defaults or defaults[key] != value:
            out[key] = value
    return out


def save_config(config):
    """Store only what differs from the defaults, so new defaults reach existing installs."""
    _write_json(CONFIG, _diff(config, DEFAULT_CONFIG))


def limit_settings(config, name):
    return {**config["default_limit"], **config["limits"].get(name, {})}


def threshold(settings, level):
    """The active percentage for a level, or None when the level is switched off."""
    return settings[level] if settings[f"{level}_on"] else None


def _write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def read_token():
    raw = None
    if sys.platform == "darwin":
        r = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
                           capture_output=True, text=True)
        if r.returncode == 0:
            raw = r.stdout
    if raw is None:
        try:
            with open(os.path.join(CONFIG_DIR, ".credentials.json")) as f:
                raw = f.read()
        except OSError:
            raise UsageError("no Claude Code OAuth credentials found; "
                             "log in to Claude Code with a Claude subscription first")
    try:
        oauth = json.loads(raw).get("claudeAiOauth") or {}
    except ValueError:
        raise UsageError("could not parse Claude Code credentials")
    token = oauth.get("accessToken")
    if not token:
        raise UsageError("Claude Code credentials have no OAuth access token "
                         "(API-key logins have no subscription usage)")
    expires = oauth.get("expiresAt")
    if expires and expires / 1000 < time.time():
        raise UsageError("OAuth token expired; start `claude` once to refresh it")
    return token


def fetch():
    token = read_token()
    # curl uses the system trust store (python.org builds often lack CA certs);
    # the auth header goes through stdin so the token never shows up in `ps`
    r = subprocess.run(
        ["curl", "-sf", "--max-time", "10", USAGE_URL,
         "-H", "anthropic-beta: oauth-2025-04-20", "-H", "@-"],
        input=f"Authorization: Bearer {token}", capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise UsageError(f"usage request failed (curl exit code {r.returncode})")
    try:
        return json.loads(r.stdout)
    except ValueError:
        raise UsageError("usage endpoint returned invalid JSON")


def write_cache(data):
    _write_json(CACHE, {"fetched_at": time.time(), "data": data})


def refresh_if_stale(fetched_at):
    """Start a detached `cc-usage --cache` run if the cache is stale; never blocks."""
    now = time.time()
    if now - fetched_at < MAX_AGE:
        return
    try:
        if now - os.path.getmtime(LOCK) < RETRY_EVERY:
            return
    except OSError:
        pass
    open(LOCK, "w").close()
    subprocess.Popen([sys.executable, os.path.join(INSTALL_DIR, "cc-usage"), "--cache"],
                     start_new_session=True, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def model_of(lim):
    return ((lim.get("scope") or {}).get("model") or {}).get("display_name")


def limit_name(lim):
    if lim["kind"] == "session":
        return "session"
    if lim["kind"] == "weekly_all":
        return "weekly"
    model = model_of(lim)
    return model.lower() if model else lim["kind"]


def short_label(name, lim):
    """Statusline label: 5h / 7d / model name."""
    return {"session": "5h", "weekly": "7d"}.get(name) or model_of(lim) or name


def long_label(name, lim):
    """Label used in agent-facing messages."""
    if name == "session":
        return "5-hour session limit"
    if name == "weekly":
        return "weekly limit"
    model = model_of(lim)
    return f"weekly {model} limit" if model else f"{name} limit"


def limits_from(data):
    """Map limit name -> limit dict, in API order."""
    return {limit_name(lim): lim for lim in data.get("limits") or []}


def load_limits():
    """Return (limits, age_seconds) from the cache; see limits_from()."""
    try:
        with open(CACHE) as f:
            cached = json.load(f)
    except (OSError, ValueError):
        cached = {"fetched_at": 0, "data": {}}
    fetched_at = cached.get("fetched_at", 0)
    refresh_if_stale(fetched_at)
    return limits_from(cached.get("data", {})), time.time() - fetched_at


def format_time(ts, fmt="%a %d %b %H:%M"):
    if not ts:
        return "-"
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().strftime(fmt)

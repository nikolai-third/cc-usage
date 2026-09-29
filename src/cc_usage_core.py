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

DEFAULT_CONFIG = {
    # limit kind -> [warning %, critical %]; null disables that level
    "thresholds": {"session": [80, 90], "weekly_all": [None, 95]},
    # the statusline command that was configured before cc-usage wrapped it
    "wrapped_statusline": None,
}


class UsageError(Exception):
    pass


def load_config():
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        with open(CONFIG) as f:
            config.update(json.load(f))
    except (OSError, ValueError):
        pass
    return config


def save_config(config):
    _write_json(CONFIG, config)


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


def load_limits():
    """Return (limits, age_seconds) from the cache: limits maps kind -> limit dict."""
    try:
        with open(CACHE) as f:
            cached = json.load(f)
    except (OSError, ValueError):
        cached = {"fetched_at": 0, "data": {}}
    fetched_at = cached.get("fetched_at", 0)
    refresh_if_stale(fetched_at)
    limits = {lim["kind"]: lim for lim in cached.get("data", {}).get("limits") or []}
    return limits, time.time() - fetched_at


def format_time(ts, fmt="%a %d %b %H:%M"):
    if not ts:
        return "-"
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().strftime(fmt)

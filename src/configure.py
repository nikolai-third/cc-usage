#!/usr/bin/env python3
"""Install or uninstall cc-usage into a Claude Code config directory.

  configure.py install [--no-statusline] [--no-hook] [--no-claude-md]
  configure.py uninstall

Install copies the scripts to $CLAUDE_CONFIG_DIR/cc-usage (default ~/.claude/cc-usage),
wraps the existing statusline, registers the limit hook and adds a short note to
CLAUDE.md. Every step is idempotent; settings.json is backed up before it is changed.
"""
import json
import os
import shlex
import shutil
import sys

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
TARGET = os.path.join(CONFIG_DIR, "cc-usage")
SETTINGS = os.path.join(CONFIG_DIR, "settings.json")
CLAUDE_MD = os.path.join(CONFIG_DIR, "CLAUDE.md")
FILES = ["cc-usage", "cc_usage_core.py", "statusline.py", "usage-hook.py", "configure.py"]
EXECUTABLES = {"cc-usage", "statusline.py", "usage-hook.py", "configure.py"}
HOOK_EVENTS = {"UserPromptSubmit": None, "PostToolUse": "*"}
MARK_START, MARK_END = "<!-- cc-usage:start -->", "<!-- cc-usage:end -->"


def say(msg):
    print(f"  {msg}")


def stable_python():
    """The interpreter to put into settings.json: never a venv that may disappear."""
    exe = sys.executable
    if sys.prefix != sys.base_prefix:
        for name in ("python3", f"python{sys.version_info[0]}.{sys.version_info[1]}"):
            candidate = os.path.join(sys.base_prefix, "bin", name)
            if os.access(candidate, os.X_OK):
                return candidate
    return exe


def command(script):
    return f"{shlex.quote(stable_python())} {shlex.quote(os.path.join(TARGET, script))}"


def is_ours(cmd, script):
    return isinstance(cmd, str) and f"cc-usage/{script}" in cmd


def load_settings():
    try:
        with open(SETTINGS) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_settings(settings):
    if os.path.exists(SETTINGS):
        shutil.copy2(SETTINGS, SETTINGS + ".cc-usage.bak")
    tmp = SETTINGS + ".tmp"
    with open(tmp, "w") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, SETTINGS)


def remove_hooks(settings):
    hooks = settings.get("hooks") or {}
    for event in HOOK_EVENTS:
        groups = []
        for group in hooks.get(event) or []:
            group["hooks"] = [h for h in group.get("hooks", [])
                              if not is_ours(h.get("command"), "usage-hook.py")]
            if group["hooks"]:
                groups.append(group)
        if groups:
            hooks[event] = groups
        else:
            hooks.pop(event, None)
    if "hooks" in settings and not hooks:
        del settings["hooks"]


def add_hooks(settings):
    remove_hooks(settings)
    hooks = settings.setdefault("hooks", {})
    for event, matcher in HOOK_EVENTS.items():
        group = {"hooks": [{"type": "command", "command": command("usage-hook.py"), "timeout": 5}]}
        if matcher:
            group = {"matcher": matcher, **group}
        hooks.setdefault(event, []).insert(0, group)


def wrap_statusline(settings, config):
    current = settings.get("statusLine")
    if not (current and is_ours(current.get("command"), "statusline.py")):
        config["wrapped_statusline"] = current
        if current:
            say(f"wrapping existing statusline: {current.get('command')}")
    wrapper = {"type": "command", "command": command("statusline.py")}
    if (config.get("wrapped_statusline") or {}).get("padding") is not None:
        wrapper["padding"] = config["wrapped_statusline"]["padding"]
    settings["statusLine"] = wrapper


def unwrap_statusline(settings, config):
    current = settings.get("statusLine")
    if not (current and is_ours(current.get("command"), "statusline.py")):
        return
    if config.get("wrapped_statusline"):
        settings["statusLine"] = config["wrapped_statusline"]
    else:
        del settings["statusLine"]


def claude_md_block():
    return f"""{MARK_START}
## Subscription usage (cc-usage)

- Check current Claude subscription usage with `{os.path.join(TARGET, "cc-usage")}` (`--json` for raw
  data) before large or long-running work and whenever the user asks about limits or usage.
- Messages starting with `[usage]` come from the cc-usage limit hook. Follow them: at the warning level
  finish the current work and tell the user; at the critical level write a handoff and stop unless the
  user explicitly says to continue.
{MARK_END}
"""


def strip_claude_md_block(text):
    start, end = text.find(MARK_START), text.find(MARK_END)
    if start == -1 or end == -1:
        return text
    return (text[:start].rstrip("\n") + "\n" + text[end + len(MARK_END):].lstrip("\n")).lstrip("\n")


def update_claude_md(add):
    try:
        with open(CLAUDE_MD) as f:
            text = f.read()
    except FileNotFoundError:
        text = ""
    text = strip_claude_md_block(text)
    if add:
        text = (text.rstrip("\n") + "\n\n" if text.strip() else "") + claude_md_block()
    if text.strip() or os.path.exists(CLAUDE_MD):
        with open(CLAUDE_MD, "w") as f:
            f.write(text)


def local_bin():
    path = os.path.expanduser("~/.local/bin")
    in_path = path in os.environ.get("PATH", "").split(os.pathsep)
    return os.path.join(path, "cc-usage") if in_path and os.path.isdir(path) else None


def install(args):
    print(f"Installing cc-usage into {TARGET}")
    os.makedirs(TARGET, exist_ok=True)
    if os.path.realpath(SRC_DIR) != os.path.realpath(TARGET):
        for name in FILES:
            shutil.copy2(os.path.join(SRC_DIR, name), os.path.join(TARGET, name))
    for name in EXECUTABLES:
        os.chmod(os.path.join(TARGET, name), 0o755)

    sys.path.insert(0, TARGET)
    import cc_usage_core as core
    config = core.load_config()
    settings = load_settings()
    if "--no-statusline" not in args:
        wrap_statusline(settings, config)
        say("statusline: usage segment enabled")
    if "--no-hook" not in args:
        add_hooks(settings)
        say("hook: limit warnings enabled (UserPromptSubmit + PostToolUse)")
    core.save_config(config)
    save_settings(settings)
    say(f"settings updated (backup: {SETTINGS}.cc-usage.bak)")

    if "--no-claude-md" not in args:
        update_claude_md(add=True)
        say(f"CLAUDE.md: usage note added ({CLAUDE_MD})")

    link = local_bin()
    if link and (not os.path.exists(link) or os.path.islink(link)):
        if os.path.islink(link):
            os.remove(link)
        os.symlink(os.path.join(TARGET, "cc-usage"), link)
        say(f"command: {link}")

    print("\nCurrent usage:")
    try:
        data = core.fetch()
        core.write_cache(data)
        for lim in data.get("limits") or []:
            say(f"{lim['kind']:16} {lim['percent']:>3}%")
    except core.UsageError as e:
        say(f"could not fetch usage yet: {e}")
    print("\nDone. Restart Claude Code to activate the hook and statusline.")


def uninstall():
    print(f"Uninstalling cc-usage from {TARGET}")
    sys.path.insert(0, TARGET if os.path.isdir(TARGET) else SRC_DIR)
    import cc_usage_core as core
    config = core.load_config()
    settings = load_settings()
    unwrap_statusline(settings, config)
    remove_hooks(settings)
    save_settings(settings)
    say("settings restored")
    update_claude_md(add=False)
    say("CLAUDE.md note removed")
    link = local_bin()
    if link and os.path.islink(link) and os.path.realpath(link).startswith(os.path.realpath(TARGET)):
        os.remove(link)
    shutil.rmtree(TARGET, ignore_errors=True)
    say(f"removed {TARGET}")
    print("\nDone. Restart Claude Code to apply.")


def main():
    args = sys.argv[1:]
    if not args or args[0] not in ("install", "uninstall"):
        sys.exit(__doc__)
    if args[0] == "install":
        install(args[1:])
    else:
        uninstall()


if __name__ == "__main__":
    main()

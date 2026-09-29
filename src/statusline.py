#!/usr/bin/env python3
"""Statusline wrapper: renders the previously configured statusline and appends usage.

The original statusline command (if any) is stored in config.json by the
installer and run unchanged; without one, a minimal built-in line is shown.
Usage comes from the cache, so rendering never waits on the network.
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import cc_usage_core as core  # noqa: E402

DIM, RESET = "\x1b[2m", "\x1b[0m"


def wrapped_line(raw, statusline):
    try:
        return subprocess.run(statusline["command"], shell=True, input=raw, capture_output=True,
                              text=True, timeout=5).stdout.rstrip("\n")
    except Exception:
        return ""


def builtin_line(raw):
    try:
        data = json.loads(raw)
    except ValueError:
        return ""
    parts = [f"{DIM}{(data.get('model') or {}).get('display_name', 'Claude')}{RESET}"]
    cwd = (data.get("workspace") or {}).get("current_dir")
    if cwd:
        parts.append(f"{DIM}{os.path.basename(cwd) or cwd}{RESET}")
    return " │ ".join(parts)


def color(pct):
    return "32" if pct < 50 else "33" if pct < 80 else "31"


def context_bar(raw, width=10):
    """`ctx ███░░░░░░░ 30%` from the context fill Claude Code passes on stdin."""
    try:
        remaining = (json.loads(raw).get("context_window") or {}).get("remaining_percentage")
    except (ValueError, AttributeError):
        return ""
    if remaining is None:
        return ""
    pct = max(0, min(100, round(100 - remaining)))
    filled = round(pct * width / 100)
    return (f"{DIM}ctx{RESET} \x1b[{color(pct)}m{'█' * filled}{DIM}{'░' * (width - filled)}{RESET} "
            f"\x1b[{color(pct)}m{pct}%{RESET}")


def usage_segment(config):
    limits, age = core.load_limits()
    parts = []
    for name, lim in limits.items():
        if core.limit_settings(config, name)["show"]:
            pct = lim["percent"]
            parts.append(f"{DIM}{core.short_label(name, lim)}{RESET} \x1b[{color(pct)}m{pct}%{RESET}")
    if not parts:
        return ""
    # a "?" marks data older than 10 minutes (offline, expired token, ...)
    return " ".join(parts) + (f"{DIM}?{RESET}" if age > 10 * 60 else "")


def main():
    raw = sys.stdin.read()
    config = core.load_config()
    statusline = config.get("wrapped_statusline")
    base = wrapped_line(raw, statusline) if statusline else builtin_line(raw)
    lines = base.split("\n")
    ctx = context_bar(raw) if config["context_bar"] else ""
    segment = " ".join(s for s in (ctx, usage_segment(config)) if s)
    if segment:
        lines[0] = f"{lines[0]} │ {segment}" if lines[0] else segment
    print("\n".join(lines))


if __name__ == "__main__":
    main()

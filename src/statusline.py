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
    remaining = (data.get("context_window") or {}).get("remaining_percentage")
    if remaining is not None:
        parts.append(f"{DIM}ctx{RESET} {round(100 - remaining)}%")
    return " │ ".join(parts)


def color(pct):
    return "32" if pct < 50 else "33" if pct < 80 else "31"


def usage_segment():
    limits, age = core.load_limits()
    parts = []
    for kind, label in (("session", "5h"), ("weekly_all", "7d")):
        if kind in limits:
            pct = limits[kind]["percent"]
            parts.append(f"{DIM}{label}{RESET} \x1b[{color(pct)}m{pct}%{RESET}")
    if not parts:
        return ""
    # a "?" marks data older than 10 minutes (offline, expired token, ...)
    return " ".join(parts) + (f"{DIM}?{RESET}" if age > 10 * 60 else "")


def main():
    raw = sys.stdin.read()
    statusline = core.load_config().get("wrapped_statusline")
    base = wrapped_line(raw, statusline) if statusline else builtin_line(raw)
    lines = base.split("\n")
    segment = usage_segment()
    if segment:
        lines[0] = f"{lines[0]} │ {segment}" if lines[0] else segment
    print("\n".join(lines))


if __name__ == "__main__":
    main()

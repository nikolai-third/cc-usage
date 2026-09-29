#!/usr/bin/env python3
"""PostToolUse / UserPromptSubmit hook: warns the agent about subscription limits.

Default thresholds (override in config.json; the most severe level across limits wins):
  5-hour session: >= 80% WARNING (finish, don't start new work), >= 90% CRITICAL (handoff + stop)
  weekly:         >= 95% CRITICAL only - a weekly warning would stall work for days

On PostToolUse a level is announced when first reached, then repeated every
REPEAT[level] tool calls. On UserPromptSubmit it is announced on every prompt.
Advisory only: never blocks a tool call. Fails open on any error.
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import cc_usage_core as core  # noqa: E402

REPEAT = {1: 25, 2: 5}             # tool calls between repeated reminders per level
MAX_DATA_AGE = 10 * 60             # ignore older data: the window may have reset since
STATE_DIR = os.path.join(core.INSTALL_DIR, "state")
STATE_TTL = 7 * 24 * 3600          # per-session state files older than this are pruned
LABELS = {"session": "5-hour session limit", "weekly_all": "weekly limit"}


def level_of(lim, thresholds):
    warning, critical = thresholds[lim["kind"]]
    pct = lim["percent"]
    if critical is not None and pct >= critical:
        return 2
    return 1 if warning is not None and pct >= warning else 0


def worst_limit(limits, thresholds):
    """Return (limit, level) for the most severe limit, or (None, 0) if none is known."""
    candidates = [limits[k] for k in thresholds if k in limits and k in LABELS]
    lim = max(candidates, key=lambda l: (level_of(l, thresholds), l["percent"]), default=None)
    return lim, level_of(lim, thresholds) if lim else 0


def load_state(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        prune_states()
        return {"level": 0, "calls": 0}


def prune_states():
    now = time.time()
    for name in os.listdir(STATE_DIR):
        path = os.path.join(STATE_DIR, name)
        try:
            if now - os.path.getmtime(path) > STATE_TTL:
                os.remove(path)
        except OSError:
            pass


def message(lim, level, critical_pct, cwd):
    reset = core.format_time(lim.get("resets_at"))
    head = f"[usage] {LABELS[lim['kind']]} at {lim['percent']}% (resets {reset})."
    if level == 1:
        return (f"{head} Finish the current task, avoid starting large new work or spawning many "
                "subagents, and mention the limit to the user."
                + (f" At {critical_pct}% you will have to write a handoff and stop." if critical_pct else ""))
    return (f"{head} CRITICAL: stop starting new work. Bring the current step to a consistent state, "
            "write HANDOFF.md in the working directory (goal, what is done, what is in progress, "
            "exact next steps, relevant files, and commands to verify the current state), then stop "
            "and tell the user the limit was reached. Continue only if the user explicitly tells "
            "you to go on past the limit.")


def main():
    data = json.load(sys.stdin)
    event = data.get("hook_event_name", "PostToolUse")
    thresholds = core.load_config()["thresholds"]
    limits, age = core.load_limits()
    lim, level = worst_limit(limits, thresholds)
    if lim is None or age > MAX_DATA_AGE:
        return

    session = re.sub(r"[^A-Za-z0-9_-]", "", data.get("session_id", "")) or "unknown"
    os.makedirs(STATE_DIR, exist_ok=True)
    state_path = os.path.join(STATE_DIR, f"{session}.json")
    state = load_state(state_path)

    if event == "UserPromptSubmit":
        emit = level > 0
        state = {"level": level, "calls": 0}
    elif level > state["level"]:
        emit, state = True, {"level": level, "calls": 0}
    elif level < state["level"] or level == 0:
        emit, state = False, {"level": level, "calls": 0}
    else:
        state["calls"] += 1
        emit = state["calls"] >= REPEAT[level]
        if emit:
            state["calls"] = 0

    with open(state_path, "w") as f:
        json.dump(state, f)
    if emit:
        critical_pct = thresholds[lim["kind"]][1]
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": message(lim, level, critical_pct, data.get("cwd") or os.getcwd()),
        }}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)

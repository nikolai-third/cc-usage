#!/usr/bin/env python3
"""PostToolUse / UserPromptSubmit hook: warns the agent about subscription limits.

Per-limit soft (warn) and hard (stop) levels come from config.json (edit with
`cc-usage config`); the most severe level across limits wins. Out of the box only the
5-hour limit is on: warn at 85% (finish, don't start new work), stop at 95% (handoff +
stop). Weekly and model-scoped limits (e.g. fable) are off by default; a model-scoped
limit, when on, only counts while the session runs on that model.

On PostToolUse a level is announced when first reached, then repeated every
repeat_warn / repeat_stop tool calls. On UserPromptSubmit it is announced on every
prompt. Advisory only: never blocks a tool call. Fails open on any error.
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import cc_usage_core as core  # noqa: E402

MAX_DATA_AGE = 10 * 60             # ignore older data: the window may have reset since
STATE_DIR = os.path.join(core.INSTALL_DIR, "state")
STATE_TTL = 7 * 24 * 3600          # per-session state files older than this are pruned
TRANSCRIPT_TAIL = 256 * 1024       # bytes of transcript scanned for the current model

WARN_MESSAGE = ("Finish the current task, avoid starting large new work or spawning many "
                "subagents, and mention the limit to the user.")
STOP_MESSAGE = ("CRITICAL: stop starting new work. Bring the current step to a consistent state, "
                "write HANDOFF.md in the working directory (goal, what is done, what is in "
                "progress, exact next steps, relevant files, and commands to verify the current "
                "state), then stop and tell the user the limit was reached. Continue only if the "
                "user explicitly tells you to go on past the limit.")


def current_model(transcript_path):
    """Model id of the latest assistant message, e.g. "claude-opus-5-5", or None."""
    try:
        with open(transcript_path, "rb") as f:
            f.seek(max(0, os.path.getsize(transcript_path) - TRANSCRIPT_TAIL))
            tail = f.read().decode("utf-8", "ignore")
    except (OSError, TypeError):
        return None
    models = re.findall(r'"model"\s*:\s*"([^"]+)"', tail)
    return models[-1] if models else None


def level_of(pct, settings):
    warn, stop = core.threshold(settings, "warn"), core.threshold(settings, "stop")
    if stop is not None and pct >= stop:
        return 2
    return 1 if warn is not None and pct >= warn else 0


def worst_limit(limits, config, model):
    """Return (name, limit, level) for the most severe applicable limit."""
    best = (None, None, 0)
    for name, lim in limits.items():
        scoped = core.model_of(lim)
        if scoped and not (model and scoped.lower() in model.lower()):
            continue  # a model-scoped limit only matters while running that model
        level = level_of(lim["percent"], core.limit_settings(config, name))
        if best[1] is None or (level, lim["percent"]) > (best[2], best[1]["percent"]):
            best = (name, lim, level)
    return best


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


def message(name, lim, level, config):
    hook = config["hook"]
    reset = core.format_time(lim.get("resets_at"))
    head = f"[usage] {core.long_label(name, lim)} at {lim['percent']}% (resets {reset})."
    if level == 2:
        return f"{head} {hook['stop_message'] or STOP_MESSAGE}"
    text = f"{head} {hook['warn_message'] or WARN_MESSAGE}"
    stop = core.threshold(core.limit_settings(config, name), "stop")
    if stop is not None and not hook["warn_message"]:
        text += f" At {stop}% you will have to write a handoff and stop."
    return text


def main():
    data = json.load(sys.stdin)
    config = core.load_config()
    if not config["hook"]["enabled"]:
        return
    event = data.get("hook_event_name", "PostToolUse")
    limits, age = core.load_limits()
    if age > MAX_DATA_AGE:
        return
    name, lim, level = worst_limit(limits, config, current_model(data.get("transcript_path")))
    if lim is None:
        return

    session = re.sub(r"[^A-Za-z0-9_-]", "", data.get("session_id", "")) or "unknown"
    os.makedirs(STATE_DIR, exist_ok=True)
    state_path = os.path.join(STATE_DIR, f"{session}.json")
    state = load_state(state_path)
    repeat = {1: config["hook"]["repeat_warn"], 2: config["hook"]["repeat_stop"]}

    if event == "UserPromptSubmit":
        emit = level > 0
        state = {"level": level, "calls": 0}
    elif level > state["level"]:
        emit, state = True, {"level": level, "calls": 0}
    elif level < state["level"] or level == 0:
        emit, state = False, {"level": level, "calls": 0}
    else:
        state["calls"] += 1
        emit = state["calls"] >= repeat[level]
        if emit:
            state["calls"] = 0

    with open(state_path, "w") as f:
        json.dump(state, f)
    if emit:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": message(name, lim, level, config),
        }}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)

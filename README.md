# cc-usage

Claude Code subscription usage — in your terminal, in the statusline, and in front of the agent itself.

- **`cc-usage`** prints the same numbers as `/usage`: 5-hour session, weekly limits, extra-usage spend.
- **Statusline segment** appends usage to your existing statusline (or shows a minimal one), as plain
  percentages or progress bars:

  ```
  ctx 15% 5h 18% 7d 20% Fable 0%
  ctx ██░░░░░░░░ 15% 5h ██░░░░░░░░ 18% 7d ██░░░░░░░░ 20% Fable ░░░░░░░░░░ 0%
  ```
- **Limit hook** tells the agent when you are close to a limit, so a long autonomous run wraps up and
  leaves a handoff instead of dying mid-task. Every limit has a soft level (finish current work, tell you)
  and a hard level (write `HANDOFF.md`, stop), each with its own percentage and on/off switch:

  | Limit                         | Soft (warn)   | Hard (stop)   |
  |-------------------------------|---------------|---------------|
  | 5-hour session                | 85%, on       | 95%, on       |
  | Weekly                        | 85%, off      | 95%, off      |
  | Weekly per model (e.g. Fable) | 85%, off      | 95%, off      |

  Per-model limits only count while the session runs on that model.
- **`cc-usage config`** changes all of that from the command line — or just ask the agent to.

```
$ cc-usage
session         18%   resets Tue 29 Sep 19:09
weekly          20%   resets Mon 05 Oct 23:59
fable            0%   resets Tue 06 Oct 00:00
extra usage     25%   12.50 / 50.00 USD
```

## Install

```sh
curl -fsSL https://raw.githubusercontent.com/nikolai-third/cc-usage/main/install.sh | bash
```

Then restart Claude Code. Requirements: macOS or Linux, Python 3.8+, `curl`, and Claude Code logged in
with a Claude subscription (Pro / Max / Team). API-key logins have no subscription usage to show.

The installer:

1. copies the scripts to `~/.claude/cc-usage/` (respects `CLAUDE_CONFIG_DIR`);
2. wraps your current `statusLine` command — it keeps running unchanged, the usage segment is appended;
3. registers the hook on `UserPromptSubmit` and `PostToolUse` in `~/.claude/settings.json`
   (backed up to `settings.json.cc-usage.bak` first);
4. adds a short marked block to `~/.claude/CLAUDE.md` so the agent knows it can run `cc-usage`;
5. links `cc-usage` into `~/.local/bin` if that directory is on your `PATH`.

Skip parts with `--no-statusline`, `--no-hook` or `--no-claude-md`:

```sh
curl -fsSL https://raw.githubusercontent.com/nikolai-third/cc-usage/main/install.sh | bash -s -- --no-claude-md
```

Re-running the installer is safe and is also how you update. If another tool later overwrites your
`statusLine`, run it again to re-wrap the new command.

## Uninstall

```sh
curl -fsSL https://raw.githubusercontent.com/nikolai-third/cc-usage/main/install.sh | bash -s -- --uninstall
```

Restores your original statusline, removes the hooks and the CLAUDE.md block, and deletes `~/.claude/cc-usage/`.

## Configuration

```
$ cc-usage config
limit            now   soft (warn)  hard (stop)  statusline
session          18%   85% on       95% on       shown
weekly           20%   85% off      95% off      shown
fable             0%   85% off      95% off      shown   (default, hook only while running Fable)

statusline: percent; context on
hook: on - repeat warning every 25 tool calls, stop every 5
warn message: built-in
stop message: built-in
```

Limit names are `session`, `weekly`, and the lowercased model name for per-model weekly limits.
A number sets a level and turns it on; `on` / `off` switch it without losing the number.
Changes apply immediately.

```sh
cc-usage config limit session --warn 70 --stop 90    # set both levels of the 5-hour limit
cc-usage config limit weekly --stop on               # turn on the weekly hard level (95%)
cc-usage config limit fable --warn 80 --stop on      # per-model limits work the same way
cc-usage config limit session --warn off             # hard level only
cc-usage config limit weekly --hide                  # remove from the statusline (--show to bring back)
cc-usage config limit session --reset                # back to the default
cc-usage config style bar                            # progress bars (percent for plain numbers)
cc-usage config style bar --width 5                  # shorter bars
cc-usage config context off                          # hide context fill (e.g. if your statusline has it)
cc-usage config hook off                             # disable the hook (on to enable)
cc-usage config hook --repeat-warn 10 --repeat-stop 3
cc-usage config message stop "Run /gsd-pause-work, then stop."   # custom instruction for a level
cc-usage config message stop --reset
cc-usage config reset                                # all defaults
```

Settings live in `~/.claude/cc-usage/config.json`, which stores only what you changed, so new defaults
from updates still reach you. The agent knows about `cc-usage config`, so "turn on the weekly stop at 90%"
in a Claude Code session works too.

## How it works

- Claude Code keeps its OAuth token in the macOS Keychain (`Claude Code-credentials`) or in
  `~/.claude/.credentials.json` on Linux. `cc-usage` reads it and calls the endpoint behind `/usage`.
  The token is passed to `curl` via stdin, so it never appears in the process list, and is never written anywhere.
- Results are cached in `~/.claude/cc-usage/cache.json`. The statusline and the hook only read the cache
  and trigger a background refresh when it is older than 2 minutes, so they never wait on the network.
  A `?` after the numbers means the data is older than 10 minutes.
- The hook injects `[usage] …` messages through `additionalContext`. A level is announced when first
  reached, then repeated every 25 (warning) or 5 (critical) tool calls, and on every prompt you send.
  Per-model limits are checked against the model of the latest reply in the session transcript.

## Caveats

- **Unofficial.** The usage endpoint is undocumented and may change or disappear without notice.
- **Advisory.** The hook never blocks tools (the agent needs them to write the handoff); stopping is up
  to the model following the instruction. Tell it to continue and it will.
- **Token refresh** is left to Claude Code. If Claude Code has not run for a while, the token may expire
  and `cc-usage` will ask you to start `claude` once.
- **Extra usage.** If you have extra usage enabled, hitting 100% does not stop Claude Code — it starts
  spending credits. The critical threshold is your chance to stop before that.

## License

MIT

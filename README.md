# corral

**English** | [简体中文](README.zh-CN.md)

Minimal hosting for interactive AI coding agents: Claude Code, Codex, pi and omp.

Each agent lives in its own small **pen** process. Callers open an agent by name, send it text, check its state, wait for the turn to end and read the reply. A human can attach from any terminal at any time to watch, answer a dialog or type into the conversation. Closing the window does not stop the agent.

```sh
corral start demo/alice --cwd ~/proj -- claude    # open an agent in the background
corral attach demo/alice                          # look at it; Ctrl-] detaches, the agent keeps running
corral send demo/alice "Read TASKS.md and do task 1."
corral wait demo/alice --timeout 600              # until this turn ends
corral reply demo/alice                           # the last reply, as clean text
corral stop demo/alice
```

## Why corral

- **Mechanism, not workflow.** corral opens, feeds, watches and stops agents. Who to open, what to say and what to do when an agent goes idle are decided by the caller: another agent, a script, or a harness you build on top.
- **One pen per agent, no central daemon.** Each agent is held by its own tiny process. If one pen dies, only that agent is affected. Commands run and exit; state lives in files.
- **State from the agents' own hooks, not from screen scraping.** corral knows whether an agent is `idle`, `working` or `blocked` from hook events, so a new version of an agent's UI does not break it.
- **The agent's own terminal UI is kept.** No headless mode, no editor protocol in place of the TUI. What you attach to is the real thing.
- **Python standard library only.** No third-party packages and no terminal multiplexer. The only exception is where an agent accepts hooks only in its own language (the pi and omp hooks are TypeScript files run by the agent's own runtime, with no dependencies).
- **Never touches global configuration.** Hooks are injected only into the agents corral starts; your global settings, hooks and plugins stay as they are.
- **A small, versioned contract.** Every command prints one line of JSON and uses documented exit codes. Callers must not read corral's internal files.

## Status

- In daily use. Real-agent smoke tests are recorded per agent version in [docs/ROADMAP.md](docs/ROADMAP.md) (R6). Known issue from the latest Claude Code run: longer messages sent with `corral send` arrive marked as pasted content, so the model may occasionally decline instructions in them; the first message via `--prompt` is not affected.
- Developed and tested on macOS. Linux is not tested. Windows is not supported.
- Contract version: `1` (`corral --version`).

## Supported agents

| Agent | Command after `--` | Notes |
|---|---|---|
| Claude Code | `claude` | |
| Codex | `codex --yolo` | Always `--yolo`: inside Codex's default sandbox the agent cannot reach corral. |
| pi | `pi` | Has no built-in permission prompts; dialogs from extensions show up as `blocked`. |
| omp | `omp --approval-mode yolo` | Do not use your home directory as `--cwd`. |

Any other command can be started and attached to as well; its state is reported as `unknown` because it has no hooks.

## Requirements

- Python 3.11 or newer
- macOS
- The agent CLIs you want to run, installed and logged in

## Install

```sh
git clone https://github.com/firegnu/corral.git
ln -s "$PWD/corral/bin/corral" ~/.local/bin/corral   # a symlink, so updates to the repo take effect
corral --version                                     # {"ok": true, "version": "…", "contract": "1"}
```

Optionally, install the **corral skill** so that your agents know how to use corral themselves (see [Delegating from a conversation](#delegating-from-a-conversation)):

```sh
corral install-skills --dry-run   # list what would be written
corral install-skills             # writes one SKILL.md for Claude Code and one for Codex, asks y/N first
```

It writes only `~/.claude/skills/corral/SKILL.md` and `~/.agents/skills/corral/SKILL.md` (pi and omp read the latter too). Use `--project <dir>` to install into one project instead, and `--remove` to uninstall.

## Core ideas

- **One agent, one pen, one name.** Names use `/` segments, for example `demo/alice`. Group them by project with a prefix.
- **Start every agent with corral, including your main chat agent.** An agent started by typing `claude` directly is invisible to corral: no pen, no hooks. Mixing the two is not supported.
- **Windows are only viewers.** Which terminal you watch from, whether you watch at all, and closing a window make no difference to the agent.
- **Instance ids.** When an agent exits and is started again under the same name, it gets a new instance id. Check it before sending, so you do not hand a message to a fresh conversation that knows nothing.

How it fits together:

```
                       hooks ──► events file ──► corral status / wait / reply
                         │
   agent ◄──► pty ◄──► pen ◄──► corral attach ◄──► any terminal window
                         ▲
                         └──── corral send / keys / stop
```

## Commands

| Command | What it does |
|---|---|
| `corral start <name> --cwd <dir> [--prompt <text>] [--unique] [--env K=V] -- <agent command>` | Open an agent in its own pen and return at once. `--prompt` is the first message (the only way to send one to a new agent). `--unique` adds a free suffix such as `demo/ask-3`. |
| `corral attach [--wait] <name>` | Connect this terminal to the agent. Ctrl-] detaches. `--wait` waits for the name to appear and reattaches after restarts. |
| `corral send <name> <text> [--after <other>]` | Send text only when the agent is idle, and confirm delivery through its input event. `--after` returns at once and delivers the text when the other agent's turn ends. |
| `corral keys <name> <key>...` | Send raw keys (`esc`, `enter`, `down`, `text:…`). No safety checks. |
| `corral status <name>` | State, instance id, last tool, when this turn started, last input source, number of attached windows. |
| `corral wait <name> [--timeout N] [--quiet N]` | Wait until the turn ends; returns `idle`, `blocked`, `stopped-quiet` or `unknown`. |
| `corral reply <name>` | The agent's last reply, as clean text. |
| `corral ls` | All live agents; also cleans up leftovers of dead ones. |
| `corral where <name>` | Working directory and agent process id. |
| `corral read <name>` | Recent raw output, for troubleshooting only. |
| `corral stop <name>` | End the agent the way that agent expects, wait until it has exited, and report the exit code. |
| `corral install-skills` | Install the corral skill for Claude Code and Codex (asks first). |
| `corral guide` | Print the usage guide written for agents. |

States: `starting` (starting up, or stuck on a startup dialog such as "trust this folder"), `idle` (the turn ended), `working`, `blocked` (a permission or question dialog is waiting for a human), `exiting`, `unknown`.

`idle` only means the turn ended. An agent may start a new turn by itself, for example when a background command finishes. `last_input_source` tells you whether the last input came from `send`, a `human` or the `agent` itself.

Exit codes:

| Code | Id | Meaning |
|---|---|---|
| 0 | `ok` | Success |
| 1 | `error` | Usage or internal error (details in the JSON `error` field) |
| 2 | `not_found` | No such agent, or it has exited |
| 3 | `not_delivered` | Sent but delivery not confirmed. Do not resend blindly; attach and look. |
| 4 | `timeout` | Timed out; wait again |
| 5 | `exists` | An agent with that name is already running |
| 6 | `sandbox` | Called from inside the Codex sandbox |
| 7 | `not_idle` | The agent is not idle |
| 8 | `human_active` | A human typed in an attached window within the last 30 seconds |
| 9 | `incompatible` | Pen protocol or event format version mismatch |

## Detach is not exit

| You do | Result |
|---|---|
| Press Ctrl-] | Only this window detaches. The agent keeps running. |
| Close the window or quit the terminal app | Same as detaching. |
| Type `/exit`, or press Ctrl-C twice inside the agent | The agent really exits and the conversation ends. |
| `corral stop <name>` | The agent exits cleanly and corral reports how. |

Typing `/exit` out of habit after attaching is the easiest way to lose an agent.

## Delegating from a conversation

The everyday way to use corral is not scripting. You tell the agent you are talking to, for example:

- "Open a Codex and look at the error handling in `src/parse.py`, then tell me what it thinks."
- "Ask both a Claude Code and a Codex this question and list where they disagree."
- "Hand the full test run to another agent and tell me when it is done; let's keep talking meanwhile."

With the corral skill installed, the agent starts a new one with `corral start`, waits, reads the reply and reports back. For long jobs it does not block: it runs `corral send <itself> "<reminder>" --after <other>` and ends its turn, and the reminder arrives like a typed message when the other agent's turn ends. The other agent ends its reply with a `DONE` line so an early wake-up can be told apart from a finished job.

Agents it opens stay open until you say to close them, so you can follow up or attach and talk to them directly.

## Dashboard

`tools/board` shows all agents in the terminal. Split your terminal into two panes and run:

```sh
<repo>/tools/board            # left: panel with a status table; press r for the selected agent's last reply
<repo>/tools/board --viewer   # right: a viewer that attaches to whatever the panel selects
```

- Rows are grouped by name prefix. Each row shows state, what the agent is doing (last tool and how long this turn has run), how long since the last output, attached windows, the last input source, directory and title.
- Markers: red `!` stuck, yellow `?` probably stuck, cyan `●` finished a turn you have not looked at, green `▶` shown on the right.
- A narrow pane hides nothing: each agent wraps onto a few lines. With many agents the list scrolls and shows how many are hidden above or below.
- Keys: ↑/↓ or j/k to select, Enter or click to show it on the right, `r` to show or hide the last reply, `s` to sort by state, `x` then `y` to stop it, `q` to quit.
- Options: `--prefix demo/`, `--bell`, `--once`.

It is a companion tool, not a `corral` subcommand; add a shell alias if you use it often.

## Orchestration skill: corral-dispatch (optional)

[`corral-dispatch-skill/`](corral-dispatch-skill/) is a skill for a **controller** agent that splits development work into task files, dispatches each task to a Claude Code, Codex or pi agent in its own branch and git worktree, reviews the results, asks for cross-review where mistakes are costly, and merges. corral itself does not reference it.

- Install by symlinking the folder into `~/.claude/skills/corral-dispatch` and `~/.agents/skills/corral-dispatch`.
- Enable it in a project by pasting the section from [`项目AGENTS模板.md`](corral-dispatch-skill/项目AGENTS模板.md) into the project's `AGENTS.md`. The project does not list which agent does what or which tier to use; the skill decides.
- Which agent: the controller follows one split table in the skill (Codex for backend, Claude Code for frontend, pi for light chores outside the product code).
- Which tier (light / regular / heavy model and effort) and whether to cross-review: [`route.py`](corral-dispatch-skill/route.py) asks a classification model (TypeSafe) with a short task summary. Confident answers are used; uncertain answers, a missing `TYPESAFE_API_KEY` or a failed call fall back to the controller's own judgment, so dispatching never blocks.
- Expensive configurations always need the user's approval.

Details are in [`corral-dispatch-skill/README.md`](corral-dispatch-skill/README.md) (Chinese).

## After an agent upgrade

Agents change their hooks without notice. After upgrading any of them, run the smoke test:

```sh
<repo>/tools/smoke                   # all four agents, about 8 minutes, uses a few tokens
<repo>/tools/smoke --agents claude   # just one
```

It uses its own short-path state directory and temporary folders, never touches your running agents, and checks mechanisms only (start, delivery, tool state, interrupt, stop, delegation), not model answers.

## Rules

- Start every agent with corral, the main chat agent included.
- One channel per agent: talk to a corral-started agent only through corral.
- Only touch agents you started. Do not send to or stop someone else's.
- Do not read the state directory (`~/.corral`); use command output only.
- corral never answers an agent's dialogs for you. `blocked` means a human should attach and decide.

## Out of scope

UI frontend, multi-pane layouts, terminal emulation, session resume, remote machines, Windows.

## Documentation

The detailed documents are in Chinese:

- [docs/USAGE.md](docs/USAGE.md): the user guide
- [docs/CONTRACT.md](docs/CONTRACT.md): the external contract (commands, fields, exit codes)
- [docs/DESIGN.md](docs/DESIGN.md): design and the reasons behind it
- [docs/ROADMAP.md](docs/ROADMAP.md): roadmap and smoke-test records
- [docs/SPIKE.md](docs/SPIKE.md): experiments and their results
- `corral guide`: the guide written for agents

## Development

```sh
python3 -m unittest discover -s tests -t .   # full suite, about 3.5 minutes
python3 -m unittest tests.test_skills        # one module
```

- Standard library only; do not add dependencies.
- Changes to the pen, the protocol, hooks or the event format are high risk: they need a compatibility plan, a test against the previous version, and a run with real agents.
- Before adding a feature, decide which layer it belongs to (kernel, skill, or a harness outside this repo). See section 16 of [docs/DESIGN.md](docs/DESIGN.md).

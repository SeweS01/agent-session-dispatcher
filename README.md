# Agent Session Dispatcher

An owner-only Telegram control surface for existing, uniquely named tmux sessions running the
Codex TUI. It lets an operator save session names, switch the active connection dynamically,
send text safely, and receive only structured user-facing Codex messages.

The dispatcher is deliberately standalone. It does not depend on another Telegram bot, a control
center, a web API, or a shared Python package.

## What it does

- Saves exact tmux session names chosen by the operator.
- Lets the operator assign a durable, human-readable label without renaming tmux.
- Shows saved sessions as online or offline.
- Routes ordinary private-chat text to one selected session at a time.
- Downloads owner-sent JPEG, PNG, and WebP images into private temporary storage and asks the
  selected Codex agent to open them with `view_image`.
- Binds that tmux pane to the rollout JSONL opened by its Codex process through `/proc/<pid>/fd`.
- Delivers qualified `commentary` and `final_answer` assistant messages only.
- Filters reasoning, tool traffic, system/developer records, user echoes, and unknown schemas.
- Resumes the same rollout cursor after a dispatcher restart without replaying old output.
- Automatically recognizes a recreated tmux session with the same saved name.

It never creates, restarts, renames, or kills managed tmux sessions. Raw TUI output is never used
as normal chat output.

## Requirements

- Linux with `/proc`
- Python 3.12+
- tmux
- Codex CLI running inside named tmux sessions
- A separate Telegram bot token from [@BotFather](https://t.me/BotFather)

The dispatcher must run as the same Unix user that owns the target tmux server and Codex
processes.

## Install

With `uv`:

```bash
uv tool install git+https://github.com/SeweS01/agent-session-dispatcher.git
agent-session-dispatcher setup
agent-session-dispatcher doctor
```

`setup` asks for the token locally, writes it to
`~/.config/agent-session-dispatcher/dispatcher.env` with mode `0600`, and can install a user
systemd service. Never paste the token into Telegram or commit the generated file.

After setup, open the new bot in a private chat and use `/add` to select an existing tmux session.
Photos and supported image documents are limited to 20 MB and expire from local storage after 24
hours.

## Telegram commands

- `/sessions` — saved session cards
- `/add [exact_name]` — discover or add an exact live tmux name
- `/current` — selected session and transcript status
- `/rename [new label]` — rename the active card; use `-` to restore its tmux name
- `/cancel` — cancel an interactive rename
- `/disconnect` — stop routing ordinary messages
- `/remove <exact_name>` — remove a saved card; the tmux session is untouched
- `/help` — concise help

Only the configured owner user ID in a private chat is accepted.

## Diagnostics

```bash
agent-session-dispatcher doctor
journalctl --user -u agent-session-dispatcher -f
```

`doctor` is read-only. It checks configuration permissions, credentials, tmux visibility, the
selected session, exact rollout binding, state storage, and Telegram connectivity.

## Development

```bash
uv sync
uv run ruff check .
uv run ruff format --check .
uv run mypy src/
uv run pytest
```

Architecture and recovery details live in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) and
[`docs/RECOVERY.md`](docs/RECOVERY.md).

## License

MIT. See [`LICENSE`](LICENSE) and [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

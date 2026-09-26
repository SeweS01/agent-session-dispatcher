# Architecture

## Scope and independence

The dispatcher is a separate owner-only Telegram bot, repository, process, virtual environment,
configuration, token, and systemd service. It has no runtime connection to CUS or to another
Telegram bot.

Its only shared infrastructure is the host itself: the Unix user's tmux server, `/proc`, Codex
rollout files, the network, and Telegram.

## Components

```text
Owner private chat
  -> Telegram adapter and owner/private-chat gate
  -> durable saved-session catalog
  -> active exact tmux name
  -> safe tmux input transport

Selected tmux pane PID
  -> descendant process tree
  -> open rollout JSONL through /proc/<pid>/fd
  -> Codex provider adapter
  -> per-turn duplicate filter
  -> persisted byte cursor
  -> Owner private chat
```

SQLite stores only session names, optional display labels, the active selection, and rollout read
cursors. It does not store transcripts, prompts, agent answers, provider credentials, or Telegram
tokens.

## Identity

The exact tmux session name is the sole durable agent identity. A typical operator convention is
`Client_Project_Specialist`.

An optional display label is presentation metadata only. Renaming a card never changes its exact
tmux identity, active selection, rollout binding, or cursor.

- No process or transcript fingerprint is exposed as identity.
- If a name disappears, its card is offline.
- If the same name reappears, its card becomes available automatically.
- The dispatcher never creates, restarts, renames, or kills that session.

A rollout path, device, inode, and byte offset are transient cursor data only. They prevent output
replay and never require the operator to add a session again.

## Exact rollout binding

Working directory and file modification time alone are ambiguous when multiple agents share a
project folder. The dispatcher therefore:

1. resolves the active pane PID for the exact tmux target `=name:`;
2. walks only that process tree through `/proc`;
3. inspects only JSONL files opened by those processes;
4. accepts only top-level `codex-tui` rollouts, excluding subagents;
5. selects the most recently written qualifying open rollout when Codex temporarily retains more
   than one file.

There is no global "newest rollout" or cwd fallback. Failure to prove the binding fails closed.

## Output contract

Only explicit assistant `commentary` and `final_answer` records in known Codex schemas are
eligible. The adapter discards reasoning, thinking, tools, tool results, system/developer
messages, user echoes, lifecycle events, and unknown records.

First manual selection starts at the current end of the rollout. A process restart resumes a
saved cursor only if path, device, and inode still match. A new rollout under the same tmux name
starts at its current end. Thus a brief dispatcher restart can deliver output missed from the same
rollout, while attaching to an existing or recreated agent never floods old history.

## Input contract

Input uses a unique named tmux buffer and bracketed paste. Before Enter, the dispatcher checks the
live pane for a blocking dialog, refuses to append over existing composer text, verifies that the
new payload or paste placeholder appeared, and checks again for a modal. After Enter it verifies
that the payload left the input bar.

An ambiguous result is reported without blindly pasting the message again. Pane content is neither
sent to Telegram nor stored.

## Security boundary

- Private Telegram chats only.
- Exact owner user ID allowlist.
- Token outside Git in a mode `0600` file.
- No shell interpolation and no arbitrary command execution.
- New session cards can be created only from the live tmux list.
- Single process enforced by a kernel file lock.
- State directory mode `0700`; SQLite database and lock mode `0600`.

## Provider boundary

Codex is the only implemented provider. Transcript discovery and parsing are isolated behind
provider-specific modules so another CLI can be added after its structured output is researched.
Raw terminal scraping is not an acceptable provider adapter.

## Known limitation

Another project agent may write to the same TUI at the same time. The dispatcher serializes its
own sends and verifies their delivery, but it cannot force an unrelated process to honor its lock.
Operational coordination with the parent project agent remains necessary.

# AGENTS.md

## Project rules

1. This service is an owner-only Telegram control surface for manually selected tmux sessions.
2. A configured tmux session name is its stable identity. Do not add fingerprints or automatic reassignment.
3. Do not create, restart, rename, or kill managed tmux sessions unless a future explicitly approved feature requires it.
4. Do not expose raw terminal output, model reasoning, tool payloads, system messages, or user echoes in normal chat.
5. The repository is self-contained. Do not import `telegram-ai-agent`, call CUS APIs, or depend
   on another local project at runtime.
6. TUI snapshots are not part of the user interface.
7. The bot is private-chat-only and owner-ID-only.
8. Never store Telegram tokens, provider credentials, `.env` files, auth files, or private transcripts in Git.
9. The existing `telegram-ai-agent` service and its topic mappings are out of scope and must not be mutated.
10. Shell commands must use argument arrays; never interpolate session names into a shell command.
11. Codex is the first provider adapter. Keep provider boundaries explicit and do not claim support
    for another provider until its structured transcript format is implemented and tested.
12. A rollout inode/path may be used only as a transient read cursor identity. It must never become
    a user-facing session fingerprint or require a saved tmux name to be added again.

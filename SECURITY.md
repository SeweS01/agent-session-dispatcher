# Security policy

Do not report vulnerabilities by posting bot tokens, Codex credentials, rollout contents, or tmux
captures in a public issue. Revoke an exposed Telegram token through BotFather immediately.

The security boundary assumes:

- the dispatcher runs as the same trusted Unix user as the target agents;
- the host, that user's tmux server, `/proc`, and local Codex files are trusted;
- only the configured Telegram owner may control the bot;
- saved tmux names are operator-selected and not discovered automatically into the catalog.

The dispatcher intentionally has no commands to execute a shell, create sessions, kill sessions,
or expose raw pane output.

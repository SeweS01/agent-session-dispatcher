# Recovery and rollback

## Service recovery

The dispatcher does not own managed tmux sessions. Restarting it is therefore safe for the agents:

```bash
systemctl --user restart agent-session-dispatcher
agent-session-dispatcher doctor
```

For the same rollout, the saved cursor resumes and delivers previously unseen eligible output.

## State backup

Stop the service briefly and copy the state directory:

```bash
systemctl --user stop agent-session-dispatcher
cp -a ~/.local/state/agent-session-dispatcher ~/.local/state/agent-session-dispatcher.backup
systemctl --user start agent-session-dispatcher
```

The state contains saved tmux names, display labels, cursors, and image attachments that have not
yet reached their 24-hour expiry. It does not contain the Telegram token. The token is in
`~/.config/agent-session-dispatcher/dispatcher.env` and should be backed up separately with
restricted permissions.

## Version rollback

Stable releases are tagged. To install a known version:

```bash
uv tool install --force \
  git+https://github.com/SeweS01/agent-session-dispatcher.git@v0.1.0
systemctl --user restart agent-session-dispatcher
agent-session-dispatcher doctor
```

Rolling back the package does not alter tmux sessions or the SQLite catalog. Back up state before
crossing a release that explicitly documents a database migration.

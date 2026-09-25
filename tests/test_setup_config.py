from __future__ import annotations

import stat
from pathlib import Path

from agent_session_dispatcher.config import load_settings
from agent_session_dispatcher.setup_cli import run_setup


def test_setup_writes_private_loadable_config(tmp_path: Path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    token = "123456789:abcdefghijklmnopqrstuvwxyz_ABCD"
    monkeypatch.setattr("getpass.getpass", lambda _prompt: token)
    monkeypatch.setattr("builtins.input", lambda _prompt: "987654321")
    path = tmp_path / "config" / "dispatcher.env"

    assert run_setup(path, install_service=False) == 0

    output = capsys.readouterr().out
    assert token not in output
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    settings = load_settings(path)
    assert settings.telegram_bot_token == token
    assert settings.owner_user_id == 987654321

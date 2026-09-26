from pathlib import Path

from agent_session_dispatcher.setup_cli import _service_text


def test_service_keeps_proc_and_tmux_namespace_visible() -> None:
    unit = _service_text(Path("/opt/bin/dispatcher"), Path("/opt/config/dispatcher.env"))

    assert "NoNewPrivileges=true" in unit
    assert "PrivateTmp=true" not in unit

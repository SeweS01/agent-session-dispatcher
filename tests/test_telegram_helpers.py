import pytest

from agent_session_dispatcher.domain import SavedSession
from agent_session_dispatcher.telegram_app import (
    _chunks,
    _live_token,
    _normalize_display_name,
    _session_identity,
    _short_label,
)


def test_chunks_preserve_content_within_telegram_limit() -> None:
    text = "a" * 2000 + "\n" + "b" * 2500
    chunks = list(_chunks(text, 3900))
    assert all(len(chunk) <= 3900 for chunk in chunks)
    assert "\n".join(chunks) == text


def test_live_token_is_stable_and_does_not_expose_name() -> None:
    name = "Client_Project_AmoCRM"
    assert _live_token(name) == _live_token(name)
    assert name not in _live_token(name)


def test_display_name_is_normalized_and_bounded() -> None:
    assert _normalize_display_name("  TTUZ\n  AmoCRM  ") == "TTUZ AmoCRM"
    with pytest.raises(ValueError, match="пустым"):
        _normalize_display_name(" \n ")
    with pytest.raises(ValueError, match="64"):
        _normalize_display_name("x" * 65)


def test_session_identity_keeps_display_and_tmux_names_separate() -> None:
    saved = SavedSession(
        id=7,
        name="TTUZ_managers-conversation_AmoCRM",
        added_at=1,
        last_selected_at=2,
        display_name="TTUZ — специалист AmoCRM",
    )

    assert saved.label == "TTUZ — специалист AmoCRM"
    assert _session_identity(saved) == (
        "Активная сессия: TTUZ — специалист AmoCRM\nTmux: TTUZ_managers-conversation_AmoCRM"
    )
    assert _short_label("x" * 60, limit=10) == "xxxxxxxxx…"

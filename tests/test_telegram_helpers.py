from agent_session_dispatcher.telegram_app import _chunks, _live_token


def test_chunks_preserve_content_within_telegram_limit() -> None:
    text = "a" * 2000 + "\n" + "b" * 2500
    chunks = list(_chunks(text, 3900))
    assert all(len(chunk) <= 3900 for chunk in chunks)
    assert "\n".join(chunks) == text


def test_live_token_is_stable_and_does_not_expose_name() -> None:
    name = "Client_Project_AmoCRM"
    assert _live_token(name) == _live_token(name)
    assert name not in _live_token(name)

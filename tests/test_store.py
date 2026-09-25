from __future__ import annotations

import stat
from pathlib import Path

from agent_session_dispatcher.domain import RolloutRef
from agent_session_dispatcher.store import StateStore


def test_catalog_selection_and_cursor_lifecycle(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state" / "dispatcher.sqlite3")
    try:
        first = store.add_session("Client_Project_AmoCRM")
        second = store.add_session("Client_Project_Bitrix24")
        assert [item.name for item in store.list_sessions()] == sorted(
            [first.name, second.name], key=str.casefold
        )

        assert store.select_session(first.name) is True
        rollout_path = tmp_path / "rollout.jsonl"
        rollout_path.write_text("{}\n", encoding="utf-8")
        rollout = RolloutRef.from_path(rollout_path)
        store.save_cursor(first.name, rollout, 3)
        assert store.get_cursor(first.name) is not None

        # Re-selecting the already active card is a no-op and preserves progress.
        assert store.select_session(first.name) is False
        assert store.get_cursor(first.name).offset == 3  # type: ignore[union-attr]

        # A manual switch starts the newly selected session at its current EOF.
        assert store.select_session(second.name) is True
        assert store.get_cursor(second.name) is None
        assert store.active_session() == second.name

        store.disconnect()
        assert store.active_session() is None
        assert store.remove_session(first.id) is True
        assert store.get_session(first.id) is None
    finally:
        store.close()

    assert stat.S_IMODE((tmp_path / "state").stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / "state" / "dispatcher.sqlite3").stat().st_mode) == 0o600

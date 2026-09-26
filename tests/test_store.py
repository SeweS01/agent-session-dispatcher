from __future__ import annotations

import sqlite3
import stat
from pathlib import Path

from agent_session_dispatcher.domain import RolloutRef
from agent_session_dispatcher.store import StateStore


def test_catalog_selection_and_cursor_lifecycle(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state" / "dispatcher.sqlite3")
    try:
        first = store.add_session("Client_Project_AmoCRM")
        second = store.add_session("Client_Project_Bitrix24")
        renamed = store.set_display_name(first.id, "Клиент — AmoCRM")
        assert renamed is not None
        assert renamed.display_name == "Клиент — AmoCRM"
        assert renamed.label == "Клиент — AmoCRM"
        assert store.get_session(first.id).display_name == "Клиент — AmoCRM"  # type: ignore[union-attr]
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


def test_legacy_catalog_is_migrated_without_losing_sessions(tmp_path: Path) -> None:
    path = tmp_path / "state" / "dispatcher.sqlite3"
    path.parent.mkdir()
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            added_at INTEGER NOT NULL,
            last_selected_at INTEGER
        )
        """
    )
    connection.execute(
        "INSERT INTO sessions(name, added_at) VALUES (?, ?)",
        ("Client_Project_AmoCRM", 1),
    )
    connection.commit()
    connection.close()

    store = StateStore(path)
    try:
        saved = store.get_session_by_name("Client_Project_AmoCRM")
        assert saved is not None
        assert saved.display_name is None

        updated = store.set_display_name(saved.id, "Понятное название")
        assert updated is not None
        assert updated.label == "Понятное название"
    finally:
        store.close()

    reopened = StateStore(path)
    try:
        restored = reopened.get_session_by_name("Client_Project_AmoCRM")
        assert restored is not None
        assert restored.display_name == "Понятное название"
    finally:
        reopened.close()

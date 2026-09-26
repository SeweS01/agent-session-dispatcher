"""Small durable SQLite catalog and transcript cursor store."""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path

from agent_session_dispatcher.domain import Cursor, RolloutRef, SavedSession


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._lock = threading.RLock()
        self._migrate()
        os.chmod(path, 0o600)

    def _migrate(self) -> None:
        with self._lock, self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    added_at INTEGER NOT NULL,
                    last_selected_at INTEGER,
                    display_name TEXT
                );

                CREATE TABLE IF NOT EXISTS app_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS cursors (
                    session_name TEXT PRIMARY KEY,
                    rollout_path TEXT NOT NULL,
                    rollout_device INTEGER NOT NULL,
                    rollout_inode INTEGER NOT NULL,
                    offset INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    FOREIGN KEY(session_name) REFERENCES sessions(name) ON DELETE CASCADE
                );
                """
            )
            columns = {
                str(row["name"])
                for row in self._connection.execute("PRAGMA table_info(sessions)").fetchall()
            }
            if "display_name" not in columns:
                self._connection.execute("ALTER TABLE sessions ADD COLUMN display_name TEXT")

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def add_session(self, name: str) -> SavedSession:
        now = int(time.time())
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT OR IGNORE INTO sessions(name, added_at) VALUES (?, ?)",
                (name, now),
            )
        session = self.get_session_by_name(name)
        if session is None:  # pragma: no cover - protected by the insert above
            raise RuntimeError("failed to save session")
        return session

    def remove_session(self, session_id: int) -> bool:
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT name FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row is None:
                return False
            name = str(row["name"])
            self._connection.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            self._connection.execute(
                "DELETE FROM app_state WHERE key = 'active_session' AND value = ?", (name,)
            )
            return True

    def list_sessions(self) -> list[SavedSession]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT id, name, added_at, last_selected_at, display_name
                FROM sessions
                ORDER BY COALESCE(last_selected_at, 0) DESC, name COLLATE NOCASE
                """
            ).fetchall()
        return [self._row_to_session(row) for row in rows]

    def get_session(self, session_id: int) -> SavedSession | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT id, name, added_at, last_selected_at, display_name
                FROM sessions WHERE id = ?
                """,
                (session_id,),
            ).fetchone()
        return self._row_to_session(row) if row is not None else None

    def get_session_by_name(self, name: str) -> SavedSession | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT id, name, added_at, last_selected_at, display_name
                FROM sessions WHERE name = ?
                """,
                (name,),
            ).fetchone()
        return self._row_to_session(row) if row is not None else None

    @staticmethod
    def _row_to_session(row: sqlite3.Row) -> SavedSession:
        selected = row["last_selected_at"]
        return SavedSession(
            id=int(row["id"]),
            name=str(row["name"]),
            added_at=int(row["added_at"]),
            last_selected_at=int(selected) if selected is not None else None,
            display_name=str(row["display_name"]) if row["display_name"] is not None else None,
        )

    def set_display_name(self, session_id: int, display_name: str | None) -> SavedSession | None:
        with self._lock, self._connection:
            result = self._connection.execute(
                "UPDATE sessions SET display_name = ? WHERE id = ?",
                (display_name, session_id),
            )
        if result.rowcount == 0:
            return None
        return self.get_session(session_id)

    def active_session(self) -> str | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT value FROM app_state WHERE key = 'active_session'"
            ).fetchone()
        return str(row["value"]) if row is not None else None

    def select_session(self, name: str) -> bool:
        """Select ``name`` and reset history only when this is a manual switch.

        Returns True when the active name changed. Keeping the cursor on a no-op selection
        prevents tapping the already-active card from losing an in-flight response.
        """
        if self.get_session_by_name(name) is None:
            raise KeyError(name)
        now = int(time.time())
        previous = self.active_session()
        changed = previous != name
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO app_state(key, value) VALUES ('active_session', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (name,),
            )
            self._connection.execute(
                "UPDATE sessions SET last_selected_at = ? WHERE name = ?", (now, name)
            )
            if changed:
                self._connection.execute("DELETE FROM cursors WHERE session_name = ?", (name,))
        return changed

    def disconnect(self) -> None:
        with self._lock, self._connection:
            self._connection.execute("DELETE FROM app_state WHERE key = 'active_session'")

    def get_cursor(self, session_name: str) -> Cursor | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT rollout_path, rollout_device, rollout_inode, offset
                FROM cursors WHERE session_name = ?
                """,
                (session_name,),
            ).fetchone()
        if row is None:
            return None
        rollout = RolloutRef(
            path=Path(str(row["rollout_path"])),
            device=int(row["rollout_device"]),
            inode=int(row["rollout_inode"]),
        )
        return Cursor(session_name=session_name, rollout=rollout, offset=int(row["offset"]))

    def save_cursor(self, session_name: str, rollout: RolloutRef, offset: int) -> None:
        if offset < 0:
            raise ValueError("cursor offset cannot be negative")
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO cursors(
                    session_name, rollout_path, rollout_device, rollout_inode, offset, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_name) DO UPDATE SET
                    rollout_path = excluded.rollout_path,
                    rollout_device = excluded.rollout_device,
                    rollout_inode = excluded.rollout_inode,
                    offset = excluded.offset,
                    updated_at = excluded.updated_at
                """,
                (
                    session_name,
                    str(rollout.path),
                    rollout.device,
                    rollout.inode,
                    offset,
                    int(time.time()),
                ),
            )

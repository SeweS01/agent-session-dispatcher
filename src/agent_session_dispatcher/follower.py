"""Follow the structured transcript of the currently selected tmux session."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from agent_session_dispatcher.domain import FollowState, FollowStatus, RolloutRef
from agent_session_dispatcher.providers.codex import OutputDeduplicator, parse_line
from agent_session_dispatcher.rollout import resolve_rollout
from agent_session_dispatcher.store import StateStore
from agent_session_dispatcher.tmux import TmuxClient, TmuxError

logger = logging.getLogger(__name__)

OutputCallback = Callable[[str, str], Awaitable[None]]


class SessionFollower:
    def __init__(
        self,
        store: StateStore,
        tmux: TmuxClient,
        output_callback: OutputCallback,
        *,
        poll_interval: float = 0.25,
    ) -> None:
        self.store = store
        self.tmux = tmux
        self.output_callback = output_callback
        self.poll_interval = poll_interval
        self.status = FollowStatus(None, FollowState.DISCONNECTED)

    async def follow(self, session_name: str) -> None:
        rollout: RolloutRef | None = None
        offset = 0
        dedupe = OutputDeduplicator()
        next_resolve = 0.0
        self.status = FollowStatus(session_name, FollowState.OFFLINE)
        try:
            while True:
                if not await self.tmux.has_session(session_name):
                    rollout = None
                    self.status = FollowStatus(session_name, FollowState.OFFLINE)
                    await asyncio.sleep(max(self.poll_interval, 0.5))
                    continue

                now = asyncio.get_running_loop().time()
                if rollout is None or now >= next_resolve:
                    next_resolve = now + 1.0
                    try:
                        resolved = await resolve_rollout(self.tmux, session_name)
                    except (TmuxError, OSError):
                        resolved = None
                    if resolved is None:
                        rollout = None
                        self.status = FollowStatus(
                            session_name,
                            FollowState.WAITING_FOR_CODEX,
                            "Точная Codex JSONL-сессия пока не найдена.",
                        )
                        await asyncio.sleep(max(self.poll_interval, 0.5))
                        continue
                    if rollout != resolved:
                        rollout = resolved
                        offset = self._initial_offset(session_name, rollout)
                        dedupe.reset()
                        self.status = FollowStatus(session_name, FollowState.WATCHING)

                assert rollout is not None
                try:
                    offset = await self._read_available(session_name, rollout, offset, dedupe)
                except FileNotFoundError:
                    rollout = None
                except OSError as exc:
                    logger.warning("rollout read failed for %s: %s", session_name, exc)
                    self.status = FollowStatus(
                        session_name, FollowState.ERROR, "Не удалось прочитать Codex JSONL."
                    )
                await asyncio.sleep(self.poll_interval)
        except asyncio.CancelledError:
            raise
        finally:
            if self.status.session_name == session_name:
                self.status = FollowStatus(None, FollowState.DISCONNECTED)

    def _initial_offset(self, session_name: str, rollout: RolloutRef) -> int:
        size = rollout.path.stat().st_size
        cursor = self.store.get_cursor(session_name)
        if (
            cursor is not None
            and cursor.rollout.path == rollout.path
            and cursor.rollout.device == rollout.device
            and cursor.rollout.inode == rollout.inode
            and 0 <= cursor.offset <= size
        ):
            return cursor.offset
        self.store.save_cursor(session_name, rollout, size)
        return size

    async def _read_available(
        self,
        session_name: str,
        rollout: RolloutRef,
        offset: int,
        dedupe: OutputDeduplicator,
    ) -> int:
        records = await asyncio.to_thread(_read_complete_records, rollout.path, offset)
        for line, end_offset in records:
            parsed = parse_line(line)
            if parsed.turn_started:
                dedupe.reset()
            output = parsed.output
            if output is not None and not dedupe.is_duplicate(output):
                await self.output_callback(session_name, output.text)
            self.store.save_cursor(session_name, rollout, end_offset)
            offset = end_offset
        return offset


def _read_complete_records(path: Path, offset: int) -> list[tuple[str, int]]:
    records: list[tuple[str, int]] = []
    with path.open("rb") as handle:
        handle.seek(offset)
        while True:
            line = handle.readline()
            if not line:
                break
            if not line.endswith(b"\n"):
                break
            records.append((line.decode("utf-8", errors="replace").strip(), handle.tell()))
    return records


class FollowerSupervisor:
    """Keep exactly one follower aligned with the durable active session name."""

    def __init__(self, store: StateStore, follower: SessionFollower) -> None:
        self.store = store
        self.follower = follower
        self._task: asyncio.Task[None] | None = None
        self._active: str | None = None

    @property
    def status(self) -> FollowStatus:
        return self.follower.status

    async def run(self) -> None:
        try:
            while True:
                selected = self.store.active_session()
                if selected != self._active:
                    await self._replace(selected)
                await asyncio.sleep(0.2)
        except asyncio.CancelledError:
            raise
        finally:
            await self._replace(None)

    async def _replace(self, session_name: str | None) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._active = session_name
        if session_name is not None:
            self._task = asyncio.create_task(
                self.follower.follow(session_name), name=f"follow:{session_name}"
            )

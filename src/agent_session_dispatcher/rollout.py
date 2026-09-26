"""Bind an exact tmux pane process tree to its open Codex rollout."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_session_dispatcher.domain import RolloutRef
from agent_session_dispatcher.tmux import TmuxClient, TmuxPane


@dataclass(frozen=True)
class RolloutBinding:
    pane_id: str
    pane_pid: int
    rollout: RolloutRef


def _proc_ppid(proc_root: Path, pid: int) -> int | None:
    try:
        stat = (proc_root / str(pid) / "stat").read_text(encoding="utf-8", errors="replace")
        return int(stat.rsplit(") ", 1)[1].split()[1])
    except (OSError, IndexError, ValueError):
        return None


def process_descendants(root_pid: int, proc_root: Path = Path("/proc")) -> set[int]:
    parents: dict[int, int] = {}
    try:
        entries = list(proc_root.iterdir())
    except OSError:
        return {root_pid}
    for entry in entries:
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        parent = _proc_ppid(proc_root, pid)
        if parent is not None:
            parents[pid] = parent
    descendants = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, parent in parents.items():
            if pid not in descendants and parent in descendants:
                descendants.add(pid)
                changed = True
    return descendants


def _is_rollout_filename(path: Path) -> bool:
    return path.name.startswith("rollout-") and path.suffix == ".jsonl" and "sessions" in path.parts


def _is_root_codex_tui_rollout(path: Path) -> bool:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            first = handle.readline(1_048_576)
        record: Any = json.loads(first)
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(record, dict) or record.get("type") != "session_meta":
        return False
    payload = record.get("payload")
    if not isinstance(payload, dict) or payload.get("originator") != "codex-tui":
        return False
    source = payload.get("source")
    if isinstance(source, dict) and "subagent" in source:
        return False
    if source == "subagent":
        return False
    agent_path = payload.get("agent_path")
    return agent_path in (None, "", "/root")


def open_rollouts_for_process_tree(root_pid: int, proc_root: Path = Path("/proc")) -> list[Path]:
    candidates: set[Path] = set()
    for pid in process_descendants(root_pid, proc_root):
        fd_dir = proc_root / str(pid) / "fd"
        try:
            fds = list(fd_dir.iterdir())
        except OSError:
            continue
        for fd in fds:
            try:
                target = Path(os.readlink(fd))
            except OSError:
                continue
            if _is_rollout_filename(target) and _is_root_codex_tui_rollout(target):
                candidates.add(target)
    return sorted(
        candidates,
        key=lambda path: path.stat().st_mtime_ns if path.exists() else 0,
        reverse=True,
    )


async def resolve_rollout(tmux: TmuxClient, session_name: str) -> RolloutRef | None:
    binding = await resolve_binding(tmux, session_name)
    return binding.rollout if binding is not None else None


def find_rollout_binding(
    panes: list[TmuxPane], proc_root: Path = Path("/proc")
) -> RolloutBinding | None:
    candidates: list[tuple[int, TmuxPane, Path]] = []
    for pane in panes:
        for path in open_rollouts_for_process_tree(pane.pane_pid, proc_root):
            try:
                modified = path.stat().st_mtime_ns
            except OSError:
                continue
            candidates.append((modified, pane, path))
    if not candidates:
        return None
    _, pane, path = max(candidates, key=lambda item: item[0])
    try:
        return RolloutBinding(
            pane_id=pane.pane_id,
            pane_pid=pane.pane_pid,
            rollout=RolloutRef.from_path(path),
        )
    except OSError:
        return None


async def resolve_binding(tmux: TmuxClient, session_name: str) -> RolloutBinding | None:
    panes = await tmux.list_panes(session_name)
    return await asyncio.to_thread(find_rollout_binding, panes)

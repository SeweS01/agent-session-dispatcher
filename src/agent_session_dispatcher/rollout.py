"""Bind an exact tmux pane process tree to its open Codex rollout."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from agent_session_dispatcher.domain import RolloutRef
from agent_session_dispatcher.tmux import TmuxClient


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
    pid = await tmux.pane_pid(session_name)
    paths = await asyncio.to_thread(open_rollouts_for_process_tree, pid)
    if not paths:
        return None
    try:
        return RolloutRef.from_path(paths[0])
    except OSError:
        return None

from __future__ import annotations

import json
import os
from pathlib import Path

from agent_session_dispatcher.rollout import (
    find_rollout_binding,
    open_rollouts_for_process_tree,
    process_descendants,
)
from agent_session_dispatcher.tmux import TmuxPane


def _fake_process(proc: Path, pid: int, ppid: int) -> Path:
    root = proc / str(pid)
    (root / "fd").mkdir(parents=True)
    (root / "stat").write_text(f"{pid} (codex worker) S {ppid} 0 0 0\n", encoding="utf-8")
    return root


def _rollout(path: Path, *, subagent: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    source: object = {"subagent": "worker"} if subagent else "cli"
    path.write_text(
        json.dumps(
            {
                "type": "session_meta",
                "payload": {
                    "originator": "codex-tui",
                    "source": source,
                    "agent_path": "/root" if not subagent else "/root/child",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )


def test_exact_process_tree_selects_newest_root_rollout(tmp_path: Path) -> None:
    proc = tmp_path / "proc"
    root = _fake_process(proc, 100, 1)
    child = _fake_process(proc, 101, 100)
    _fake_process(proc, 999, 1)
    _fake_process(proc, 888, 1)

    sessions = tmp_path / ".codex" / "sessions" / "2026" / "09" / "25"
    old = sessions / "rollout-old.jsonl"
    new = sessions / "rollout-new.jsonl"
    foreign = sessions / "rollout-foreign.jsonl"
    subagent = sessions / "rollout-subagent.jsonl"
    for path in (old, new, foreign):
        _rollout(path)
    _rollout(subagent, subagent=True)
    os.utime(old, ns=(1, 1))
    os.utime(new, ns=(2, 2))
    os.symlink(old, root / "fd" / "3")
    os.symlink(new, child / "fd" / "4")
    os.symlink(subagent, child / "fd" / "5")
    os.symlink(foreign, proc / "999" / "fd" / "6")

    assert process_descendants(100, proc) == {100, 101}
    assert open_rollouts_for_process_tree(100, proc) == [new, old]
    binding = find_rollout_binding([TmuxPane("%8", 888), TmuxPane("%1", 100)], proc)
    assert binding is not None
    assert binding.pane_id == "%1"
    assert binding.pane_pid == 100
    assert binding.rollout.path == new


def test_does_not_fallback_to_unrelated_rollout(tmp_path: Path) -> None:
    proc = tmp_path / "proc"
    _fake_process(proc, 100, 1)
    unrelated = tmp_path / ".codex" / "sessions" / "rollout-unrelated.jsonl"
    _rollout(unrelated)
    assert open_rollouts_for_process_tree(100, proc) == []

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_session_dispatcher.domain import RolloutRef
from agent_session_dispatcher.follower import SessionFollower
from agent_session_dispatcher.providers.codex import OutputDeduplicator
from agent_session_dispatcher.store import StateStore


@pytest.mark.asyncio
async def test_first_attach_starts_at_eof_then_resumes_without_duplicates(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "dispatcher.sqlite3")
    name = "Client_Project_AmoCRM"
    store.add_session(name)
    store.select_session(name)
    rollout_path = tmp_path / "sessions" / "rollout-test.jsonl"
    rollout_path.parent.mkdir()
    rollout_path.write_text(
        json.dumps({"type": "session_meta", "payload": {"originator": "codex-tui"}})
        + "\n"
        + json.dumps(
            {
                "type": "event_msg",
                "payload": {"type": "agent_message", "phase": "final_answer", "message": "old"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    rollout = RolloutRef.from_path(rollout_path)
    delivered: list[str] = []

    async def output(_session_name: str, text: str) -> None:
        delivered.append(text)

    follower = SessionFollower(store, object(), output)  # type: ignore[arg-type]
    offset = follower._initial_offset(name, rollout)
    assert offset == rollout_path.stat().st_size
    assert delivered == []

    with rollout_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"type": "event_msg", "payload": {"type": "turn_started"}}) + "\n")
        handle.write(
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "phase": "final_answer",
                        "id": "answer-1",
                        "content": [{"type": "output_text", "text": "new"}],
                    },
                }
            )
            + "\n"
        )
        handle.write(
            json.dumps(
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "item_completed",
                        "item": {
                            "type": "AgentMessage",
                            "phase": "final_answer",
                            "id": "answer-1",
                            "content": [{"type": "Text", "text": "new"}],
                        },
                    },
                }
            )
            + "\n"
        )

    offset = await follower._read_available(name, rollout, offset, OutputDeduplicator())
    assert delivered == ["new"]
    assert offset == rollout_path.stat().st_size

    restarted = SessionFollower(store, object(), output)  # type: ignore[arg-type]
    assert restarted._initial_offset(name, rollout) == offset
    assert await restarted._read_available(name, rollout, offset, OutputDeduplicator()) == offset
    assert delivered == ["new"]
    store.close()

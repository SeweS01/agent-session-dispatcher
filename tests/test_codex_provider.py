from __future__ import annotations

import json
from pathlib import Path

from agent_session_dispatcher.domain import AssistantOutput, AssistantPhase
from agent_session_dispatcher.providers.codex import (
    OutputDeduplicator,
    extract_assistant_output,
    format_image_prompt,
    parse_line,
)


def test_accepts_only_qualified_assistant_output() -> None:
    commentary = {
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "assistant",
            "phase": "commentary",
            "id": "msg-1",
            "content": [{"type": "output_text", "text": "Работаю"}],
        },
    }
    final = {
        "type": "event_msg",
        "payload": {
            "type": "item_completed",
            "item": {
                "type": "AgentMessage",
                "phase": "final_answer",
                "id": "msg-2",
                "content": [{"type": "Text", "text": "Готово"}],
            },
        },
    }
    assert extract_assistant_output(commentary) == AssistantOutput(
        "Работаю", AssistantPhase.COMMENTARY, "msg-1"
    )
    assert extract_assistant_output(final) == AssistantOutput(
        "Готово", AssistantPhase.FINAL, "msg-2"
    )


def test_rejects_reasoning_tools_user_and_unknown_phase() -> None:
    records = [
        {"type": "response_item", "payload": {"type": "reasoning", "text": "secret"}},
        {"type": "response_item", "payload": {"type": "function_call", "name": "exec"}},
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "echo"}],
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "phase": "analysis",
                "content": [{"type": "output_text", "text": "internal"}],
            },
        },
        {"type": "mystery", "payload": {"message": "unknown"}},
    ]
    assert all(extract_assistant_output(record) is None for record in records)


def test_parse_lifecycle_and_deduplicates_paired_records() -> None:
    assert parse_line(
        json.dumps({"type": "event_msg", "payload": {"type": "turn_started"}})
    ).turn_started
    assert parse_line(
        json.dumps({"type": "event_msg", "payload": {"type": "turn_complete"}})
    ).turn_completed

    dedupe = OutputDeduplicator()
    first = AssistantOutput("same", AssistantPhase.FINAL, "id-1")
    paired = AssistantOutput("same", AssistantPhase.FINAL, None)
    assert dedupe.is_duplicate(first) is False
    assert dedupe.is_duplicate(paired) is True
    dedupe.reset()
    assert dedupe.is_duplicate(paired) is False


def test_image_prompt_requires_view_image_and_preserves_caption() -> None:
    path = Path("/private/attachments/image.png")

    prompt = format_image_prompt(path, "Что означает эта ошибка?")

    assert str(path) in prompt
    assert "view_image" in prompt
    assert "Что означает эта ошибка?" in prompt

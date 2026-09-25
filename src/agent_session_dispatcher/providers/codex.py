"""Strict extraction of user-facing assistant text from Codex rollouts.

Adapted from telegram-ai-agent's MIT-licensed Codex rollout classifier. Unknown records fail
closed: they are ignored rather than guessed into a chat message.
"""

from __future__ import annotations

import hashlib
import json
from collections import deque
from dataclasses import dataclass
from typing import Any

from agent_session_dispatcher.domain import AssistantOutput, AssistantPhase


@dataclass(frozen=True)
class ParseResult:
    output: AssistantOutput | None = None
    turn_started: bool = False
    turn_completed: bool = False


def _joined_content_text(content: object, *, accepted_type: str) -> str | None:
    if not isinstance(content, list):
        return None
    parts: list[str] = []
    for part in content:
        if not isinstance(part, dict) or part.get("type") != accepted_type:
            continue
        text = part.get("text")
        if isinstance(text, str):
            parts.append(text)
    joined = "".join(parts)
    return joined if joined.strip() else None


def _phase(value: object) -> AssistantPhase | None:
    if value == "commentary":
        return AssistantPhase.COMMENTARY
    if value == "final_answer":
        return AssistantPhase.FINAL
    return None


def _identity(payload: dict[str, Any]) -> str | None:
    value = payload.get("id")
    return value if isinstance(value, str) and value else None


def extract_assistant_output(record: object) -> AssistantOutput | None:
    if not isinstance(record, dict):
        return None
    outer_type = record.get("type")
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return None

    if outer_type == "event_msg" and payload.get("type") == "agent_message":
        message = payload.get("message")
        if not isinstance(message, str) or not message.strip():
            return None
        raw_phase = payload.get("phase")
        qualified = AssistantPhase.COMMENTARY if raw_phase is None else _phase(raw_phase)
        if qualified is None:
            return None
        return AssistantOutput(text=message, phase=qualified)

    if outer_type == "event_msg" and payload.get("type") == "item_completed":
        item = payload.get("item")
        if not isinstance(item, dict) or item.get("type") != "AgentMessage":
            return None
        qualified = _phase(item.get("phase"))
        if qualified is None:
            return None
        text = _joined_content_text(item.get("content"), accepted_type="Text")
        if text is None:
            return None
        return AssistantOutput(text=text, phase=qualified, identity=_identity(item))

    if outer_type == "response_item" and payload.get("type") == "message":
        if payload.get("role") != "assistant":
            return None
        qualified = _phase(payload.get("phase"))
        if qualified is None:
            return None
        text = _joined_content_text(payload.get("content"), accepted_type="output_text")
        if text is None:
            return None
        return AssistantOutput(text=text, phase=qualified, identity=_identity(payload))

    return None


def parse_line(line: str) -> ParseResult:
    try:
        record = json.loads(line)
    except (json.JSONDecodeError, TypeError):
        return ParseResult()
    if not isinstance(record, dict):
        return ParseResult()
    payload = record.get("payload")
    if isinstance(payload, dict) and record.get("type") == "event_msg":
        event_type = payload.get("type")
        if event_type in {"task_started", "turn_started"}:
            return ParseResult(turn_started=True)
        if event_type in {"task_complete", "turn_complete"}:
            return ParseResult(turn_completed=True)
    return ParseResult(output=extract_assistant_output(record))


class OutputDeduplicator:
    """Bounded per-turn duplicate filter for Codex's paired record representations."""

    def __init__(self, limit: int = 64) -> None:
        self.limit = limit
        self._identities: set[str] = set()
        self._identity_order: deque[str] = deque()
        self._digests: set[bytes] = set()
        self._digest_order: deque[bytes] = deque()

    def reset(self) -> None:
        self._identities.clear()
        self._identity_order.clear()
        self._digests.clear()
        self._digest_order.clear()

    def is_duplicate(self, output: AssistantOutput) -> bool:
        digest = hashlib.blake2b(f"{output.phase}\0{output.text}".encode(), digest_size=16).digest()
        if output.identity and output.identity in self._identities:
            return True
        if digest in self._digests:
            return True
        if output.identity:
            self._identities.add(output.identity)
            self._identity_order.append(output.identity)
        self._digests.add(digest)
        self._digest_order.append(digest)
        while len(self._identity_order) > self.limit:
            self._identities.discard(self._identity_order.popleft())
        while len(self._digest_order) > self.limit:
            self._digests.discard(self._digest_order.popleft())
        return False

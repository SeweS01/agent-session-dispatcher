"""Send-and-verify delivery for the Codex TUI.

The pane is captured only for control verification. Its contents are never returned to Telegram
or persisted by this module.
"""

from __future__ import annotations

import asyncio
import re
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from agent_session_dispatcher.rollout import resolve_binding
from agent_session_dispatcher.tmux import TmuxClient, TmuxError

_INPUT_MARKER = re.compile(r"^\s*›\s?(?P<rest>.*)$")
_FOOTER = re.compile(r"\b(gpt-[\w.:-]+|default)\b.*\b(model|effort)\b|·\s*~?/")
_PASTED = re.compile(r"\[(?:Pasted text #\d+(?: \+\d+ lines?)?|Pasted Content \d+ chars)\]", re.I)
_EMPTY_INPUT_PLACEHOLDERS = {
    "ask codex to do anything",
}
_MODAL_MARKERS = (
    "allow command",
    "approval required",
    "do you trust the contents of this directory",
    "trust this folder?",
    "hooks need review",
    "select model and effort",
    "press enter to confirm",
    "press enter to continue",
    "press enter to select",
    "enter to confirm",
    "esc to cancel",
    "esc to dismiss",
    "no, quit",
    "enter to submit answer",
    "tab to add notes",
)


@dataclass(frozen=True)
class DeliveryResult:
    delivered: bool
    code: str
    message: str


PaneResolver = Callable[[TmuxClient, str], Awaitable[str | None]]


async def _resolve_codex_pane(tmux: TmuxClient, session_name: str) -> str | None:
    binding = await resolve_binding(tmux, session_name)
    return binding.pane_id if binding is not None else None


def modal_present(pane: str) -> bool:
    lines = pane.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    tail = "\n".join(lines[-20:]).casefold()
    return any(marker in tail for marker in _MODAL_MARKERS)


def input_bar_content(pane: str) -> str | None:
    if not pane or modal_present(pane):
        return None
    lines = pane.rstrip().splitlines()[-80:]
    for index in range(len(lines) - 1, -1, -1):
        match = _INPUT_MARKER.match(lines[index])
        if match is None:
            continue
        parts = [match.group("rest")]
        for continuation in lines[index + 1 :]:
            if not continuation.strip() or _INPUT_MARKER.match(continuation):
                break
            if _FOOTER.search(continuation):
                break
            parts.append(continuation[2:] if continuation.startswith("  ") else continuation)
        content = "\n".join(parts)
        if _normalized(content).casefold() in _EMPTY_INPUT_PLACEHOLDERS:
            return ""
        return content
    return None


def _normalized(value: str) -> str:
    return " ".join(value.split())


def _prompt_candidates(prompt: str) -> list[str]:
    normalized = _normalized(prompt)
    values = [normalized]
    if len(normalized) > 48:
        values.append(normalized[:48])
    if len(normalized) > 32:
        values.append(normalized[-32:])
    return [value for value in values if value]


def delivery_visible(before: str, after: str, prompt: str) -> bool:
    if modal_present(after):
        return False
    before_bar = input_bar_content(before) or ""
    after_bar = input_bar_content(after)
    if after_bar is None:
        return False
    before_normalized = _normalized(before_bar)
    after_normalized = _normalized(after_bar)
    after_compact = re.sub(r"-\s+", "-", after_normalized)
    for candidate in _prompt_candidates(prompt):
        if candidate in after_normalized or candidate in after_compact:
            return candidate not in before_normalized
    return len(_PASTED.findall(after_bar)) > len(_PASTED.findall(before_bar))


def prompt_still_in_bar(pane: str, prompt: str) -> bool:
    bar = input_bar_content(pane)
    if not bar:
        return False
    if _PASTED.search(bar):
        return True
    normalized = _normalized(bar)
    compact = re.sub(r"-\s+", "-", normalized)
    return any(
        candidate in normalized or candidate in compact for candidate in _prompt_candidates(prompt)
    )


class SafeInputSender:
    def __init__(
        self,
        tmux: TmuxClient,
        *,
        paste_timeout: float = 4.0,
        enter_attempts: int = 3,
        poll_interval: float = 0.15,
        pane_resolver: PaneResolver = _resolve_codex_pane,
    ) -> None:
        self.tmux = tmux
        self.paste_timeout = paste_timeout
        self.enter_attempts = enter_attempts
        self.poll_interval = poll_interval
        self.pane_resolver = pane_resolver
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def send(self, session_name: str, prompt: str) -> DeliveryResult:
        if not prompt.strip():
            return DeliveryResult(False, "empty", "Пустое сообщение не отправлено.")
        async with self._locks[session_name]:
            return await self._send_locked(session_name, prompt)

    async def _send_locked(self, session_name: str, prompt: str) -> DeliveryResult:
        try:
            if not await self.tmux.has_session(session_name):
                return DeliveryResult(False, "offline", "Выбранная tmux-сессия сейчас offline.")
            pane_id = await self.pane_resolver(self.tmux, session_name)
            if pane_id is None:
                return DeliveryResult(
                    False,
                    "codex_not_found",
                    "В выбранной tmux-сессии нет активной панели Codex. Сообщение не отправлено.",
                )
            before = await self.tmux.capture_pane(session_name, pane_id=pane_id)
            if modal_present(before):
                return DeliveryResult(
                    False,
                    "modal",
                    "В Codex открыто окно подтверждения. Закройте его в терминале и повторите.",
                )
            existing = input_bar_content(before)
            if existing is None:
                return DeliveryResult(
                    False,
                    "composer_missing",
                    "Поле ввода Codex не найдено. Сообщение не отправлено.",
                )
            if existing and existing.strip():
                return DeliveryResult(
                    False,
                    "composer_busy",
                    "В поле ввода Codex уже есть текст. Диспетчер не стал дописывать поверх него.",
                )

            await self.tmux.paste(session_name, prompt, pane_id=pane_id)
            deadline = asyncio.get_running_loop().time() + self.paste_timeout
            observed = before
            while asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(self.poll_interval)
                observed = await self.tmux.capture_pane(session_name, pane_id=pane_id)
                if modal_present(observed):
                    return DeliveryResult(
                        False,
                        "modal_after_paste",
                        "После вставки появилось окно подтверждения. Enter не отправлялся.",
                    )
                if delivery_visible(before, observed, prompt):
                    break
            else:
                return DeliveryResult(
                    False,
                    "paste_unconfirmed",
                    "Не удалось подтвердить вставку. Повторная отправка не выполнялась.",
                )

            for _ in range(self.enter_attempts):
                if modal_present(observed):
                    return DeliveryResult(
                        False,
                        "modal_before_enter",
                        "Появилось окно подтверждения. Enter не отправлялся.",
                    )
                await self.tmux.send_enter(session_name, pane_id=pane_id)
                await asyncio.sleep(self.poll_interval * 2)
                observed = await self.tmux.capture_pane(session_name, pane_id=pane_id)
                if modal_present(observed):
                    return DeliveryResult(
                        False,
                        "modal_after_enter",
                        "Codex открыл окно подтверждения; дальнейшие нажатия остановлены.",
                    )
                if not prompt_still_in_bar(observed, prompt):
                    return DeliveryResult(True, "delivered", "")
            return DeliveryResult(
                False,
                "enter_unconfirmed",
                "Сообщение осталось в поле ввода Codex. Повторная вставка не выполнялась.",
            )
        except TmuxError:
            return DeliveryResult(
                False,
                "tmux_error",
                "Tmux не подтвердил доставку сообщения. Повторная отправка не выполнялась.",
            )

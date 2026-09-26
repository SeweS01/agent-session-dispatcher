from __future__ import annotations

from collections import deque

import pytest

from agent_session_dispatcher.input_delivery import SafeInputSender


class FakeTmux:
    def __init__(self, panes: list[str], *, online: bool = True) -> None:
        self.panes = deque(panes)
        self.last_pane = panes[-1] if panes else ""
        self.online = online
        self.pastes: list[str] = []
        self.enters = 0
        self.targets: list[str | None] = []

    async def has_session(self, _name: str) -> bool:
        return self.online

    async def capture_pane(self, _name: str, *, pane_id: str | None = None) -> str:
        self.targets.append(pane_id)
        if self.panes:
            self.last_pane = self.panes.popleft()
        return self.last_pane

    async def paste(self, _name: str, text: str, *, pane_id: str | None = None) -> None:
        self.targets.append(pane_id)
        self.pastes.append(text)

    async def send_enter(self, _name: str, *, pane_id: str | None = None) -> None:
        self.targets.append(pane_id)
        self.enters += 1


async def _codex_pane(_tmux: object, _name: str) -> str:
    return "%7"


async def _no_codex_pane(_tmux: object, _name: str) -> None:
    return None


@pytest.mark.asyncio
async def test_safe_send_verifies_paste_and_enter() -> None:
    idle = "› \n\ngpt-5 default model"
    pasted = "› Проверь отчёт\n\ngpt-5 default model"
    tmux = FakeTmux([idle, pasted, idle])
    sender = SafeInputSender(  # type: ignore[arg-type]
        tmux, paste_timeout=0.05, poll_interval=0, pane_resolver=_codex_pane
    )

    result = await sender.send("client_project_agent", "Проверь отчёт")

    assert result.delivered is True
    assert tmux.pastes == ["Проверь отчёт"]
    assert tmux.enters == 1
    assert set(tmux.targets) == {"%7"}


@pytest.mark.asyncio
async def test_safe_send_treats_codex_placeholder_as_empty_input() -> None:
    idle = "› Ask Codex to do anything\n\nGPT-5.6-Sol high · ~/project"
    pasted = "› Проверь отчёт\n\nGPT-5.6-Sol high · ~/project"
    tmux = FakeTmux([idle, pasted, idle])
    sender = SafeInputSender(  # type: ignore[arg-type]
        tmux, paste_timeout=0.05, poll_interval=0, pane_resolver=_codex_pane
    )

    result = await sender.send("client_project_agent", "Проверь отчёт")

    assert result.delivered is True
    assert tmux.pastes == ["Проверь отчёт"]
    assert tmux.enters == 1


@pytest.mark.asyncio
async def test_safe_send_never_presses_enter_into_modal() -> None:
    modal = "Allow command?\n\nEsc to cancel | Enter to confirm"
    tmux = FakeTmux([modal])
    sender = SafeInputSender(tmux, pane_resolver=_codex_pane)  # type: ignore[arg-type]

    result = await sender.send("client_project_agent", "do it")

    assert result.delivered is False
    assert result.code == "modal"
    assert tmux.pastes == []
    assert tmux.enters == 0


@pytest.mark.asyncio
async def test_safe_send_stops_at_new_codex_folder_trust_modal() -> None:
    modal = "Trust this folder?\n\n1. Trust and continue\n2. Quit\n\nenter continue"
    tmux = FakeTmux([modal])
    sender = SafeInputSender(tmux, pane_resolver=_codex_pane)  # type: ignore[arg-type]

    result = await sender.send("client_project_agent", "do it")

    assert result.delivered is False
    assert result.code == "modal"
    assert tmux.pastes == []
    assert tmux.enters == 0


@pytest.mark.asyncio
async def test_safe_send_stops_at_codex_hooks_review_modal() -> None:
    modal = "Hooks need review\n1. Review hooks\n2. Trust all\n3. Continue without trusting"
    tmux = FakeTmux([modal])
    sender = SafeInputSender(tmux, pane_resolver=_codex_pane)  # type: ignore[arg-type]

    result = await sender.send("client_project_agent", "do it")

    assert result.delivered is False
    assert result.code == "modal"
    assert tmux.pastes == []
    assert tmux.enters == 0


@pytest.mark.asyncio
async def test_unconfirmed_paste_is_not_retried() -> None:
    idle = "› \n\ngpt-5 default model"
    tmux = FakeTmux([idle, idle])
    sender = SafeInputSender(  # type: ignore[arg-type]
        tmux, paste_timeout=0, poll_interval=0, pane_resolver=_codex_pane
    )

    result = await sender.send("client_project_agent", "message")

    assert result.code == "paste_unconfirmed"
    assert tmux.pastes == ["message"]
    assert tmux.enters == 0


@pytest.mark.asyncio
async def test_safe_send_rejects_session_without_codex_pane() -> None:
    tmux = FakeTmux(["devops@host:~$ "])
    sender = SafeInputSender(tmux, pane_resolver=_no_codex_pane)  # type: ignore[arg-type]

    result = await sender.send("client_project_agent", "do not paste into shell")

    assert result.code == "codex_not_found"
    assert tmux.pastes == []
    assert tmux.enters == 0


@pytest.mark.asyncio
async def test_safe_send_rejects_pane_without_codex_composer() -> None:
    tmux = FakeTmux(["devops@host:~$ "])
    sender = SafeInputSender(tmux, pane_resolver=_codex_pane)  # type: ignore[arg-type]

    result = await sender.send("client_project_agent", "do not paste into shell")

    assert result.code == "composer_missing"
    assert tmux.pastes == []
    assert tmux.enters == 0

"""Read-only installation and runtime diagnostics."""

from __future__ import annotations

import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path

from aiogram import Bot

from agent_session_dispatcher.config import Settings
from agent_session_dispatcher.rollout import resolve_rollout
from agent_session_dispatcher.store import StateStore
from agent_session_dispatcher.tmux import TmuxClient, TmuxError


@dataclass(frozen=True)
class Check:
    level: str
    name: str
    detail: str


async def run_doctor(
    settings: Settings,
    *,
    config_path: Path,
    include_telegram: bool = True,
) -> tuple[bool, list[Check]]:
    checks: list[Check] = []
    if config_path.exists():
        mode = stat.S_IMODE(config_path.stat().st_mode)
        if mode & 0o077:
            checks.append(Check("FAIL", "config", f"небезопасные права {mode:o}; нужны 600"))
        else:
            checks.append(Check("OK", "config", str(config_path)))
    else:
        checks.append(Check("FAIL", "config", f"файл не найден: {config_path}"))

    if settings.telegram_bot_token and settings.owner_user_id > 0:
        checks.append(Check("OK", "credentials", "токен задан, owner ID задан"))
    else:
        checks.append(Check("FAIL", "credentials", "не заданы токен или owner ID"))

    if shutil.which("tmux"):
        checks.append(Check("OK", "tmux", "исполняемый файл найден"))
    else:
        checks.append(Check("FAIL", "tmux", "tmux не найден в PATH"))

    parent = settings.data_dir if settings.data_dir.exists() else settings.data_dir.parent
    writable = parent.exists() and os.access(parent, os.W_OK)
    checks.append(Check("OK" if writable else "FAIL", "state", f"каталог: {settings.data_dir}"))

    tmux = TmuxClient(settings.tmux_socket_name)
    try:
        sessions = await tmux.list_sessions()
        checks.append(Check("OK", "tmux sessions", f"найдено: {len(sessions)}"))
    except TmuxError as exc:
        sessions = []
        checks.append(Check("FAIL", "tmux sessions", str(exc)))

    active: str | None = None
    if settings.database_path.exists():
        store = StateStore(settings.database_path)
        try:
            active = store.active_session()
        finally:
            store.close()
    if active is None:
        checks.append(Check("WARN", "active session", "сессия ещё не выбрана"))
    elif active not in sessions:
        checks.append(Check("WARN", "active session", f"{active}: offline"))
    else:
        try:
            rollout = await resolve_rollout(tmux, active)
        except (TmuxError, OSError):
            rollout = None
        if rollout is None:
            checks.append(Check("FAIL", "rollout", "точный Codex JSONL не найден"))
        else:
            checks.append(Check("OK", "rollout", "точный открытый Codex JSONL найден"))

    if include_telegram and settings.telegram_bot_token:
        bot = Bot(token=settings.telegram_bot_token)
        try:
            me = await bot.get_me()
            checks.append(Check("OK", "telegram", f"@{me.username or me.id}"))
        except Exception as exc:  # Telegram client exceptions vary by transport/version.
            checks.append(Check("FAIL", "telegram", type(exc).__name__))
        finally:
            await bot.session.close()

    return not any(check.level == "FAIL" for check in checks), checks

"""Command-line entry point."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from pydantic import ValidationError

from agent_session_dispatcher.config import default_config_path, load_settings
from agent_session_dispatcher.doctor import run_doctor
from agent_session_dispatcher.process_lock import AlreadyRunningError, ProcessLock
from agent_session_dispatcher.setup_cli import run_setup
from agent_session_dispatcher.store import StateStore
from agent_session_dispatcher.telegram_app import TelegramApplication
from agent_session_dispatcher.tmux import TmuxClient


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-session-dispatcher")
    parser.add_argument("--config", type=Path, default=default_config_path())
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING"])
    subcommands = parser.add_subparsers(dest="command")
    subcommands.add_parser("run", help="run the Telegram dispatcher")
    doctor = subcommands.add_parser("doctor", help="run read-only diagnostics")
    doctor.add_argument("--no-telegram", action="store_true")
    setup = subcommands.add_parser("setup", help="configure the bot and optional user service")
    setup.add_argument("--no-service", action="store_true")
    return parser


async def _run_bot(config_path: Path) -> int:
    settings = load_settings(config_path)
    settings.require_bot_credentials()
    store = StateStore(settings.database_path)
    tmux = TmuxClient(settings.tmux_socket_name)
    application = TelegramApplication(settings, store, tmux)
    try:
        with ProcessLock(settings.data_dir / "dispatcher.lock"):
            await application.run()
    finally:
        store.close()
    return 0


async def _doctor(config_path: Path, *, include_telegram: bool) -> int:
    settings = load_settings(config_path)
    healthy, checks = await run_doctor(
        settings, config_path=config_path, include_telegram=include_telegram
    )
    for check in checks:
        print(f"[{check.level}] {check.name}: {check.detail}")
    return 0 if healthy else 1


def main() -> None:
    args = _parser().parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    command = args.command or "run"
    try:
        if command == "setup":
            code = run_setup(args.config, install_service=False if args.no_service else None)
        elif command == "doctor":
            code = asyncio.run(_doctor(args.config, include_telegram=not args.no_telegram))
        else:
            code = asyncio.run(_run_bot(args.config))
    except (ValueError, ValidationError, AlreadyRunningError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        code = 2
    raise SystemExit(code)


if __name__ == "__main__":
    main()

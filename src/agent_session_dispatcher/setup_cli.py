"""Interactive local setup. Secrets are written only to the operator's config directory."""

from __future__ import annotations

import getpass
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from agent_session_dispatcher.config import default_config_path, default_data_dir

_TOKEN_RE = re.compile(r"^[0-9]+:[A-Za-z0-9_-]{20,}$")


def _write_private(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
    finally:
        os.chmod(path, 0o600)


def _service_text(executable: Path, config_path: Path) -> str:
    escaped_exec = str(executable).replace("%", "%%")
    escaped_config = str(config_path).replace("%", "%%")
    return f"""[Unit]
Description=Agent Session Dispatcher Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={escaped_exec} --config {escaped_config} run
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=default.target
"""


def run_setup(config_path: Path | None = None, *, install_service: bool | None = None) -> int:
    path = config_path or default_config_path()
    print("Настройка Agent Session Dispatcher")
    token = getpass.getpass("Токен нового бота от BotFather: ").strip()
    if not _TOKEN_RE.fullmatch(token):
        print("Ошибка: токен имеет неожиданный формат.", file=sys.stderr)
        return 2
    raw_owner = input("Ваш Telegram user ID: ").strip()
    if not raw_owner.isdigit() or int(raw_owner) <= 0:
        print("Ошибка: user ID должен быть положительным числом.", file=sys.stderr)
        return 2
    data_dir = default_data_dir()
    content = (
        f'DISPATCHER_TELEGRAM_BOT_TOKEN="{token}"\n'
        f"DISPATCHER_OWNER_USER_ID={int(raw_owner)}\n"
        f'DISPATCHER_DATA_DIR="{data_dir}"\n'
        "DISPATCHER_POLL_INTERVAL_SECONDS=0.25\n"
        "DISPATCHER_TMUX_SOCKET_NAME=\n"
    )
    _write_private(path, content)
    print(f"Конфигурация сохранена: {path} (mode 0600)")

    if install_service is None:
        answer = input("Установить и запустить пользовательскую systemd-службу? [y/N]: ")
        install_service = answer.strip().casefold() in {"y", "yes", "д", "да"}
    if not install_service:
        print(f"Проверка: agent-session-dispatcher --config {path} doctor")
        return 0

    executable_raw = shutil.which("agent-session-dispatcher")
    if executable_raw is None:
        print(
            "Ошибка: agent-session-dispatcher не найден в PATH. Сначала установите пакет.",
            file=sys.stderr,
        )
        return 2
    unit = Path.home() / ".config" / "systemd" / "user" / "agent-session-dispatcher.service"
    _write_private(unit, _service_text(Path(executable_raw).resolve(), path.resolve()))
    for args in (
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", "--now", "agent-session-dispatcher.service"],
    ):
        result = subprocess.run(args, check=False)
        if result.returncode != 0:
            print(f"Ошибка выполнения: {' '.join(args)}", file=sys.stderr)
            return result.returncode
    print("Служба установлена и запущена.")
    return 0

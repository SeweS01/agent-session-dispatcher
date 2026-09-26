"""Runtime configuration with secrets kept outside the repository."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def default_config_path() -> Path:
    root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "agent-session-dispatcher" / "dispatcher.env"


def default_data_dir() -> Path:
    root = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return root / "agent-session-dispatcher"


class Settings(BaseSettings):
    """Validated process configuration.

    ``_env_file`` is supplied by the CLI. The class deliberately has no repository-local
    dotenv fallback so an accidental ``.env`` can never become production configuration.
    """

    model_config = SettingsConfigDict(
        env_prefix="DISPATCHER_",
        extra="ignore",
        case_sensitive=False,
    )

    telegram_bot_token: str = ""
    owner_user_id: int = 0
    data_dir: Path = Field(default_factory=default_data_dir)
    poll_interval_seconds: float = 0.25
    tmux_socket_name: str | None = None

    @field_validator("data_dir", mode="before")
    @classmethod
    def expand_data_dir(cls, value: object) -> object:
        if isinstance(value, str):
            return Path(value).expanduser()
        return value

    @field_validator("poll_interval_seconds")
    @classmethod
    def validate_poll_interval(cls, value: float) -> float:
        if not 0.05 <= value <= 10:
            raise ValueError("poll interval must be between 0.05 and 10 seconds")
        return value

    @field_validator("tmux_socket_name", mode="before")
    @classmethod
    def normalize_socket(cls, value: object) -> object:
        if value == "":
            return None
        return value

    @property
    def database_path(self) -> Path:
        return self.data_dir / "dispatcher.sqlite3"

    @property
    def attachments_dir(self) -> Path:
        return self.data_dir / "attachments"

    def require_bot_credentials(self) -> None:
        if not self.telegram_bot_token:
            raise ValueError("DISPATCHER_TELEGRAM_BOT_TOKEN is required")
        if self.owner_user_id <= 0:
            raise ValueError("DISPATCHER_OWNER_USER_ID must be a positive Telegram user ID")


def load_settings(config_path: Path | None = None) -> Settings:
    path = config_path or default_config_path()
    return Settings(  # type: ignore[call-arg]
        _env_file=path if path.exists() else None,
        _env_file_encoding="utf-8",
    )

"""Provider-neutral domain types."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class AssistantPhase(StrEnum):
    COMMENTARY = "commentary"
    FINAL = "final_answer"


@dataclass(frozen=True)
class AssistantOutput:
    text: str
    phase: AssistantPhase
    identity: str | None = None


@dataclass(frozen=True)
class SavedSession:
    id: int
    name: str
    added_at: int
    last_selected_at: int | None
    display_name: str | None = None

    @property
    def label(self) -> str:
        return self.display_name or self.name


@dataclass(frozen=True)
class RolloutRef:
    path: Path
    device: int
    inode: int

    @classmethod
    def from_path(cls, path: Path) -> RolloutRef:
        stat = path.stat()
        return cls(path=path, device=stat.st_dev, inode=stat.st_ino)


@dataclass(frozen=True)
class Cursor:
    session_name: str
    rollout: RolloutRef
    offset: int


class FollowState(StrEnum):
    DISCONNECTED = "disconnected"
    OFFLINE = "offline"
    WAITING_FOR_CODEX = "waiting_for_codex"
    WATCHING = "watching"
    ERROR = "error"


@dataclass(frozen=True)
class FollowStatus:
    session_name: str | None
    state: FollowState
    detail: str = ""

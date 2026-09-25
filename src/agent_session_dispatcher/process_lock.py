"""Kernel-backed single-instance guard."""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
from typing import TextIO


class AlreadyRunningError(RuntimeError):
    pass


class ProcessLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle: TextIO | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        handle = self.path.open("a+", encoding="utf-8")
        os.chmod(self.path, 0o600)
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            raise AlreadyRunningError("another dispatcher process is already running") from None
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        self._handle = handle

    def release(self) -> None:
        if self._handle is None:
            return
        fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        self._handle.close()
        self._handle = None

    def __enter__(self) -> ProcessLock:
        self.acquire()
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()

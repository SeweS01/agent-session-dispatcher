"""Argument-safe asynchronous tmux transport.

No command in this module is evaluated by a shell. Saved names are resolved through exact tmux
targets (``=name:``), so a substring can never select a different session.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass


class TmuxError(RuntimeError):
    pass


@dataclass(frozen=True)
class TmuxResult:
    stdout: bytes
    stderr: bytes
    returncode: int


class TmuxClient:
    def __init__(self, socket_name: str | None = None, *, timeout: float = 10.0) -> None:
        self.socket_name = socket_name
        self.timeout = timeout

    def _command(self, *args: str) -> list[str]:
        command = ["tmux"]
        if self.socket_name:
            command.extend(["-L", self.socket_name])
        command.extend(args)
        return command

    async def _run(
        self,
        *args: str,
        input_bytes: bytes | None = None,
        check: bool = True,
    ) -> TmuxResult:
        process = await asyncio.create_subprocess_exec(
            *self._command(*args),
            stdin=asyncio.subprocess.PIPE
            if input_bytes is not None
            else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(input=input_bytes), timeout=self.timeout
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            raise TmuxError(f"tmux {' '.join(args[:2])} timed out") from None
        result = TmuxResult(stdout=stdout, stderr=stderr, returncode=process.returncode or 0)
        if check and result.returncode != 0:
            detail = (stderr or stdout).decode("utf-8", errors="replace").strip()
            raise TmuxError(f"tmux {args[0] if args else ''} failed: {detail}")
        return result

    @staticmethod
    def target(session_name: str) -> str:
        return f"={session_name}:"

    async def list_sessions(self) -> list[str]:
        result = await self._run("list-sessions", "-F", "#{session_name}", check=False)
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace").lower()
            if "no server running" in stderr or "failed to connect" in stderr:
                return []
            raise TmuxError(stderr.strip() or "tmux list-sessions failed")
        names = result.stdout.decode("utf-8", errors="replace").splitlines()
        return sorted({name for name in names if name})

    async def has_session(self, session_name: str) -> bool:
        result = await self._run("has-session", "-t", self.target(session_name), check=False)
        return result.returncode == 0

    async def pane_pid(self, session_name: str) -> int:
        result = await self._run(
            "display-message",
            "-p",
            "-t",
            self.target(session_name),
            "#{pane_pid}",
        )
        raw = result.stdout.decode("ascii", errors="ignore").strip()
        if not raw.isdigit():
            raise TmuxError(f"tmux returned an invalid pane pid for {session_name!r}")
        return int(raw)

    async def capture_pane(self, session_name: str, *, lines: int = 100) -> str:
        result = await self._run(
            "capture-pane",
            "-p",
            "-J",
            "-S",
            f"-{max(1, lines)}",
            "-t",
            self.target(session_name),
        )
        return result.stdout.decode("utf-8", errors="replace")

    async def paste(self, session_name: str, text: str) -> None:
        buffer_name = f"dispatcher-{uuid.uuid4().hex}"
        sanitized = text.replace("\x1b[201~", "")
        loaded = False
        try:
            await self._run(
                "load-buffer",
                "-b",
                buffer_name,
                "-",
                input_bytes=sanitized.encode("utf-8"),
            )
            loaded = True
            await self._run(
                "paste-buffer",
                "-p",
                "-b",
                buffer_name,
                "-t",
                self.target(session_name),
            )
        finally:
            if loaded:
                await self._run("delete-buffer", "-b", buffer_name, check=False)

    async def send_enter(self, session_name: str) -> None:
        await self._run("send-keys", "-t", self.target(session_name), "Enter")

"""Private, bounded storage for Telegram image attachments."""

from __future__ import annotations

import asyncio
import os
import stat
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

MAX_IMAGE_BYTES = 20 * 1024 * 1024
IMAGE_RETENTION_SECONDS = 24 * 60 * 60


class AttachmentError(RuntimeError):
    pass


class AttachmentTooLargeError(AttachmentError):
    pass


class UnsupportedImageError(AttachmentError):
    pass


class TelegramDownloader(Protocol):
    async def download(self, file: str, destination: Path) -> object: ...


@dataclass(frozen=True)
class StoredImage:
    path: Path
    media_type: str
    size: int


def _image_kind(header: bytes) -> tuple[str, str] | None:
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png", "image/png"
    if header.startswith(b"\xff\xd8\xff"):
        return ".jpg", "image/jpeg"
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return ".webp", "image/webp"
    return None


class AttachmentStore:
    def __init__(
        self,
        directory: Path,
        *,
        max_bytes: int = MAX_IMAGE_BYTES,
        retention_seconds: int = IMAGE_RETENTION_SECONDS,
    ) -> None:
        self.directory = directory.resolve()
        self.max_bytes = max_bytes
        self.retention_seconds = retention_seconds
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)

    async def download_image(
        self,
        downloader: TelegramDownloader,
        file_id: str,
        declared_size: int | None,
    ) -> StoredImage:
        if declared_size is not None and declared_size > self.max_bytes:
            raise AttachmentTooLargeError("declared image size exceeds the limit")

        await asyncio.to_thread(self.cleanup_expired)
        token = uuid.uuid4().hex
        partial = self.directory / f".{token}.part"
        try:
            await downloader.download(file_id, destination=partial)
            info = partial.lstat()
            if not stat.S_ISREG(info.st_mode):
                raise UnsupportedImageError("downloaded attachment is not a regular file")
            if info.st_size == 0:
                raise UnsupportedImageError("downloaded image is empty")
            if info.st_size > self.max_bytes:
                raise AttachmentTooLargeError("downloaded image exceeds the limit")
            with partial.open("rb") as handle:
                kind = _image_kind(handle.read(16))
            if kind is None:
                raise UnsupportedImageError("unsupported image signature")
            extension, media_type = kind
            final_path = self.directory / f"{token}{extension}"
            os.chmod(partial, 0o600)
            os.replace(partial, final_path)
            os.chmod(final_path, 0o600)
            return StoredImage(path=final_path, media_type=media_type, size=info.st_size)
        except AttachmentError:
            partial.unlink(missing_ok=True)
            raise
        except Exception as exc:
            partial.unlink(missing_ok=True)
            raise AttachmentError("failed to download Telegram image") from exc

    def cleanup_expired(self, *, now: float | None = None) -> int:
        threshold = (time.time() if now is None else now) - self.retention_seconds
        removed = 0
        try:
            entries = list(self.directory.iterdir())
        except OSError:
            return 0
        for entry in entries:
            try:
                info = entry.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_mtime >= threshold:
                    continue
                entry.unlink()
                removed += 1
            except OSError:
                continue
        return removed

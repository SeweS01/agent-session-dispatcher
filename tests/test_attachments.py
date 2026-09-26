from __future__ import annotations

import os
import stat
import time
from pathlib import Path

import pytest

from agent_session_dispatcher.attachments import (
    AttachmentStore,
    AttachmentTooLargeError,
    UnsupportedImageError,
)


class FakeDownloader:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.calls = 0

    async def download(self, _file: str, destination: Path) -> None:
        self.calls += 1
        destination.write_bytes(self.payload)


@pytest.mark.asyncio
async def test_download_image_validates_signature_and_permissions(tmp_path: Path) -> None:
    store = AttachmentStore(tmp_path / "attachments")
    downloader = FakeDownloader(b"\x89PNG\r\n\x1a\n" + b"payload")

    image = await store.download_image(downloader, "telegram-file", None)

    assert image.media_type == "image/png"
    assert image.path.suffix == ".png"
    assert image.path.read_bytes().startswith(b"\x89PNG")
    assert stat.S_IMODE(image.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(image.path.parent.stat().st_mode) == 0o700
    assert not list(image.path.parent.glob("*.part"))


@pytest.mark.asyncio
async def test_declared_oversize_is_rejected_before_download(tmp_path: Path) -> None:
    store = AttachmentStore(tmp_path / "attachments", max_bytes=8)
    downloader = FakeDownloader(b"\xff\xd8\xffpayload")

    with pytest.raises(AttachmentTooLargeError):
        await store.download_image(downloader, "telegram-file", 9)

    assert downloader.calls == 0


@pytest.mark.asyncio
async def test_unsupported_payload_is_removed(tmp_path: Path) -> None:
    store = AttachmentStore(tmp_path / "attachments")
    downloader = FakeDownloader(b"not-an-image")

    with pytest.raises(UnsupportedImageError):
        await store.download_image(downloader, "telegram-file", None)

    assert list(store.directory.iterdir()) == []


def test_cleanup_removes_only_expired_regular_files(tmp_path: Path) -> None:
    store = AttachmentStore(tmp_path / "attachments", retention_seconds=60)
    expired = store.directory / "expired.png"
    current = store.directory / "current.png"
    directory = store.directory / "nested"
    link = store.directory / "link.png"
    expired.write_bytes(b"old")
    current.write_bytes(b"new")
    directory.mkdir()
    link.symlink_to(current)
    now = time.time()
    os.utime(expired, (now - 120, now - 120))

    assert store.cleanup_expired(now=now) == 1
    assert not expired.exists()
    assert current.exists()
    assert directory.exists()
    assert link.is_symlink()

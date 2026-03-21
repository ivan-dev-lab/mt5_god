"""Tests for Telegram capture compatibility helpers."""

from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import apscheduler.schedulers.base as scheduler_base
import pytest
import pytz
import tzlocal
from telegram.error import TimedOut

from app.adapters.capture import TelegramCaptureAdapter, patch_apscheduler_timezone_compatibility
from app.domain.models import CaptureEvent
from app.storage.files import FileStorage
from app.storage.sqlite import SQLiteRepository


@pytest.fixture
def anyio_backend() -> str:
    """Run async Telegram adapter tests on asyncio only."""

    return "asyncio"


def test_patch_apscheduler_timezone_compatibility_converts_zoneinfo(monkeypatch: pytest.MonkeyPatch) -> None:
    original = scheduler_base.get_localzone
    monkeypatch.setattr(tzlocal, "get_localzone", lambda: ZoneInfo("Asia/Yekaterinburg"))

    patch_apscheduler_timezone_compatibility()

    patched = scheduler_base.get_localzone()
    assert hasattr(patched, "localize")
    assert hasattr(patched, "normalize")
    assert getattr(patched, "zone", None) == "Asia/Yekaterinburg"

    monkeypatch.setattr(scheduler_base, "get_localzone", original)


def test_patch_apscheduler_timezone_compatibility_falls_back_to_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    original = scheduler_base.get_localzone

    class UnknownZone:
        key = "Unknown/Zone"

    monkeypatch.setattr(tzlocal, "get_localzone", lambda: UnknownZone())

    patch_apscheduler_timezone_compatibility()

    patched = scheduler_base.get_localzone()
    assert patched == pytz.utc

    monkeypatch.setattr(scheduler_base, "get_localzone", original)


@pytest.mark.anyio
async def test_fetch_file_with_retry_retries_once(app_config) -> None:
    file_storage = FileStorage(app_config.paths)
    repository = SQLiteRepository(app_config.paths.database_path)
    adapter = TelegramCaptureAdapter(app_config, file_storage, repository)

    class FakeBot:
        def __init__(self) -> None:
            self.calls = 0

        async def get_file(self, file_id: str, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise TimedOut("timeout")
            return {"file_id": file_id, "kwargs": kwargs}

    bot = FakeBot()
    result = await adapter._fetch_file_with_retry(bot, "file-123")

    assert bot.calls == 2
    assert result["file_id"] == "file-123"
    assert result["kwargs"]["read_timeout"] == app_config.telegram.read_timeout


@pytest.mark.anyio
async def test_download_file_with_retry_retries_once(app_config, tmp_path: Path) -> None:
    file_storage = FileStorage(app_config.paths)
    repository = SQLiteRepository(app_config.paths.database_path)
    adapter = TelegramCaptureAdapter(app_config, file_storage, repository)

    class FakeTelegramFile:
        def __init__(self) -> None:
            self.calls = 0

        async def download_to_drive(self, custom_path: str, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise TimedOut("timeout")
            path = Path(custom_path)
            path.write_bytes(b"image")
            return path

    download_path = tmp_path / "telegram_1.jpg"
    telegram_file = FakeTelegramFile()
    result = await adapter._download_file_with_retry(telegram_file, download_path)

    assert telegram_file.calls == 2
    assert result == download_path
    assert download_path.exists()


def test_to_capture_event_allows_duplicate_images_when_enabled(app_config, tmp_path: Path) -> None:
    config = app_config.model_copy(deep=True)
    config.telegram.allow_duplicate_images = True

    file_storage = FileStorage(config.paths)
    repository = SQLiteRepository(config.paths.database_path)
    adapter = TelegramCaptureAdapter(config, file_storage, repository)

    download_path = tmp_path / "telegram_1.jpg"
    download_path.write_bytes(b"same-image")
    existing = CaptureEvent(
        source="telegram",
        source_chat_id="-1002944501825",
        source_thread_id="548",
        source_message_id="1",
        image_path=str(download_path),
        image_sha256="placeholder",
    )
    existing.image_sha256 = adapter._to_capture_event(
        SimpleNamespace(
            chat=SimpleNamespace(id=-1002944501825),
            message_thread_id=548,
            message_id=1,
            from_user=SimpleNamespace(id=42),
            caption=None,
        ),
        download_path,
    ).image_sha256
    repository.create_task(existing)

    duplicate_event = adapter._to_capture_event(
        SimpleNamespace(
            chat=SimpleNamespace(id=-1002944501825),
            message_thread_id=548,
            message_id=2,
            from_user=SimpleNamespace(id=42),
            caption=None,
        ),
        download_path,
    )

    assert duplicate_event.image_sha256 == existing.image_sha256

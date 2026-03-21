"""Capture adapters for local files and Telegram."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Callable

from app.config import AppConfig
from app.domain.exceptions import ConfigurationError, DuplicateEventError
from app.domain.interfaces import CaptureAdapter
from app.domain.models import CaptureEvent, CaptureMetadata
from app.services.logging import get_logger
from app.storage.files import FileStorage
from app.storage.sqlite import SQLiteRepository


def sha256_file(path: Path) -> str:
    """Return the SHA-256 hash of a file."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def patch_apscheduler_timezone_compatibility() -> None:
    """Patch APScheduler timezone detection to return a `pytz` timezone on Windows."""

    try:
        import pytz
        import apscheduler.util as apscheduler_util
        import apscheduler.schedulers.base as scheduler_base
        from tzlocal import get_localzone
    except ImportError:
        return

    def to_pytz_timezone(zone):
        if zone is None:
            return None
        if hasattr(zone, "localize") and hasattr(zone, "normalize"):
            return zone
        zone_name = getattr(zone, "key", None) or getattr(zone, "zone", None) or str(zone)
        try:
            return pytz.timezone(zone_name)
        except Exception:
            return pytz.utc

    local_zone = get_localzone()
    if hasattr(local_zone, "localize") and hasattr(local_zone, "normalize"):
        return

    original_astimezone = getattr(apscheduler_util, "_mt5_god_original_astimezone", apscheduler_util.astimezone)

    def compat_astimezone(obj):
        if obj is None:
            return None
        if hasattr(obj, "localize") and hasattr(obj, "normalize"):
            return obj
        if not isinstance(obj, str):
            coerced = to_pytz_timezone(obj)
            if coerced is not None:
                return coerced
        return original_astimezone(obj)

    apscheduler_util._mt5_god_original_astimezone = original_astimezone
    apscheduler_util.astimezone = compat_astimezone
    scheduler_base.astimezone = compat_astimezone
    scheduler_base.get_localzone = lambda: to_pytz_timezone(get_localzone()) or pytz.utc


class LocalFileCaptureAdapter(CaptureAdapter):
    """Copies a local image into the inbox and builds a `CaptureEvent`."""

    def __init__(self, file_storage: FileStorage, repository: SQLiteRepository) -> None:
        self.file_storage = file_storage
        self.repository = repository

    def capture(
        self,
        source: Path,
        *,
        source_name: str = "local_file",
        metadata: CaptureMetadata | None = None,
        allow_duplicate: bool = False,
    ) -> CaptureEvent:
        """Create a `CaptureEvent` from a local image path."""

        metadata = metadata or CaptureMetadata(file_name=source.name)
        source_hash = sha256_file(source)
        if not allow_duplicate and self.repository.is_duplicate(None, source_hash):
            raise DuplicateEventError(f"duplicate image hash: {source_hash}")
        event = CaptureEvent(
            source=source_name,
            image_path="pending",
            image_sha256=source_hash,
            metadata=metadata,
        )
        target = self.file_storage.copy_to_inbox(source, event_id=event.event_id)
        self._copy_sidecar_files(source, target)
        return CaptureEvent.model_validate({**event.model_dump(mode="json"), "image_path": str(target)})

    def _copy_sidecar_files(self, source: Path, target: Path) -> None:
        """Copy OCR sidecar files next to the captured image when they exist."""

        pairs = (
            (source.with_suffix(source.suffix + ".ocr.txt"), target.with_suffix(target.suffix + ".ocr.txt")),
            (source.with_suffix(".ocr.txt"), target.with_suffix(".ocr.txt")),
            (source.with_suffix(".txt"), target.with_suffix(".txt")),
        )
        for candidate, sidecar_target in pairs:
            if candidate.exists():
                sidecar_target.write_text(candidate.read_text(encoding="utf-8"), encoding="utf-8")

    def run(self, handler) -> None:
        """Local capture adapter is single-shot and does not support a listener loop."""

        raise NotImplementedError("LocalFileCaptureAdapter is intended for explicit `capture(...)` calls")


class TelegramCaptureAdapter(CaptureAdapter):
    """Polls Telegram for photo messages from an allowed group/topic."""

    def __init__(self, config: AppConfig, file_storage: FileStorage, repository: SQLiteRepository) -> None:
        self.config = config
        self.file_storage = file_storage
        self.repository = repository
        self.logger = get_logger(__name__)

    def _request_timeouts(self) -> dict[str, float]:
        """Return Telegram request timeout kwargs."""

        return {
            "connect_timeout": self.config.telegram.connect_timeout,
            "read_timeout": self.config.telegram.read_timeout,
            "write_timeout": self.config.telegram.write_timeout,
            "pool_timeout": self.config.telegram.pool_timeout,
        }

    async def _fetch_file_with_retry(self, bot, file_id: str):
        """Fetch file metadata with retry for transient Telegram network errors."""

        from telegram.error import NetworkError, TimedOut

        attempts = max(1, self.config.retry_policy.telegram_download + 1)
        last_error = None
        for attempt in range(1, attempts + 1):
            try:
                return await bot.get_file(file_id, **self._request_timeouts())
            except (TimedOut, NetworkError) as exc:
                last_error = exc
                self.logger.warning(
                    "telegram get_file retry",
                    extra={"event_id": file_id, "status": f"attempt={attempt}/{attempts}"},
                )
                if attempt == attempts:
                    break
                await asyncio.sleep(min(2 * attempt, 5))
        raise last_error

    async def _download_file_with_retry(self, telegram_file, download_path: Path):
        """Download file contents with retry for transient Telegram network errors."""

        from telegram.error import NetworkError, TimedOut

        attempts = max(1, self.config.retry_policy.telegram_download + 1)
        last_error = None
        for attempt in range(1, attempts + 1):
            try:
                return await telegram_file.download_to_drive(custom_path=str(download_path), **self._request_timeouts())
            except (TimedOut, NetworkError) as exc:
                last_error = exc
                self.logger.warning(
                    "telegram file download retry",
                    extra={"event_id": download_path.name, "status": f"attempt={attempt}/{attempts}"},
                )
                if attempt == attempts:
                    break
                await asyncio.sleep(min(2 * attempt, 5))
        raise last_error

    async def _on_error(self, update, context) -> None:
        """Log Telegram update handler failures."""

        message_id = getattr(getattr(update, "effective_message", None), "message_id", "unknown")
        self.logger.exception("telegram update handler failed", extra={"event_id": str(message_id)}, exc_info=context.error)

    def _is_allowed(self, message) -> bool:
        """Return `True` when the message matches configured chat and thread filters."""

        chat_id = getattr(getattr(message, "chat", None), "id", None)
        thread_id = getattr(message, "message_thread_id", None)
        if self.config.telegram.allowed_chat_id is not None and chat_id != self.config.telegram.allowed_chat_id:
            self.logger.info(
                "telegram message skipped: unexpected chat",
                extra={"event_id": str(getattr(message, "message_id", "unknown"))},
            )
            return False
        if self.config.telegram.allowed_thread_id is not None and thread_id != self.config.telegram.allowed_thread_id:
            self.logger.info(
                "telegram message skipped: unexpected thread",
                extra={"event_id": str(getattr(message, "message_id", "unknown"))},
            )
            return False
        if not bool(getattr(message, "photo", None)):
            self.logger.info(
                "telegram message skipped: no photo",
                extra={"event_id": str(getattr(message, "message_id", "unknown"))},
            )
            return False
        return True

    def _to_capture_event(self, message, downloaded_path: Path) -> CaptureEvent:
        """Build a domain event from a Telegram message."""

        sha = sha256_file(downloaded_path)
        if not self.config.telegram.allow_duplicate_images and self.repository.is_duplicate(str(message.message_id), sha):
            raise DuplicateEventError(f"duplicate telegram message: {message.message_id}")
        return CaptureEvent(
            source="telegram",
            source_chat_id=str(message.chat.id),
            source_thread_id=str(message.message_thread_id) if message.message_thread_id is not None else None,
            source_message_id=str(message.message_id),
            image_path=str(downloaded_path),
            image_sha256=sha,
            metadata=CaptureMetadata(
                sender_id=str(message.from_user.id) if message.from_user else None,
                caption=message.caption,
                file_name=downloaded_path.name,
            ),
        )

    def run(self, handler: Callable[[CaptureEvent], object]) -> None:
        """Start Telegram polling and forward every accepted image to `handler`."""

        if not self.config.bot_token:
            raise ConfigurationError("TELEGRAM_BOT_TOKEN is required when telegram.enabled = true")

        patch_apscheduler_timezone_compatibility()

        try:
            from telegram.ext import ApplicationBuilder, MessageHandler, filters
        except ImportError as exc:
            raise ConfigurationError("Install `python-telegram-bot` to use Telegram ingestion") from exc

        async def on_message(update, context) -> None:
            message = update.effective_message
            if message is None or not self._is_allowed(message):
                return

            self.logger.info(
                "telegram photo accepted",
                extra={"event_id": str(message.message_id), "status": f"chat={message.chat.id};thread={message.message_thread_id}"},
            )
            telegram_file = await self._fetch_file_with_retry(context.bot, message.photo[-1].file_id)
            suffix = Path(telegram_file.file_path or "photo.jpg").suffix or ".jpg"
            download_path = self.file_storage.paths.inbox_dir / f"telegram_{message.message_id}{suffix}"
            await self._download_file_with_retry(telegram_file, download_path)

            try:
                event = self._to_capture_event(message, download_path)
                result = handler(event)
            except DuplicateEventError:
                self.logger.info("duplicate telegram image skipped", extra={"event_id": str(message.message_id)})
                return

            if self.config.telegram.send_status_updates:
                status = getattr(result, "final_status", "processed")
                await message.reply_text(f"pipeline status: {status}")

        async def runner() -> None:
            application = (
                ApplicationBuilder()
                .token(self.config.bot_token)
                .job_queue(None)
                .connect_timeout(self.config.telegram.connect_timeout)
                .read_timeout(self.config.telegram.read_timeout)
                .write_timeout(self.config.telegram.write_timeout)
                .pool_timeout(self.config.telegram.pool_timeout)
                .get_updates_connect_timeout(self.config.telegram.connect_timeout)
                .get_updates_read_timeout(self.config.telegram.read_timeout)
                .get_updates_write_timeout(self.config.telegram.write_timeout)
                .get_updates_pool_timeout(self.config.telegram.pool_timeout)
                .build()
            )
            application.add_handler(MessageHandler(filters.PHOTO, on_message))
            application.add_error_handler(self._on_error)
            self.logger.info(
                "telegram polling started",
                extra={
                    "status": "started",
                    "event_id": f"chat={self.config.telegram.allowed_chat_id};thread={self.config.telegram.allowed_thread_id}",
                },
            )
            await application.initialize()
            await application.start()
            await application.updater.start_polling()
            try:
                while True:
                    await asyncio.sleep(1)
            finally:
                await application.updater.stop()
                await application.stop()
                await application.shutdown()

        asyncio.run(runner())

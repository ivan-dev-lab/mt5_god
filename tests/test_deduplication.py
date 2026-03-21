"""Tests for duplicate image detection."""

from pathlib import Path

import pytest

from app.adapters.capture import LocalFileCaptureAdapter
from app.domain.exceptions import DuplicateEventError
from app.storage.files import FileStorage
from app.storage.sqlite import SQLiteRepository


def test_repository_detects_duplicate_hash(app_config, tmp_path: Path) -> None:
    source = tmp_path / "sample.png"
    source.write_bytes(b"fake-image")

    file_storage = FileStorage(app_config.paths)
    repository = SQLiteRepository(app_config.paths.database_path)
    adapter = LocalFileCaptureAdapter(file_storage=file_storage, repository=repository)

    event = adapter.capture(source)
    repository.create_task(event)

    with pytest.raises(DuplicateEventError):
        adapter.capture(source)

"""Filesystem-backed artifact storage."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.config import PathsConfig


class FileStorage:
    """Stores images, JSON payloads, and diagnostics on disk."""

    def __init__(self, paths: PathsConfig) -> None:
        self.paths = paths
        self.ensure_layout()

    def ensure_layout(self) -> None:
        """Create the expected directory layout."""

        for path in (
            self.paths.data_dir,
            self.paths.inbox_dir,
            self.paths.processed_dir,
            self.paths.failed_dir,
            self.paths.screenshots_dir,
            self.paths.analysis_dir,
            self.paths.execution_dir,
            self.paths.logs_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)
        self.paths.database_path.parent.mkdir(parents=True, exist_ok=True)

    def copy_to_inbox(self, source: Path, *, event_id: str) -> Path:
        """Copy the source image into the inbox directory and return the new path."""

        target = self.paths.inbox_dir / f"{event_id}{source.suffix.lower()}"
        shutil.copy2(source, target)
        return target

    def write_json(self, target: Path, payload: BaseModel | dict[str, Any]) -> Path:
        """Write a model or dictionary as UTF-8 JSON."""

        target.parent.mkdir(parents=True, exist_ok=True)
        serialized = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else payload
        target.write_text(json.dumps(serialized, ensure_ascii=False, indent=2), encoding="utf-8")
        return target

    def write_text(self, target: Path, payload: str) -> Path:
        """Write text to disk and return the path."""

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload, encoding="utf-8")
        return target

    def move_processed(self, source: Path) -> Path:
        """Move a processed image from inbox to processed."""

        target = self.paths.processed_dir / source.name
        if source.exists():
            shutil.move(str(source), target)
        return target

    def move_failed(self, source: Path) -> Path:
        """Move a failed image from inbox to failed."""

        target = self.paths.failed_dir / source.name
        if source.exists():
            shutil.move(str(source), target)
        return target

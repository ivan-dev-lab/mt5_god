"""Logging setup with text and JSON outputs."""

from __future__ import annotations

import json
import logging

from app.config import PathsConfig


class JsonFormatter(logging.Formatter):
    """Serialize log records as JSON lines."""

    def format(self, record: logging.LogRecord) -> str:
        """Render the current record to JSON."""

        payload = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "time": self.formatTime(record, self.datefmt),
        }
        for field in ("event_id", "trade_id", "status", "stage"):
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(paths: PathsConfig) -> None:
    """Configure console, text file, and JSONL file logging."""

    paths.logs_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    if root.handlers:
        return

    root.setLevel(logging.INFO)

    text_formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    json_formatter = JsonFormatter()

    console = logging.StreamHandler()
    console.setFormatter(text_formatter)

    text_file = logging.FileHandler(paths.logs_dir / "app.log", encoding="utf-8")
    text_file.setFormatter(text_formatter)

    json_file = logging.FileHandler(paths.logs_dir / "app.jsonl", encoding="utf-8")
    json_file.setFormatter(json_formatter)

    root.addHandler(console)
    root.addHandler(text_file)
    root.addHandler(json_file)


def get_logger(name: str) -> logging.Logger:
    """Return a module logger."""

    return logging.getLogger(name)

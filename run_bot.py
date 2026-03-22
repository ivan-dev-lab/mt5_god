"""Convenience launcher for the Telegram bot."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    """Launch `python -m app run-bot` from the project root."""

    project_root = Path(__file__).resolve().parent
    arguments = list(sys.argv[1:] if argv is None else argv)
    command = [sys.executable, "-m", "app", "run-bot", *arguments]
    return subprocess.call(command, cwd=project_root)


if __name__ == "__main__":
    raise SystemExit(main())

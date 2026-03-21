"""Tests for configuration loading and Telegram overrides."""

from pathlib import Path

import pytest

from app.config import AppConfig, load_config
from app.domain.exceptions import ConfigurationError


def test_env_overrides_telegram_chat_and_thread(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "app.yaml").write_text(
        "\n".join(
            [
                "mode: dry_run",
                "telegram:",
                "  enabled: false",
                "  allowed_chat_id:",
                "  allowed_thread_id:",
                "execution:",
                "  backend: dry_run",
            ]
        ),
        encoding="utf-8",
    )
    (config_dir / "accounts.yaml").write_text("accounts: []\n", encoding="utf-8")
    (config_dir / "analysis_profiles.yaml").write_text("analysis_profiles: {}\n", encoding="utf-8")
    (config_dir / "ui_profiles.yaml").write_text("ui_profiles: {}\n", encoding="utf-8")

    monkeypatch.setenv("TELEGRAM_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", "-1001234567890")
    monkeypatch.setenv("TELEGRAM_ALLOWED_THREAD_ID", "777")
    monkeypatch.setenv("TELEGRAM_ALLOW_DUPLICATE_IMAGES", "true")

    config = load_config(config_dir=config_dir, env_file=None)

    assert config.telegram.enabled is True
    assert config.telegram.allowed_chat_id == -1001234567890
    assert config.telegram.allowed_thread_id == 777
    assert config.telegram.allow_duplicate_images is True


def test_enabled_telegram_requires_chat_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "app.yaml").write_text(
        "\n".join(
            [
                "mode: dry_run",
                "telegram:",
                "  enabled: true",
                "execution:",
                "  backend: dry_run",
            ]
        ),
        encoding="utf-8",
    )
    (config_dir / "accounts.yaml").write_text("accounts: []\n", encoding="utf-8")
    (config_dir / "analysis_profiles.yaml").write_text("analysis_profiles: {}\n", encoding="utf-8")
    (config_dir / "ui_profiles.yaml").write_text("ui_profiles: {}\n", encoding="utf-8")

    monkeypatch.delenv("TELEGRAM_ALLOWED_CHAT_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_ENABLED", raising=False)

    with pytest.raises(ConfigurationError):
        load_config(config_dir=config_dir, env_file=None)


def test_positive_telegram_chat_id_is_normalized_to_supergroup_format(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "app.yaml").write_text(
        "\n".join(
            [
                "mode: dry_run",
                "telegram:",
                "  enabled: true",
                "  allowed_chat_id: 2944501825",
                "  allowed_thread_id: 548",
                "execution:",
                "  backend: dry_run",
            ]
        ),
        encoding="utf-8",
    )
    (config_dir / "accounts.yaml").write_text("accounts: []\n", encoding="utf-8")
    (config_dir / "analysis_profiles.yaml").write_text("analysis_profiles: {}\n", encoding="utf-8")
    (config_dir / "ui_profiles.yaml").write_text("ui_profiles: {}\n", encoding="utf-8")

    config = load_config(config_dir=config_dir, env_file=None)

    assert config.telegram.allowed_chat_id == -1002944501825
    assert config.telegram.allowed_thread_id == 548


def test_tesseract_cmd_auto_detects_common_windows_install(monkeypatch: pytest.MonkeyPatch) -> None:
    target = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")

    monkeypatch.delenv("TESSERACT_CMD", raising=False)
    monkeypatch.setattr("app.config.shutil.which", lambda name: None)

    original_exists = Path.exists

    def fake_exists(self: Path) -> bool:
        if self == target:
            return True
        return original_exists(self)

    monkeypatch.setattr(Path, "exists", fake_exists)

    config = AppConfig()

    assert config.tesseract_cmd == str(target)

"""Application configuration models and loader."""

from __future__ import annotations

import re
import os
import shutil
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.exceptions import ConfigurationError
from app.domain.models import RuntimeMode


class ConfigModel(BaseModel):
    """Base class for config models."""

    model_config = ConfigDict(extra="forbid")


class PathsConfig(ConfigModel):
    """Filesystem layout."""

    data_dir: Path = Path("data")
    inbox_dir: Path = Path("data/inbox")
    processed_dir: Path = Path("data/processed")
    failed_dir: Path = Path("data/failed")
    screenshots_dir: Path = Path("data/screenshots")
    analysis_dir: Path = Path("data/analysis")
    execution_dir: Path = Path("data/execution")
    logs_dir: Path = Path("logs")
    database_path: Path = Path("data/mt5_god.sqlite3")


class RetryPolicy(ConfigModel):
    """Retries at each stage."""

    telegram_download: int = 2
    ocr: int = 2
    ui_lookup: int = 2
    execution_per_account: int = 2


class TelegramSettings(ConfigModel):
    """Telegram ingestion settings."""

    enabled: bool = False
    allowed_chat_id: int | None = None
    allowed_thread_id: int | None = None
    download_dir: Path = Path("data/inbox")
    allowed_mime_types: list[str] = Field(default_factory=lambda: ["image/jpeg", "image/png", "image/webp"])
    send_status_updates: bool = False
    allow_duplicate_images: bool = False
    connect_timeout: float = 20.0
    read_timeout: float = 60.0
    write_timeout: float = 60.0
    pool_timeout: float = 20.0

    @staticmethod
    def normalize_chat_id(value: int | None) -> int | None:
        """Convert `t.me/c/<id>` style identifiers to Bot API supergroup chat ids."""

        if value is None:
            return None
        if value < 0:
            return value
        return int(f"-100{value}")


class AnalysisThresholds(ConfigModel):
    """Confidence thresholds for an analyzer profile."""

    overall: float = 0.85
    symbol: float = 0.90
    direction: float = 0.90
    entry_price: float = 0.70
    stop_loss: float = 0.80
    take_profit: float = 0.80
    lot: float = 0.90
    risk_percent: float = 0.50


class AnalysisProfile(ConfigModel):
    """Configurable OCR/vision profile."""

    roi_templates: dict[str, Any] = Field(default_factory=dict)
    ocr_engine: str = "sidecar_or_tesseract"
    required_fields: list[str] = Field(default_factory=lambda: ["symbol", "direction", "lot", "stop_loss", "take_profit"])
    confidence_thresholds: AnalysisThresholds = Field(default_factory=AnalysisThresholds)


class UiAnchor(ConfigModel):
    """Template-based UI anchor configuration."""

    template: str | None = None
    fallback_region: list[int] | None = None
    text_hint: str | None = None


class UiProfile(ConfigModel):
    """UI profile for a specific MT5 build/theme/language."""

    language: str = "ru"
    templates_dir: Path = Path("assets/templates/mt5")
    confidence_threshold: float = 0.88
    symbol_search_mode: str = "template_only"
    symbol_search_anchor: str = "symbols_panel"
    ocr_language: str = "eng"
    ocr_config: str = "--psm 6"
    top_menu_region: list[int] = Field(default_factory=lambda: [0, 0, 240, 42])
    top_menu_ocr_languages: list[str] = Field(default_factory=lambda: ["eng", "rus+eng", "rus"])
    top_menu_labels: list[str] = Field(default_factory=lambda: ["File"])
    connect_account_menu_labels: list[str] = Field(default_factory=lambda: ["Login to Trade Account"])
    connect_account_menu_ocr_languages: list[str] = Field(default_factory=lambda: ["eng", "rus+eng", "rus"])
    account_login_field_ratio: list[float] = Field(default_factory=lambda: [0.36, 0.31, 0.34, 0.11])
    account_dropdown_region_ratio: list[float] = Field(default_factory=lambda: [0.36, 0.31, 0.36, 0.56])
    account_dropdown_region_from_login: list[int] = Field(default_factory=lambda: [0, 18, 260, 180])
    anchors: dict[str, UiAnchor] = Field(default_factory=dict)

    @field_validator("top_menu_ocr_languages", "top_menu_labels", "connect_account_menu_labels", "connect_account_menu_ocr_languages", mode="before")
    @classmethod
    def normalize_list_field(cls, value: Any) -> list[str]:
        """Accept a YAML list or comma-separated string for OCR and label config fields."""

        if value is None:
            return []
        if isinstance(value, str):
            return [part.strip() for part in re.split(r"[;,]", value) if part.strip()]
        return list(value)


class AccountConfig(ConfigModel):
    """Configured MT5 account target."""

    account_alias: str
    account_login: str | None = None
    password: str | None = None
    enabled: bool = True

    @field_validator("account_login", "password")
    @classmethod
    def normalize_optional_string(cls, value: str | None) -> str | None:
        """Treat empty account login/password values as missing."""

        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class ExecutionSettings(ConfigModel):
    """Executor behavior settings."""

    backend: str = "dry_run"
    mt5_terminal_path: Path | None = None
    require_ui_confirmation: bool = True


class AppConfig(ConfigModel):
    """Root application configuration."""

    mode: RuntimeMode = RuntimeMode.DRY_RUN
    default_analysis_profile: str = "calculator_a"
    default_ui_profile: str = "default_mt5"
    paths: PathsConfig = Field(default_factory=PathsConfig)
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    telegram: TelegramSettings = Field(default_factory=TelegramSettings)
    execution: ExecutionSettings = Field(default_factory=ExecutionSettings)
    analysis_profiles: dict[str, AnalysisProfile] = Field(default_factory=dict)
    ui_profiles: dict[str, UiProfile] = Field(default_factory=dict)
    accounts: list[AccountConfig] = Field(default_factory=list)

    @property
    def bot_token(self) -> str | None:
        """Return Telegram bot token from environment."""

        return os.getenv("TELEGRAM_BOT_TOKEN")

    @property
    def tesseract_cmd(self) -> str | None:
        """Return custom Tesseract binary path from environment."""

        explicit = os.getenv("TESSERACT_CMD")
        if explicit:
            return explicit
        discovered = shutil.which("tesseract")
        if discovered:
            return discovered
        for candidate in (
            Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
            Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
        ):
            if candidate.exists():
                return str(candidate)
        return None


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load YAML file or return an empty mapping when missing."""

    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ConfigurationError(f"expected mapping in {path}")
    return data


def _env_bool(name: str) -> bool | None:
    """Parse a boolean environment variable."""

    raw = os.getenv(name)
    if raw is None or raw == "":
        return None
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str) -> int | None:
    """Parse an integer environment variable."""

    raw = os.getenv(name)
    if raw is None or raw == "":
        return None
    return int(raw)


def _env_str(name: str) -> str | None:
    """Return an environment variable unless it is empty."""

    raw = os.getenv(name)
    if raw is None:
        return None
    raw = raw.strip()
    return raw or None


def _env_float(name: str) -> float | None:
    """Parse a float environment variable."""

    raw = os.getenv(name)
    if raw is None or raw == "":
        return None
    return float(raw)


def _apply_env_overrides(merged: dict[str, Any]) -> dict[str, Any]:
    """Apply `.env` overrides on top of YAML config."""

    config = dict(merged)
    telegram = dict(config.get("telegram", {}))
    execution = dict(config.get("execution", {}))

    env_mode = _env_str("APP_MODE")
    if env_mode:
        config["mode"] = env_mode

    env_telegram_enabled = _env_bool("TELEGRAM_ENABLED")
    if env_telegram_enabled is not None:
        telegram["enabled"] = env_telegram_enabled

    env_chat_id = _env_int("TELEGRAM_ALLOWED_CHAT_ID")
    if env_chat_id is not None:
        telegram["allowed_chat_id"] = env_chat_id

    env_thread_id = _env_int("TELEGRAM_ALLOWED_THREAD_ID")
    if env_thread_id is not None:
        telegram["allowed_thread_id"] = env_thread_id

    env_download_dir = _env_str("TELEGRAM_DOWNLOAD_DIR")
    if env_download_dir:
        telegram["download_dir"] = env_download_dir

    env_send_updates = _env_bool("TELEGRAM_SEND_STATUS_UPDATES")
    if env_send_updates is not None:
        telegram["send_status_updates"] = env_send_updates

    env_allow_duplicate_images = _env_bool("TELEGRAM_ALLOW_DUPLICATE_IMAGES")
    if env_allow_duplicate_images is not None:
        telegram["allow_duplicate_images"] = env_allow_duplicate_images

    for env_name, field_name in (
        ("TELEGRAM_CONNECT_TIMEOUT", "connect_timeout"),
        ("TELEGRAM_READ_TIMEOUT", "read_timeout"),
        ("TELEGRAM_WRITE_TIMEOUT", "write_timeout"),
        ("TELEGRAM_POOL_TIMEOUT", "pool_timeout"),
    ):
        env_value = _env_float(env_name)
        if env_value is not None:
            telegram[field_name] = env_value

    env_terminal_path = _env_str("MT5_TERMINAL_PATH")
    if env_terminal_path:
        execution["mt5_terminal_path"] = env_terminal_path

    config["telegram"] = telegram
    config["execution"] = execution
    return config


def load_config(config_dir: Path = Path("config"), env_file: Path | None = Path(".env")) -> AppConfig:
    """Load the full application configuration from YAML files and `.env`."""

    if env_file and env_file.exists():
        load_dotenv(env_file)

    app_yaml = _load_yaml(config_dir / "app.yaml")
    accounts_yaml = _load_yaml(config_dir / "accounts.yaml")
    analysis_yaml = _load_yaml(config_dir / "analysis_profiles.yaml")
    ui_yaml = _load_yaml(config_dir / "ui_profiles.yaml")

    merged = {
        **app_yaml,
        "accounts": accounts_yaml.get("accounts", app_yaml.get("accounts", [])),
        "analysis_profiles": analysis_yaml.get("analysis_profiles", app_yaml.get("analysis_profiles", {})),
        "ui_profiles": ui_yaml.get("ui_profiles", app_yaml.get("ui_profiles", {})),
    }
    merged = _apply_env_overrides(merged)
    config = AppConfig.model_validate(merged)
    config.telegram.allowed_chat_id = TelegramSettings.normalize_chat_id(config.telegram.allowed_chat_id)
    if config.telegram.enabled and config.telegram.allowed_chat_id is None:
        raise ConfigurationError("telegram.enabled=true requires telegram.allowed_chat_id or TELEGRAM_ALLOWED_CHAT_ID")
    if config.execution.backend == "visual" and not config.execution.mt5_terminal_path:
        raise ConfigurationError("visual executor requires execution.mt5_terminal_path")
    return config

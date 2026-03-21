"""Shared test fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import AccountConfig, AnalysisProfile, AppConfig, ExecutionSettings, PathsConfig, RetryPolicy, TelegramSettings, UiProfile
from app.domain.models import RuntimeMode


def make_config(tmp_path: Path, *, mode: RuntimeMode = RuntimeMode.DRY_RUN) -> AppConfig:
    """Build a self-contained app config for tests."""

    return AppConfig(
        mode=mode,
        paths=PathsConfig(
            data_dir=tmp_path / "data",
            inbox_dir=tmp_path / "data" / "inbox",
            processed_dir=tmp_path / "data" / "processed",
            failed_dir=tmp_path / "data" / "failed",
            screenshots_dir=tmp_path / "data" / "screenshots",
            analysis_dir=tmp_path / "data" / "analysis",
            execution_dir=tmp_path / "data" / "execution",
            logs_dir=tmp_path / "logs",
            database_path=tmp_path / "data" / "mt5_god.sqlite3",
        ),
        retry_policy=RetryPolicy(),
        telegram=TelegramSettings(enabled=False, download_dir=tmp_path / "data" / "inbox"),
        execution=ExecutionSettings(backend="dry_run", require_ui_confirmation=True),
        analysis_profiles={"calculator_a": AnalysisProfile()},
        ui_profiles={"default_mt5": UiProfile(templates_dir=tmp_path / "assets")},
        accounts=[
            AccountConfig(account_alias="main_1", enabled=True),
            AccountConfig(account_alias="main_2", enabled=True),
        ],
    )


@pytest.fixture
def app_config(tmp_path: Path) -> AppConfig:
    """Return a disposable app config rooted in the pytest temp directory."""

    return make_config(tmp_path)

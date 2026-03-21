"""Application assembly helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.adapters.capture import LocalFileCaptureAdapter, TelegramCaptureAdapter
from app.adapters.executor import DryRunMt5Executor, VisualMt5Executor
from app.adapters.image_analyzer import HeuristicImageAnalyzer
from app.config import AppConfig, load_config
from app.orchestrator.pipeline import PipelineOrchestrator
from app.services.logging import setup_logging
from app.storage.files import FileStorage
from app.storage.sqlite import SQLiteRepository


@dataclass(slots=True)
class AppContext:
    """Fully assembled runtime services."""

    config: AppConfig
    file_storage: FileStorage
    repository: SQLiteRepository
    local_capture: LocalFileCaptureAdapter
    telegram_capture: TelegramCaptureAdapter | None
    analyzer: HeuristicImageAnalyzer
    executor: DryRunMt5Executor | VisualMt5Executor
    orchestrator: PipelineOrchestrator


def build_context(config_dir: Path = Path("config"), env_file: Path | None = Path(".env")) -> AppContext:
    """Instantiate adapters, storage, and orchestrator from config."""

    config = load_config(config_dir=config_dir, env_file=env_file)
    setup_logging(config.paths)
    file_storage = FileStorage(config.paths)
    repository = SQLiteRepository(config.paths.database_path)
    analyzer = HeuristicImageAnalyzer(config=config, file_storage=file_storage)
    executor = (
        VisualMt5Executor(config=config, file_storage=file_storage)
        if config.execution.backend == "visual"
        else DryRunMt5Executor(config=config, file_storage=file_storage)
    )
    orchestrator = PipelineOrchestrator(
        analyzer=analyzer,
        executor=executor,
        repository=repository,
        file_storage=file_storage,
        runtime_mode=config.mode,
        allow_duplicate_images=config.telegram.allow_duplicate_images,
    )
    local_capture = LocalFileCaptureAdapter(file_storage=file_storage, repository=repository)
    telegram_capture = (
        TelegramCaptureAdapter(config=config, file_storage=file_storage, repository=repository)
        if config.telegram.enabled
        else None
    )
    return AppContext(
        config=config,
        file_storage=file_storage,
        repository=repository,
        local_capture=local_capture,
        telegram_capture=telegram_capture,
        analyzer=analyzer,
        executor=executor,
        orchestrator=orchestrator,
    )

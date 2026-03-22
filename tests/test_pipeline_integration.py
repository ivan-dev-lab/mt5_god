"""Integration tests for the invariant pipeline."""

from pathlib import Path

import pytest

from app.adapters.capture import LocalFileCaptureAdapter
from app.adapters.executor import DryRunMt5Executor
from app.domain.exceptions import DuplicateEventError
from app.adapters.image_analyzer import HeuristicImageAnalyzer
from app.domain.models import CaptureMetadata
from app.domain.models import PipelineStage, RuntimeMode
from app.orchestrator.pipeline import PipelineOrchestrator
from app.storage.files import FileStorage
from app.storage.sqlite import SQLiteRepository


def test_pipeline_runs_end_to_end_in_dry_run(app_config, tmp_path: Path) -> None:
    source = tmp_path / "calculator.png"
    source.write_bytes(b"fake-image")
    source.with_suffix(".png.ocr.txt").write_text(
        "\n".join(
            [
                "Symbol: EURUSD",
                "Direction: Buy",
                "Lot: 0.10",
                "SL: 1.0825",
                "TP: 1.0890",
                "Entry: 1.0840",
            ]
        ),
        encoding="utf-8",
    )

    file_storage = FileStorage(app_config.paths)
    repository = SQLiteRepository(app_config.paths.database_path)
    analyzer = HeuristicImageAnalyzer(config=app_config, file_storage=file_storage)
    executor = DryRunMt5Executor(config=app_config, file_storage=file_storage)
    orchestrator = PipelineOrchestrator(
        analyzer=analyzer,
        executor=executor,
        repository=repository,
        file_storage=file_storage,
        runtime_mode=app_config.mode,
    )
    capture = LocalFileCaptureAdapter(file_storage=file_storage, repository=repository)

    event = capture.capture(source)
    summary = orchestrator.process_capture_event(event)

    assert summary.final_status == PipelineStage.EXECUTED
    assert summary.execution_status == "success"
    assert summary.trade_id is not None

    processed_path = app_config.paths.processed_dir / Path(event.image_path).name
    assert processed_path.exists()
    assert (app_config.paths.analysis_dir / event.event_id / "analysis_result.json").exists()
    assert (app_config.paths.execution_dir / summary.trade_id / "execution_result.json").exists()


def test_caption_with_limit_builds_limit_trade_intent(app_config, tmp_path: Path) -> None:
    source = tmp_path / "calculator_limit.png"
    source.write_bytes(b"fake-image")
    source.with_suffix(".png.ocr.txt").write_text(
        "\n".join(
            [
                "Symbol: EURUSD",
                "Direction: Buy",
                "Lot: 0.10",
                "SL: 1.0825",
                "TP: 1.0890",
                "Entry: 1.0840",
            ]
        ),
        encoding="utf-8",
    )

    file_storage = FileStorage(app_config.paths)
    repository = SQLiteRepository(app_config.paths.database_path)
    analyzer = HeuristicImageAnalyzer(config=app_config, file_storage=file_storage)
    capture = LocalFileCaptureAdapter(file_storage=file_storage, repository=repository)

    event = capture.capture(
        source,
        metadata=CaptureMetadata(caption="limit"),
        allow_duplicate=True,
    )
    result = analyzer.analyze(event)

    assert result.validation.is_valid is True
    assert result.normalized_trade_intent is not None
    assert result.normalized_trade_intent.order_type.value == "limit"
    assert result.normalized_trade_intent.entry_price == 1.084


def test_caption_without_limit_keeps_market_order(app_config, tmp_path: Path) -> None:
    source = tmp_path / "calculator_market.png"
    source.write_bytes(b"fake-image")
    source.with_suffix(".png.ocr.txt").write_text(
        "\n".join(
            [
                "Symbol: EURUSD",
                "Direction: Buy",
                "Lot: 0.10",
                "SL: 1.0825",
                "TP: 1.0890",
                "Entry: 1.0840",
            ]
        ),
        encoding="utf-8",
    )

    file_storage = FileStorage(app_config.paths)
    repository = SQLiteRepository(app_config.paths.database_path)
    analyzer = HeuristicImageAnalyzer(config=app_config, file_storage=file_storage)
    capture = LocalFileCaptureAdapter(file_storage=file_storage, repository=repository)

    event = capture.capture(
        source,
        metadata=CaptureMetadata(caption=""),
        allow_duplicate=True,
    )
    result = analyzer.analyze(event)

    assert result.validation.is_valid is True
    assert result.normalized_trade_intent is not None
    assert result.normalized_trade_intent.order_type.value == "market"


def test_limit_caption_requires_entry_price(app_config, tmp_path: Path) -> None:
    source = tmp_path / "calculator_limit_missing_entry.png"
    source.write_bytes(b"fake-image")
    source.with_suffix(".png.ocr.txt").write_text(
        "\n".join(
            [
                "Symbol: EURUSD",
                "Direction: Buy",
                "Lot: 0.10",
                "SL: 1.0825",
                "TP: 1.0890",
            ]
        ),
        encoding="utf-8",
    )

    file_storage = FileStorage(app_config.paths)
    repository = SQLiteRepository(app_config.paths.database_path)
    analyzer = HeuristicImageAnalyzer(config=app_config, file_storage=file_storage)
    capture = LocalFileCaptureAdapter(file_storage=file_storage, repository=repository)

    event = capture.capture(
        source,
        metadata=CaptureMetadata(caption="лимит"),
        allow_duplicate=True,
    )
    result = analyzer.analyze(event)

    assert result.validation.is_valid is False
    assert "entry_price is required for limit orders" in result.validation.errors
    assert result.normalized_trade_intent is None


def test_pipeline_can_process_same_image_twice_when_duplicates_are_allowed(app_config, tmp_path: Path) -> None:
    source = tmp_path / "calculator_duplicate.png"
    source.write_bytes(b"fake-image")
    source.with_suffix(".png.ocr.txt").write_text(
        "\n".join(
            [
                "Symbol: EURUSD",
                "Direction: Buy",
                "Lot: 0.10",
                "SL: 1.0825",
                "TP: 1.0890",
                "Entry: 1.0840",
            ]
        ),
        encoding="utf-8",
    )

    file_storage = FileStorage(app_config.paths)
    repository = SQLiteRepository(app_config.paths.database_path)
    analyzer = HeuristicImageAnalyzer(config=app_config, file_storage=file_storage)
    executor = DryRunMt5Executor(config=app_config, file_storage=file_storage)
    orchestrator = PipelineOrchestrator(
        analyzer=analyzer,
        executor=executor,
        repository=repository,
        file_storage=file_storage,
        runtime_mode=app_config.mode,
        allow_duplicate_images=True,
    )
    capture = LocalFileCaptureAdapter(file_storage=file_storage, repository=repository)

    first_event = capture.capture(source, allow_duplicate=True)
    first_summary = orchestrator.process_capture_event(first_event)

    second_event = capture.capture(source, allow_duplicate=True)
    second_summary = orchestrator.process_capture_event(second_event)

    assert first_summary.final_status == PipelineStage.EXECUTED
    assert second_summary.final_status == PipelineStage.EXECUTED
    assert first_event.event_id != second_event.event_id
    assert repository.get_task(first_event.event_id).image_sha256 == repository.get_task(second_event.event_id).image_sha256


def test_pipeline_blocks_duplicate_image_in_full_pipeline_mode_by_default(app_config, tmp_path: Path) -> None:
    source = tmp_path / "calculator_duplicate_blocked.png"
    source.write_bytes(b"fake-image")
    source.with_suffix(".png.ocr.txt").write_text(
        "\n".join(
            [
                "Symbol: EURUSD",
                "Direction: Buy",
                "Lot: 0.10",
                "SL: 1.0825",
                "TP: 1.0890",
                "Entry: 1.0840",
            ]
        ),
        encoding="utf-8",
    )

    config = app_config.model_copy(deep=True)
    config.mode = RuntimeMode.FULL_PIPELINE

    file_storage = FileStorage(config.paths)
    repository = SQLiteRepository(config.paths.database_path)
    analyzer = HeuristicImageAnalyzer(config=config, file_storage=file_storage)
    executor = DryRunMt5Executor(config=config, file_storage=file_storage)
    orchestrator = PipelineOrchestrator(
        analyzer=analyzer,
        executor=executor,
        repository=repository,
        file_storage=file_storage,
        runtime_mode=config.mode,
        allow_duplicate_images=config.effective_allow_duplicate_images,
    )
    capture = LocalFileCaptureAdapter(file_storage=file_storage, repository=repository)

    first_event = capture.capture(source, allow_duplicate=True)
    orchestrator.process_capture_event(first_event)

    second_event = capture.capture(source, allow_duplicate=True)
    with pytest.raises(DuplicateEventError):
        orchestrator.process_capture_event(second_event)

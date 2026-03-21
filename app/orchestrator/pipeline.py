"""Invariant pipeline orchestration."""

from __future__ import annotations

from pathlib import Path

from app.domain.exceptions import AnalysisError, DuplicateEventError, ExecutionError
from app.domain.interfaces import AnalyzerAdapter, ExecutorAdapter, Orchestrator
from app.domain.models import CaptureEvent, PipelineRunSummary, PipelineStage, RuntimeMode, TradeIntent
from app.domain.state_machine import PipelineStateMachine
from app.services.logging import get_logger
from app.storage.files import FileStorage
from app.storage.sqlite import SQLiteRepository


class PipelineOrchestrator(Orchestrator):
    """Coordinates capture, analysis, validation, and execution."""

    def __init__(
        self,
        analyzer: AnalyzerAdapter,
        executor: ExecutorAdapter,
        repository: SQLiteRepository,
        file_storage: FileStorage,
        runtime_mode: RuntimeMode,
        allow_duplicate_images: bool = False,
    ) -> None:
        self.analyzer = analyzer
        self.executor = executor
        self.repository = repository
        self.file_storage = file_storage
        self.runtime_mode = runtime_mode
        self.allow_duplicate_images = allow_duplicate_images
        self.state_machine = PipelineStateMachine()
        self.logger = get_logger(__name__)

    def _transition(self, current: PipelineStage, target: PipelineStage, event_id: str, reason: str | None = None) -> PipelineStage:
        """Persist a state transition after validating it."""

        next_stage = self.state_machine.transition(current, target)
        self.repository.update_status(event_id, next_stage, reason)
        self.logger.info(
            "task transition",
            extra={"event_id": event_id, "stage": f"{current.value}->{next_stage.value}", "status": next_stage.value},
        )
        return next_stage

    def process_capture_event(self, capture_event: CaptureEvent, *, mode_override: RuntimeMode | None = None) -> PipelineRunSummary:
        """Run the configured pipeline for a single event."""

        mode = mode_override or self.runtime_mode
        if not self.allow_duplicate_images and self.repository.is_duplicate(
            capture_event.source_message_id,
            capture_event.image_sha256,
        ):
            raise DuplicateEventError(f"duplicate capture detected: {capture_event.event_id}")

        record = self.repository.create_task(capture_event, allow_duplicate=self.allow_duplicate_images)
        current = record.status
        current = self._transition(current, PipelineStage.SAVED, capture_event.event_id)
        current = self._transition(current, PipelineStage.QUEUED_FOR_ANALYSIS, capture_event.event_id)
        current = self._transition(current, PipelineStage.ANALYZING, capture_event.event_id)

        try:
            analysis_result = self.analyzer.analyze(capture_event)
            self.repository.save_analysis(analysis_result)
        except Exception as exc:
            reason = f"analysis error: {exc}"
            self._transition(current, PipelineStage.FAILED, capture_event.event_id, reason)
            self.file_storage.move_failed(Path(capture_event.image_path))
            raise AnalysisError(reason) from exc

        current = self._transition(current, PipelineStage.ANALYZED, capture_event.event_id)
        current = self._transition(current, PipelineStage.VALIDATED, capture_event.event_id)
        if not analysis_result.validation.is_valid or analysis_result.normalized_trade_intent is None:
            reason = "; ".join(analysis_result.validation.errors) or "analysis validation failed"
            self._transition(current, PipelineStage.FAILED, capture_event.event_id, reason)
            self.file_storage.move_failed(Path(capture_event.image_path))
            return PipelineRunSummary(
                event_id=capture_event.event_id,
                final_status=PipelineStage.FAILED,
                analysis_id=analysis_result.analysis_id,
                failure_reason=reason,
                validation_errors=analysis_result.validation.errors,
                warnings=analysis_result.validation.warnings,
            )

        trade_intent = analysis_result.normalized_trade_intent
        self.repository.attach_trade_id(capture_event.event_id, trade_intent.trade_id)

        if mode == RuntimeMode.ANALYZE_ONLY:
            self.file_storage.move_processed(Path(capture_event.image_path))
            return PipelineRunSummary(
                event_id=capture_event.event_id,
                final_status=current,
                analysis_id=analysis_result.analysis_id,
                trade_id=trade_intent.trade_id,
                validation_errors=analysis_result.validation.errors,
                warnings=analysis_result.validation.warnings,
            )

        current = self._transition(current, PipelineStage.READY_FOR_EXECUTION, capture_event.event_id)
        current = self._transition(current, PipelineStage.EXECUTING, capture_event.event_id)

        if mode == RuntimeMode.DRY_RUN:
            trade_intent = TradeIntent.model_validate(
                {
                    **trade_intent.model_dump(mode="json"),
                    "execution_policy": {
                        **trade_intent.execution_policy.model_dump(),
                        "dry_run": True,
                    },
                }
            )

        try:
            execution_result = self.executor.execute(trade_intent)
            self.repository.save_execution(execution_result)
        except Exception as exc:
            reason = f"execution error: {exc}"
            self._transition(current, PipelineStage.FAILED, capture_event.event_id, reason)
            self.file_storage.move_failed(Path(capture_event.image_path))
            raise ExecutionError(reason) from exc

        current = self._transition(current, PipelineStage.EXECUTED, capture_event.event_id)
        self.file_storage.move_processed(Path(capture_event.image_path))
        return PipelineRunSummary(
            event_id=capture_event.event_id,
            final_status=current,
            analysis_id=analysis_result.analysis_id,
            trade_id=trade_intent.trade_id,
            execution_status=execution_result.status,
            warnings=analysis_result.validation.warnings,
        )

    def execute_trade_intent(self, trade_intent: TradeIntent) -> PipelineRunSummary:
        """Execute a prebuilt trade intent without capture/analyze stages."""

        execution_result = self.executor.execute(trade_intent)
        self.repository.save_execution(execution_result)
        return PipelineRunSummary(
            event_id=trade_intent.source_event_id,
            final_status=PipelineStage.EXECUTED if execution_result.status != "failed" else PipelineStage.FAILED,
            trade_id=trade_intent.trade_id,
            execution_status=execution_result.status,
        )

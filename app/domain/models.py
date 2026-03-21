"""Canonical domain models shared across modules."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""

    return datetime.now(timezone.utc)


class RuntimeMode(str, Enum):
    """Top-level runtime modes supported by the application."""

    DRY_RUN = "dry_run"
    ANALYZE_ONLY = "analyze_only"
    EXECUTE_ONLY = "execute_only"
    FULL_PIPELINE = "full_pipeline"


class PipelineStage(str, Enum):
    """State machine for a single pipeline task."""

    RECEIVED = "RECEIVED"
    SAVED = "SAVED"
    QUEUED_FOR_ANALYSIS = "QUEUED_FOR_ANALYSIS"
    ANALYZING = "ANALYZING"
    ANALYZED = "ANALYZED"
    VALIDATED = "VALIDATED"
    READY_FOR_EXECUTION = "READY_FOR_EXECUTION"
    EXECUTING = "EXECUTING"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class AnalysisStatus(str, Enum):
    """Outcome of analyzer execution."""

    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class ExecutionStatus(str, Enum):
    """Outcome of executor execution."""

    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"


class Direction(str, Enum):
    """Supported trade directions."""

    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    """Supported order kinds."""

    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"


class DomainModel(BaseModel):
    """Base model with common JSON behavior."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid", use_enum_values=False)


class CaptureMetadata(DomainModel):
    """Telegram/file metadata attached to a capture event."""

    sender_id: str | None = None
    caption: str | None = None
    file_name: str | None = None


class CaptureEvent(DomainModel):
    """Represents the arrival of a new source image."""

    event_id: str = Field(default_factory=lambda: str(uuid4()))
    source: str = "telegram"
    source_chat_id: str | None = None
    source_thread_id: str | None = None
    source_message_id: str | None = None
    received_at: datetime = Field(default_factory=utc_now)
    image_path: str
    image_sha256: str
    metadata: CaptureMetadata = Field(default_factory=CaptureMetadata)


class DetectedFields(DomainModel):
    """Raw field set extracted from OCR/vision."""

    symbol: str | None = None
    direction: Direction | None = None
    entry_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    risk_percent: float | None = None
    lot: float | None = None


class ConfidenceFields(DomainModel):
    """Per-field confidence scores."""

    symbol: float = 0.0
    direction: float = 0.0
    entry_price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    lot: float = 0.0
    risk_percent: float = 0.0


class ConfidenceResult(DomainModel):
    """Overall and per-field confidence."""

    overall: float = 0.0
    fields: ConfidenceFields = Field(default_factory=ConfidenceFields)


class ValidationResult(DomainModel):
    """Validation outcome for an analyzed trade."""

    is_valid: bool = False
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class AnalysisArtifacts(DomainModel):
    """Artifacts generated during analysis."""

    annotated_image_path: str | None = None
    ocr_dump_path: str | None = None


class AccountTarget(DomainModel):
    """An account that should receive the trade."""

    account_alias: str
    enabled: bool = True


class ExecutionPolicy(DomainModel):
    """Execution constraints attached to a trade intent."""

    dry_run: bool = False
    max_retries: int = 2
    require_ui_confirmation: bool = True


class TradeIntentMeta(DomainModel):
    """Additional metadata on a normalized trade intent."""

    raw_source_image: str
    analysis_confidence: float = 0.0


class TradeIntent(DomainModel):
    """Normalized trade request consumed by the executor."""

    trade_id: str = Field(default_factory=lambda: str(uuid4()))
    source_event_id: str
    symbol: str
    direction: Direction
    order_type: OrderType = OrderType.MARKET
    volume: float
    entry_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    comment: str = "generated_from_image"
    accounts: list[AccountTarget] = Field(default_factory=list)
    execution_policy: ExecutionPolicy = Field(default_factory=ExecutionPolicy)
    meta: TradeIntentMeta

    @field_validator("volume")
    @classmethod
    def validate_volume(cls, value: float) -> float:
        """Ensure volume is positive."""

        if value <= 0:
            raise ValueError("volume must be positive")
        return value


class AnalysisResult(DomainModel):
    """Structured analyzer output."""

    event_id: str
    analysis_id: str = Field(default_factory=lambda: str(uuid4()))
    status: AnalysisStatus = AnalysisStatus.FAILED
    detected_fields: DetectedFields = Field(default_factory=DetectedFields)
    normalized_trade_intent: TradeIntent | None = None
    confidence: ConfidenceResult = Field(default_factory=ConfidenceResult)
    validation: ValidationResult = Field(default_factory=ValidationResult)
    artifacts: AnalysisArtifacts = Field(default_factory=AnalysisArtifacts)


class AccountExecutionResult(DomainModel):
    """Per-account MT5 execution outcome."""

    account_alias: str
    status: ExecutionStatus
    order_submit_detected: bool = False
    error_message: str | None = None
    screenshot_path: str | None = None
    technical_log: list[str] = Field(default_factory=list)


class ExecutionResult(DomainModel):
    """Executor response for a trade intent."""

    trade_id: str
    status: Literal["success", "partial", "failed"]
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime = Field(default_factory=utc_now)
    accounts: list[AccountExecutionResult] = Field(default_factory=list)


class AccountSwitchTestResult(DomainModel):
    """Result of a test-only MT5 account switching pass."""

    run_id: str = Field(default_factory=lambda: str(uuid4()))
    status: Literal["success", "partial", "failed"]
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime = Field(default_factory=utc_now)
    accounts: list[AccountExecutionResult] = Field(default_factory=list)


class TaskRecord(DomainModel):
    """Persistent record of pipeline processing."""

    event_id: str
    status: PipelineStage
    failure_reason: str | None = None
    trade_id: str | None = None
    source_message_id: str | None = None
    image_sha256: str | None = None
    image_path: str | None = None
    received_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class PipelineRunSummary(DomainModel):
    """High-level outcome returned by the orchestrator and CLI."""

    event_id: str
    final_status: PipelineStage
    analysis_id: str | None = None
    trade_id: str | None = None
    execution_status: str | None = None
    failure_reason: str | None = None
    validation_errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

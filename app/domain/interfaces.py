"""Contracts for pluggable adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.domain.models import AccountSwitchTestResult, AnalysisResult, CaptureEvent, ExecutionResult, PipelineRunSummary, TradeIntent


class CaptureAdapter(ABC):
    """Produces `CaptureEvent` objects and forwards them to the pipeline."""

    @abstractmethod
    def run(self, handler) -> None:
        """Start listening for events and call `handler(event)` for each image."""


class AnalyzerAdapter(ABC):
    """Transforms an image capture into normalized trade data."""

    @abstractmethod
    def analyze(self, capture_event: CaptureEvent) -> AnalysisResult:
        """Analyze a captured image and return the structured result."""


class ExecutorAdapter(ABC):
    """Executes a validated trade intent."""

    @abstractmethod
    def execute(self, trade_intent: TradeIntent) -> ExecutionResult:
        """Perform the trade via the selected backend."""

    @abstractmethod
    def test_accounts(self) -> AccountSwitchTestResult:
        """Run a test-only pass over configured accounts."""


class Orchestrator(ABC):
    """Coordinates the invariant pipeline between adapters."""

    @abstractmethod
    def process_capture_event(self, capture_event: CaptureEvent, *, mode_override=None) -> PipelineRunSummary:
        """Run the pipeline for a single incoming image."""

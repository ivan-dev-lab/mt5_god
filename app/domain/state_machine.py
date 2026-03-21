"""State transition rules for the invariant pipeline."""

from __future__ import annotations

from app.domain.exceptions import PipelineTransitionError
from app.domain.models import PipelineStage

ALLOWED_TRANSITIONS: dict[PipelineStage, set[PipelineStage]] = {
    PipelineStage.RECEIVED: {PipelineStage.SAVED, PipelineStage.FAILED, PipelineStage.CANCELLED},
    PipelineStage.SAVED: {PipelineStage.QUEUED_FOR_ANALYSIS, PipelineStage.FAILED, PipelineStage.CANCELLED},
    PipelineStage.QUEUED_FOR_ANALYSIS: {PipelineStage.ANALYZING, PipelineStage.FAILED, PipelineStage.CANCELLED},
    PipelineStage.ANALYZING: {PipelineStage.ANALYZED, PipelineStage.FAILED, PipelineStage.CANCELLED},
    PipelineStage.ANALYZED: {PipelineStage.VALIDATED, PipelineStage.FAILED, PipelineStage.CANCELLED},
    PipelineStage.VALIDATED: {PipelineStage.READY_FOR_EXECUTION, PipelineStage.FAILED, PipelineStage.CANCELLED},
    PipelineStage.READY_FOR_EXECUTION: {PipelineStage.EXECUTING, PipelineStage.FAILED, PipelineStage.CANCELLED},
    PipelineStage.EXECUTING: {PipelineStage.EXECUTED, PipelineStage.FAILED, PipelineStage.CANCELLED},
    PipelineStage.EXECUTED: set(),
    PipelineStage.FAILED: set(),
    PipelineStage.CANCELLED: set(),
}


class PipelineStateMachine:
    """Checks that task status changes preserve the pipeline contract."""

    def can_transition(self, current: PipelineStage, target: PipelineStage) -> bool:
        """Return `True` if the transition is allowed or idempotent."""

        return current == target or target in ALLOWED_TRANSITIONS[current]

    def transition(self, current: PipelineStage, target: PipelineStage) -> PipelineStage:
        """Validate and return the new state."""

        if not self.can_transition(current, target):
            raise PipelineTransitionError(f"invalid transition: {current.value} -> {target.value}")
        return target

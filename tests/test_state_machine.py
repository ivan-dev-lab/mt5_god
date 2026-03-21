"""Tests for pipeline transition rules."""

import pytest

from app.domain.exceptions import PipelineTransitionError
from app.domain.models import PipelineStage
from app.domain.state_machine import PipelineStateMachine


def test_pipeline_state_machine_allows_happy_path() -> None:
    machine = PipelineStateMachine()
    current = PipelineStage.RECEIVED
    for target in (
        PipelineStage.SAVED,
        PipelineStage.QUEUED_FOR_ANALYSIS,
        PipelineStage.ANALYZING,
        PipelineStage.ANALYZED,
        PipelineStage.VALIDATED,
        PipelineStage.READY_FOR_EXECUTION,
        PipelineStage.EXECUTING,
        PipelineStage.EXECUTED,
    ):
        current = machine.transition(current, target)
    assert current == PipelineStage.EXECUTED


def test_pipeline_state_machine_rejects_invalid_jump() -> None:
    machine = PipelineStateMachine()
    with pytest.raises(PipelineTransitionError):
        machine.transition(PipelineStage.RECEIVED, PipelineStage.EXECUTING)

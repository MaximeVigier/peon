import pytest
from pydantic import ValidationError

from peon.models.action import Action
from peon.models.mission import MissionStatus
from peon.models.tool_spec import RiskLevel
from peon.models.verdict import Verdict, VerdictType
from peon.state_machine import (
    AbortRequested,
    ConfirmationDenied,
    ConfirmationGranted,
    InvalidTransitionError,
    MaxIterationsReached,
    MissionCreated,
    MissionFailed,
    MissionSucceeded,
    PolicyEvaluated,
    ToolExecutionFinished,
    transition,
)


def _verdict(verdict_type: VerdictType) -> Verdict:
    suggested_action = None
    if verdict_type is VerdictType.REWRITE:
        suggested_action = Action(reasoning="x", tool_name="run_command", arguments={}, risk_level=RiskLevel.LOW)
    return Verdict(type=verdict_type, reason="x", suggested_action=suggested_action)


# --- Transitions valides -----------------------------------------------------


def test_mission_created_moves_created_to_reasoning() -> None:
    assert transition(MissionStatus.CREATED, MissionCreated()) == MissionStatus.REASONING


@pytest.mark.parametrize(
    ("verdict_type", "expected_state"),
    [
        (VerdictType.ALLOWED, MissionStatus.EXECUTING),
        (VerdictType.DENIED, MissionStatus.REASONING),
        (VerdictType.REWRITE, MissionStatus.REASONING),
        (VerdictType.REQUIRES_CONFIRMATION, MissionStatus.AWAITING_CONFIRMATION),
    ],
)
def test_policy_evaluated_routes_by_verdict_type(verdict_type: VerdictType, expected_state: MissionStatus) -> None:
    event = PolicyEvaluated(verdict=_verdict(verdict_type))

    assert transition(MissionStatus.REASONING, event) == expected_state


def test_mission_succeeded_moves_reasoning_to_succeeded() -> None:
    assert transition(MissionStatus.REASONING, MissionSucceeded()) == MissionStatus.SUCCEEDED


def test_mission_failed_moves_reasoning_to_failed() -> None:
    assert transition(MissionStatus.REASONING, MissionFailed()) == MissionStatus.FAILED


def test_max_iterations_reached_moves_reasoning_to_max_iterations() -> None:
    assert transition(MissionStatus.REASONING, MaxIterationsReached()) == MissionStatus.MAX_ITERATIONS


def test_confirmation_granted_moves_awaiting_confirmation_to_executing() -> None:
    assert (
        transition(MissionStatus.AWAITING_CONFIRMATION, ConfirmationGranted()) == MissionStatus.EXECUTING
    )


def test_confirmation_denied_moves_awaiting_confirmation_to_reasoning() -> None:
    assert (
        transition(MissionStatus.AWAITING_CONFIRMATION, ConfirmationDenied()) == MissionStatus.REASONING
    )


def test_tool_execution_finished_moves_executing_to_reasoning() -> None:
    assert transition(MissionStatus.EXECUTING, ToolExecutionFinished()) == MissionStatus.REASONING


@pytest.mark.parametrize(
    "state",
    [MissionStatus.CREATED, MissionStatus.REASONING, MissionStatus.EXECUTING, MissionStatus.AWAITING_CONFIRMATION],
)
def test_abort_requested_moves_any_non_terminal_state_to_aborted(state: MissionStatus) -> None:
    assert transition(state, AbortRequested()) == MissionStatus.ABORTED


# --- Transitions invalides ----------------------------------------------------


@pytest.mark.parametrize(
    "state",
    [MissionStatus.SUCCEEDED, MissionStatus.FAILED, MissionStatus.MAX_ITERATIONS, MissionStatus.ABORTED],
)
def test_abort_requested_from_a_terminal_state_is_invalid(state: MissionStatus) -> None:
    with pytest.raises(InvalidTransitionError):
        transition(state, AbortRequested())


def test_confirmation_granted_without_a_pending_confirmation_is_invalid() -> None:
    with pytest.raises(InvalidTransitionError):
        transition(MissionStatus.REASONING, ConfirmationGranted())


def test_policy_evaluated_outside_reasoning_is_invalid() -> None:
    with pytest.raises(InvalidTransitionError):
        transition(MissionStatus.EXECUTING, PolicyEvaluated(verdict=_verdict(VerdictType.ALLOWED)))


def test_mission_created_outside_created_is_invalid() -> None:
    with pytest.raises(InvalidTransitionError):
        transition(MissionStatus.REASONING, MissionCreated())


def test_tool_execution_finished_outside_executing_is_invalid() -> None:
    with pytest.raises(InvalidTransitionError):
        transition(MissionStatus.REASONING, ToolExecutionFinished())


def test_invalid_transition_error_carries_state_and_event() -> None:
    event = MissionCreated()

    with pytest.raises(InvalidTransitionError) as excinfo:
        transition(MissionStatus.REASONING, event)

    assert excinfo.value.state == MissionStatus.REASONING
    assert excinfo.value.event is event


# --- Purete --------------------------------------------------------------------


def test_events_are_frozen() -> None:
    event = ConfirmationGranted()

    with pytest.raises(ValidationError):
        event.kind = "confirmation_denied"  # type: ignore[misc]


def test_transition_is_deterministic_given_the_same_inputs() -> None:
    event = PolicyEvaluated(verdict=_verdict(VerdictType.ALLOWED))

    first = transition(MissionStatus.REASONING, event)
    second = transition(MissionStatus.REASONING, event)

    assert first == second == MissionStatus.EXECUTING

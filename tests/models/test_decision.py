import pytest
from pydantic import TypeAdapter, ValidationError

from peon.models.decision import ActionDecision, Decision, FinishDecision

decision_adapter = TypeAdapter(Decision)


def test_action_decision_defaults() -> None:
    decision = ActionDecision(reasoning="Il faut lire le fichier", tool_name="read_file")

    assert decision.kind == "action"
    assert decision.arguments == {}


@pytest.mark.parametrize("tool_name", ["ReadFile", "read file", "1read_file", ""])
def test_action_decision_rejects_invalid_tool_names(tool_name: str) -> None:
    with pytest.raises(ValidationError):
        ActionDecision(reasoning="x", tool_name=tool_name)


def test_action_decision_requires_reasoning() -> None:
    with pytest.raises(ValidationError):
        ActionDecision(reasoning="", tool_name="read_file")


@pytest.mark.parametrize("outcome", ["success", "failure"])
def test_finish_decision_accepts_both_outcomes(outcome: str) -> None:
    decision = FinishDecision(outcome=outcome, summary="Termine", confidence=0.9)

    assert decision.kind == "finished"
    assert decision.outcome == outcome


@pytest.mark.parametrize("confidence", [-0.01, 1.01])
def test_finish_decision_confidence_must_be_in_unit_range(confidence: float) -> None:
    with pytest.raises(ValidationError):
        FinishDecision(outcome="success", summary="x", confidence=confidence)


def test_decision_union_dispatches_on_kind() -> None:
    action = decision_adapter.validate_python(
        {"kind": "action", "reasoning": "x", "tool_name": "run_command", "arguments": {"command": "pytest"}}
    )
    finished = decision_adapter.validate_python(
        {"kind": "finished", "outcome": "success", "summary": "x", "confidence": 0.5}
    )

    assert isinstance(action, ActionDecision)
    assert isinstance(finished, FinishDecision)


def test_decision_union_rejects_unknown_kind() -> None:
    with pytest.raises(ValidationError):
        decision_adapter.validate_python({"kind": "cancelled", "reasoning": "x"})

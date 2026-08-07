import pytest
from pydantic import ValidationError

from peon.models.action import Action
from peon.models.tool_spec import RiskLevel
from peon.models.verdict import Verdict, VerdictType


def _action() -> Action:
    return Action(reasoning="x", tool_name="run_command", arguments={}, risk_level=RiskLevel.LOW)


def test_allowed_without_suggested_action() -> None:
    verdict = Verdict(type=VerdictType.ALLOWED, reason="no rule triggered")

    assert verdict.suggested_action is None


def test_rewrite_requires_a_suggested_action() -> None:
    with pytest.raises(ValidationError):
        Verdict(type=VerdictType.REWRITE, reason="safer alternative available")


def test_rewrite_with_suggested_action_is_valid() -> None:
    verdict = Verdict(type=VerdictType.REWRITE, reason="safer alternative available", suggested_action=_action())

    assert verdict.suggested_action is not None


@pytest.mark.parametrize("verdict_type", [VerdictType.ALLOWED, VerdictType.DENIED, VerdictType.REQUIRES_CONFIRMATION])
def test_non_rewrite_verdicts_reject_a_suggested_action(verdict_type: VerdictType) -> None:
    with pytest.raises(ValidationError):
        Verdict(type=verdict_type, reason="x", suggested_action=_action())


@pytest.mark.parametrize("reason", ["", "   "])
def test_blank_reason_is_rejected(reason: str) -> None:
    with pytest.raises(ValidationError):
        Verdict(type=VerdictType.ALLOWED, reason=reason)


def test_invalid_verdict_type_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Verdict(type="maybe", reason="x")

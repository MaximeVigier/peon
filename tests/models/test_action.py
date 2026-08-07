import pytest
from pydantic import ValidationError

from peon.models.action import Action
from peon.models.tool_spec import RiskLevel


def test_valid_action() -> None:
    action = Action(
        reasoning="Supprimer le dossier de build",
        tool_name="run_command",
        arguments={"command": "rm -rf build"},
        risk_level=RiskLevel.HIGH,
    )

    assert action.risk_level == RiskLevel.HIGH
    assert action.arguments == {"command": "rm -rf build"}


def test_arguments_default_to_empty_dict() -> None:
    action = Action(reasoning="x", tool_name="list_directory", risk_level=RiskLevel.LOW)

    assert action.arguments == {}


def test_invalid_tool_name_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Action(reasoning="x", tool_name="Not A Tool", risk_level=RiskLevel.LOW)


def test_invalid_risk_level_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Action(reasoning="x", tool_name="read_file", risk_level="extreme")


def test_missing_risk_level_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Action(reasoning="x", tool_name="read_file")

from typing import Any

import pytest

from peon.models.tool_spec import RiskLevel
from peon.tools.shell import ShellTool


def test_spec_declares_run_command_with_required_command() -> None:
    spec = ShellTool().spec

    assert spec.name == "run_command"
    assert spec.risk_level is RiskLevel.MEDIUM
    assert spec.parameters_schema["required"] == ["command"]
    assert spec.parameters_schema["properties"]["command"]["type"] == "string"


def test_running_a_successful_command_returns_success() -> None:
    result = ShellTool().execute({"command": "echo hello"})

    assert result.success is True
    assert result.return_code == 0
    assert result.error is None


def test_stdout_is_captured_correctly() -> None:
    result = ShellTool().execute({"command": "echo hello"})

    assert result.success is True
    assert result.output["stdout"].strip() == "hello"


def test_nonexistent_command_fails_without_raising() -> None:
    result = ShellTool().execute({"command": "this_command_does_not_exist_ai_lab_test"})

    assert result.success is False
    assert result.error is not None
    assert result.return_code != 0


def test_non_zero_return_code_fails_without_raising() -> None:
    result = ShellTool().execute({"command": "exit 3"})

    assert result.success is False
    assert result.return_code == 3
    assert result.error is not None


def test_missing_command_argument_fails_without_raising() -> None:
    result = ShellTool().execute({})

    assert result.success is False
    assert result.error is not None
    assert result.return_code is None


def test_non_string_command_argument_fails_without_raising() -> None:
    result = ShellTool().execute({"command": 123})

    assert result.success is False
    assert result.error is not None


@pytest.mark.parametrize(
    "arguments", [{}, {"command": 123}, {"command": None}, {"command": "   "}, {"command": ["a"]}]
)
def test_never_raises_for_any_invalid_arguments(arguments: dict[str, Any]) -> None:
    result = ShellTool().execute(arguments)

    assert result.success is False

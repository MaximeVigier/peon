import sys
from pathlib import Path
from typing import Any

import pytest

from peon.models.tool_spec import RiskLevel
from peon.tools.shell import ShellTool
from peon.workspace import LocalWorkspace

_WORKSPACE = LocalWorkspace()


def test_spec_declares_run_command_with_required_command() -> None:
    spec = ShellTool(_WORKSPACE).spec

    assert spec.name == "run_command"
    assert spec.risk_level is RiskLevel.MEDIUM
    assert spec.parameters_schema["required"] == ["command"]
    assert spec.parameters_schema["properties"]["command"]["type"] == "string"


def test_running_a_successful_command_returns_success() -> None:
    result = ShellTool(_WORKSPACE).execute({"command": "echo hello"})

    assert result.success is True
    assert result.return_code == 0
    assert result.error is None


def test_stdout_is_captured_correctly() -> None:
    result = ShellTool(_WORKSPACE).execute({"command": "echo hello"})

    assert result.success is True
    assert result.output["stdout"].strip() == "hello"


def test_nonexistent_command_fails_without_raising() -> None:
    result = ShellTool(_WORKSPACE).execute({"command": "this_command_does_not_exist_ai_lab_test"})

    assert result.success is False
    assert result.error is not None
    assert result.return_code != 0


def test_non_zero_return_code_fails_without_raising() -> None:
    result = ShellTool(_WORKSPACE).execute({"command": "exit 3"})

    assert result.success is False
    assert result.return_code == 3
    assert result.error is not None


def test_missing_command_argument_fails_without_raising() -> None:
    result = ShellTool(_WORKSPACE).execute({})

    assert result.success is False
    assert result.error is not None
    assert result.return_code is None


def test_non_string_command_argument_fails_without_raising() -> None:
    result = ShellTool(_WORKSPACE).execute({"command": 123})

    assert result.success is False
    assert result.error is not None


@pytest.mark.parametrize(
    "arguments", [{}, {"command": 123}, {"command": None}, {"command": "   "}, {"command": ["a"]}]
)
def test_never_raises_for_any_invalid_arguments(arguments: dict[str, Any]) -> None:
    result = ShellTool(_WORKSPACE).execute(arguments)

    assert result.success is False


def test_execute_never_raises_for_a_command_producing_invalid_utf8_output(tmp_path: Path) -> None:
    script = tmp_path / "invalid_utf8.py"
    script.write_text(
        "import sys\nsys.stdout.buffer.write(bytes([0x68, 0x69, 0xff, 0x21]))\n",
        encoding="utf-8",
    )
    command = f'"{sys.executable}" "{script}"'

    result = ShellTool(_WORKSPACE).execute({"command": command})

    assert result.success is True
    assert result.return_code == 0
    assert isinstance(result.output["stdout"], str)
    assert "�" in result.output["stdout"]

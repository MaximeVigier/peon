from typing import Any

import pytest

from peon.models.tool_result import ToolResult
from peon.models.tool_spec import RiskLevel, ToolSpec
from peon.tools.base import Tool

_ECHO_SPEC = ToolSpec(
    name="echo",
    description="Renvoie les arguments recus.",
    parameters_schema={"type": "object", "properties": {"message": {"type": "string"}}},
    risk_level=RiskLevel.LOW,
)


class _EchoTool(Tool):
    @property
    def spec(self) -> ToolSpec:
        return _ECHO_SPEC

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        return ToolResult(success=True, output=arguments.get("message"))


def test_tool_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        Tool()  # type: ignore[abstract]


def test_subclass_missing_execute_cannot_be_instantiated() -> None:
    class _MissingExecute(Tool):
        @property
        def spec(self) -> ToolSpec:
            return _ECHO_SPEC

    with pytest.raises(TypeError):
        _MissingExecute()  # type: ignore[abstract]


def test_subclass_missing_spec_cannot_be_instantiated() -> None:
    class _MissingSpec(Tool):
        def execute(self, arguments: dict[str, Any]) -> ToolResult:
            return ToolResult(success=True)

    with pytest.raises(TypeError):
        _MissingSpec()  # type: ignore[abstract]


def test_conforming_subclass_exposes_spec_and_executes() -> None:
    tool = _EchoTool()

    assert tool.spec == _ECHO_SPEC

    result = tool.execute({"message": "salut"})

    assert isinstance(result, ToolResult)
    assert result.success is True
    assert result.output == "salut"

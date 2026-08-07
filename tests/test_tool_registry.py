from typing import Any

import pytest

from peon.models.tool_result import ToolResult
from peon.models.tool_spec import RiskLevel, ToolSpec
from peon.tool_registry import ToolAlreadyRegisteredError, ToolNotFoundError, ToolRegistry
from peon.tools.base import Tool


class _StubTool(Tool):
    def __init__(self, name: str, risk_level: RiskLevel = RiskLevel.LOW) -> None:
        self._spec = ToolSpec(
            name=name,
            description=f"Outil {name}",
            parameters_schema={"type": "object", "properties": {}},
            risk_level=risk_level,
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        return ToolResult(success=True, output=None)


def test_empty_registry_has_no_tools() -> None:
    registry = ToolRegistry()

    assert registry.list_tools() == []
    assert registry.exists("read_file") is False


def test_register_then_get_returns_the_same_tool() -> None:
    registry = ToolRegistry()
    tool = _StubTool("read_file")

    registry.register(tool)

    assert registry.get("read_file") is tool
    assert registry.exists("read_file") is True


def test_registering_the_same_name_twice_is_rejected() -> None:
    registry = ToolRegistry()
    registry.register(_StubTool("read_file"))

    with pytest.raises(ToolAlreadyRegisteredError):
        registry.register(_StubTool("read_file"))


def test_getting_an_unknown_tool_raises() -> None:
    registry = ToolRegistry()

    with pytest.raises(ToolNotFoundError):
        registry.get("does_not_exist")


def test_list_tools_returns_the_spec_of_every_registered_tool() -> None:
    registry = ToolRegistry()
    read_file = _StubTool("read_file")
    run_command = _StubTool("run_command", risk_level=RiskLevel.HIGH)

    registry.register(read_file)
    registry.register(run_command)

    specs = registry.list_tools()
    assert len(specs) == 2
    assert read_file.spec in specs
    assert run_command.spec in specs

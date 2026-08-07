from typing import Any

from peon.models.action import Action
from peon.models.tool_result import ToolResult
from peon.models.tool_spec import RiskLevel, ToolSpec
from peon.models.verdict import VerdictType
from peon.policy import PolicyEngine
from peon.tool_registry import ToolRegistry
from peon.tools.base import Tool


class _StubTool(Tool):
    def __init__(self, name: str, risk_level: RiskLevel) -> None:
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
        raise AssertionError("le Policy Engine ne doit jamais executer un Tool")


def _registry(*tools: Tool) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    return registry


def _action(tool_name: str, **arguments: Any) -> Action:
    return Action(reasoning="x", tool_name=tool_name, arguments=arguments, risk_level=RiskLevel.LOW)


def test_low_risk_action_is_allowed() -> None:
    engine = PolicyEngine(_registry(_StubTool("read_file", RiskLevel.LOW)))

    verdict = engine.evaluate(_action("read_file", path="README.md"))

    assert verdict.type is VerdictType.ALLOWED


def test_untargeted_delete_of_an_unscoped_path_is_denied() -> None:
    engine = PolicyEngine(_registry(_StubTool("run_command", RiskLevel.HIGH)))

    verdict = engine.evaluate(_action("run_command", command="rm -rf /"))

    assert verdict.type is VerdictType.DENIED
    assert verdict.suggested_action is None


def test_high_risk_action_requires_confirmation() -> None:
    engine = PolicyEngine(_registry(_StubTool("git_push", RiskLevel.HIGH)))

    verdict = engine.evaluate(_action("git_push", remote="origin", branch="main"))

    assert verdict.type is VerdictType.REQUIRES_CONFIRMATION


def test_untargeted_delete_of_a_relative_path_is_rewritten() -> None:
    engine = PolicyEngine(_registry(_StubTool("run_command", RiskLevel.HIGH)))

    verdict = engine.evaluate(_action("run_command", command="rm -rf build"))

    assert verdict.type is VerdictType.REWRITE
    assert verdict.suggested_action is not None
    assert verdict.suggested_action.arguments["command"] == "rm -rf -- ./build"


def test_unknown_tool_is_denied() -> None:
    engine = PolicyEngine(_registry())

    verdict = engine.evaluate(_action("does_not_exist"))

    assert verdict.type is VerdictType.DENIED

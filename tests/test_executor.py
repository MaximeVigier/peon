from typing import Any

from peon.executor import Executor
from peon.models.action import Action
from peon.models.execution_error import ErrorCategory, ExecutionError
from peon.models.tool_result import ToolResult
from peon.models.tool_spec import RiskLevel, ToolSpec
from peon.tool_registry import ToolRegistry
from peon.tools.base import Tool


class _SucceedingTool(Tool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="succeed",
            description="Reussit toujours.",
            parameters_schema={"type": "object", "properties": {}},
            risk_level=RiskLevel.LOW,
        )

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        return ToolResult(success=True, output=arguments.get("value", "ok"))


class _FailingTool(Tool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="fail",
            description="Echoue toujours fonctionnellement.",
            parameters_schema={"type": "object", "properties": {}},
            risk_level=RiskLevel.LOW,
        )

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        return ToolResult(success=False, error="echec fonctionnel", return_code=1)


class _CrashingTool(Tool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="crash",
            description="Leve une exception inattendue.",
            parameters_schema={"type": "object", "properties": {}},
            risk_level=RiskLevel.LOW,
        )

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        raise RuntimeError("bug d'implementation")


def _registry(*tools: Tool) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    return registry


def _action(tool_name: str, **arguments: Any) -> Action:
    return Action(reasoning="x", tool_name=tool_name, arguments=arguments, risk_level=RiskLevel.LOW)


def test_resolves_the_registered_tool_and_forwards_arguments() -> None:
    executor = Executor(_registry(_SucceedingTool()))

    result = executor.run(_action("succeed", value="salut"))

    assert isinstance(result, ToolResult)
    assert result.output == "salut"


def test_missing_tool_becomes_an_invalid_request_execution_error() -> None:
    executor = Executor(_registry())

    result = executor.run(_action("does_not_exist"))

    assert isinstance(result, ExecutionError)
    assert result.category == ErrorCategory.INVALID_REQUEST
    assert result.tool_name == "does_not_exist"


def test_successful_tool_result_is_returned_as_is() -> None:
    executor = Executor(_registry(_SucceedingTool()))

    result = executor.run(_action("succeed"))

    assert isinstance(result, ToolResult)
    assert result.success is True


def test_failing_tool_result_becomes_a_tool_failure_execution_error() -> None:
    executor = Executor(_registry(_FailingTool()))

    result = executor.run(_action("fail"))

    assert isinstance(result, ExecutionError)
    assert result.category == ErrorCategory.TOOL_FAILURE
    assert result.tool_name == "fail"
    assert result.message == "echec fonctionnel"
    assert result.details == {"return_code": 1}


def test_unexpected_exception_becomes_a_system_error() -> None:
    executor = Executor(_registry(_CrashingTool()))

    result = executor.run(_action("crash"))

    assert isinstance(result, ExecutionError)
    assert result.category == ErrorCategory.SYSTEM_ERROR
    assert result.tool_name == "crash"
    assert "bug d'implementation" in result.message

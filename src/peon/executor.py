from peon.models.action import Action
from peon.models.execution_error import ErrorCategory, ExecutionError
from peon.models.tool_result import ToolResult
from peon.tool_registry import ToolNotFoundError, ToolRegistry


class Executor:
    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def run(self, action: Action) -> ToolResult | ExecutionError:
        try:
            tool = self._registry.get(action.tool_name)
        except ToolNotFoundError:
            return ExecutionError(
                category=ErrorCategory.INVALID_REQUEST,
                message=f"tool '{action.tool_name}' is not registered",
                tool_name=action.tool_name,
            )

        try:
            result = tool.execute(action.arguments)
        except Exception as exc:
            # Contrat Tool : seul un bug d'implementation doit remonter en exception.
            # Executor est le filet de securite qui l'empeche de faire planter l'appelant.
            return ExecutionError(
                category=ErrorCategory.SYSTEM_ERROR,
                message=str(exc),
                tool_name=action.tool_name,
                details={"exception_type": type(exc).__name__},
            )

        if not result.success:
            details = {"return_code": result.return_code} if result.return_code is not None else None
            return ExecutionError(
                category=ErrorCategory.TOOL_FAILURE,
                message=result.error or "tool execution failed",
                tool_name=action.tool_name,
                details=details,
            )

        return result

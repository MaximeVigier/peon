from typing import Any

from peon.models.tool_result import ToolResult
from peon.models.tool_spec import RiskLevel, ToolSpec
from peon.tools.base import Tool
from peon.workspace import Workspace

_RUN_COMMAND_SPEC = ToolSpec(
    name="run_command",
    description="Exécute une commande shell",
    parameters_schema={
        "type": "object",
        "properties": {"command": {"type": "string"}},
        "required": ["command"],
    },
    risk_level=RiskLevel.MEDIUM,
)


class ShellTool(Tool):
    def __init__(self, workspace: Workspace) -> None:
        self._workspace = workspace

    @property
    def spec(self) -> ToolSpec:
        return _RUN_COMMAND_SPEC

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        command = arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            return ToolResult(
                success=False,
                error="l'argument 'command' est requis et doit etre une chaine non vide",
            )

        try:
            result = self._workspace.run_command(command)
        except OSError as exc:
            return ToolResult(success=False, error=f"execution de '{command}' impossible : {exc}")

        if result.return_code != 0:
            error = result.stderr.strip() or f"la commande a echoue avec le code {result.return_code}"
            return ToolResult(success=False, error=error, return_code=result.return_code)

        return ToolResult(
            success=True,
            output={"stdout": result.stdout, "stderr": result.stderr},
            return_code=0,
        )

import subprocess
from typing import Any

from peon.models.tool_result import ToolResult
from peon.models.tool_spec import RiskLevel, ToolSpec
from peon.tools.base import Tool

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
            completed = subprocess.run(command, shell=True, capture_output=True, text=True)
        except OSError as exc:
            return ToolResult(success=False, error=f"execution de '{command}' impossible : {exc}")

        if completed.returncode != 0:
            error = completed.stderr.strip() or f"la commande a echoue avec le code {completed.returncode}"
            return ToolResult(success=False, error=error, return_code=completed.returncode)

        return ToolResult(
            success=True,
            output={"stdout": completed.stdout, "stderr": completed.stderr},
            return_code=0,
        )

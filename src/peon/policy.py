import re

from peon.models.action import Action
from peon.models.tool_spec import RiskLevel
from peon.models.verdict import Verdict, VerdictType
from peon.tool_registry import ToolNotFoundError, ToolRegistry

_RM_RF_PATTERN = re.compile(r"^rm -rf (\S+)$")
_UNSAFE_TARGETS = {".", "..", "/", "~"}
_UNSAFE_TARGET_PREFIXES = ("/", "~")


class PolicyEngine:
    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def evaluate(self, action: Action) -> Verdict:
        try:
            # Seule .spec est lue : le Policy Engine ne doit jamais executer de Tool.
            spec = self._registry.get(action.tool_name).spec
        except ToolNotFoundError:
            return Verdict(type=VerdictType.DENIED, reason=f"tool '{action.tool_name}' is not registered")

        command_verdict = self._check_dangerous_command(action)
        if command_verdict is not None:
            return command_verdict

        if spec.risk_level is RiskLevel.HIGH:
            return Verdict(
                type=VerdictType.REQUIRES_CONFIRMATION,
                reason=f"tool '{spec.name}' is high risk",
            )

        return Verdict(
            type=VerdictType.ALLOWED,
            reason=f"tool '{spec.name}' is risk level '{spec.risk_level.value}', no blocking rule triggered",
        )

    @staticmethod
    def _check_dangerous_command(action: Action) -> Verdict | None:
        command = action.arguments.get("command")
        if not isinstance(command, str):
            return None

        match = _RM_RF_PATTERN.match(command.strip())
        if match is None:
            return None

        target = match.group(1)
        if target in _UNSAFE_TARGETS or target.startswith(_UNSAFE_TARGET_PREFIXES):
            return Verdict(
                type=VerdictType.DENIED,
                reason=f"'{command}' targets an unscoped or absolute path, no safe rewrite exists",
            )

        safe_command = f"rm -rf -- ./{target}"
        return Verdict(
            type=VerdictType.REWRITE,
            reason=f"'{command}' is an untargeted recursive delete, a scoped alternative is available",
            suggested_action=Action(
                reasoning=f"Policy Engine suggestion: prefer '{safe_command}' over '{command}'.",
                tool_name=action.tool_name,
                arguments={**action.arguments, "command": safe_command},
                risk_level=RiskLevel.LOW,
            ),
        )

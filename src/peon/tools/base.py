from abc import ABC, abstractmethod
from typing import Any

from peon.models.tool_result import ToolResult
from peon.models.tool_spec import ToolSpec


class Tool(ABC):
    @property
    @abstractmethod
    def spec(self) -> ToolSpec: ...

    @abstractmethod
    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        # Echecs attendus (fichier absent, commande en erreur...) -> ToolResult(success=False),
        # jamais une exception : seul un bug d'implementation doit remonter.
        ...

from peon.models.tool_spec import ToolName, ToolSpec
from peon.tools.base import Tool


class ToolAlreadyRegisteredError(Exception):
    pass


class ToolNotFoundError(Exception):
    pass


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        name = tool.spec.name
        if name in self._tools:
            raise ToolAlreadyRegisteredError(f"tool '{name}' is already registered")
        self._tools[name] = tool

    def get(self, name: ToolName) -> Tool:
        try:
            return self._tools[name]
        except KeyError:
            raise ToolNotFoundError(f"tool '{name}' is not registered") from None

    def list_tools(self) -> list[ToolSpec]:
        return [tool.spec for tool in self._tools.values()]

    def exists(self, name: ToolName) -> bool:
        return name in self._tools

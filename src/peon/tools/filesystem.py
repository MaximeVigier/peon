from typing import Any

from peon.models.tool_result import ToolResult
from peon.models.tool_spec import RiskLevel, ToolSpec
from peon.tools.base import Tool
from peon.workspace import Workspace

_READ_FILE_SPEC = ToolSpec(
    name="read_file",
    description="Lit le contenu texte integral d'un fichier au chemin donne.",
    parameters_schema={
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    },
    risk_level=RiskLevel.LOW,
)

_LIST_DIRECTORY_SPEC = ToolSpec(
    name="list_directory",
    description="Liste les fichiers et dossiers d'un répertoire",
    parameters_schema={
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    },
    risk_level=RiskLevel.LOW,
)

_DELETE_FILE_SPEC = ToolSpec(
    name="delete_file",
    description="Supprime definitivement un fichier au chemin donne. Operation destructive et irreversible.",
    parameters_schema={
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    },
    risk_level=RiskLevel.HIGH,
)


def _missing_or_invalid_path_result() -> ToolResult:
    return ToolResult(success=False, error="l'argument 'path' est requis et doit etre une chaine non vide")


class ReadFileTool(Tool):
    def __init__(self, workspace: Workspace) -> None:
        self._workspace = workspace

    @property
    def spec(self) -> ToolSpec:
        return _READ_FILE_SPEC

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        path = arguments.get("path")
        if not isinstance(path, str) or not path.strip():
            return _missing_or_invalid_path_result()

        try:
            content = self._workspace.read_file(path)
        except OSError as exc:
            return ToolResult(success=False, error=f"lecture de '{path}' impossible : {exc}")
        except UnicodeDecodeError as exc:
            return ToolResult(success=False, error=f"'{path}' n'est pas un fichier texte valide (utf-8) : {exc}")

        return ToolResult(success=True, output=content)


class ListDirectoryTool(Tool):
    def __init__(self, workspace: Workspace) -> None:
        self._workspace = workspace

    @property
    def spec(self) -> ToolSpec:
        return _LIST_DIRECTORY_SPEC

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        path = arguments.get("path")
        if not isinstance(path, str) or not path.strip():
            return _missing_or_invalid_path_result()

        try:
            entries = sorted(self._workspace.list_directory(path))
        except OSError as exc:
            return ToolResult(success=False, error=f"lecture du dossier '{path}' impossible : {exc}")

        return ToolResult(success=True, output=entries)


class DeleteFileTool(Tool):
    def __init__(self, workspace: Workspace) -> None:
        self._workspace = workspace

    @property
    def spec(self) -> ToolSpec:
        return _DELETE_FILE_SPEC

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        path = arguments.get("path")
        if not isinstance(path, str) or not path.strip():
            return _missing_or_invalid_path_result()

        try:
            self._workspace.delete_file(path)
        except OSError as exc:
            return ToolResult(success=False, error=f"suppression de '{path}' impossible : {exc}")

        return ToolResult(success=True, output={"path": path, "deleted": True})

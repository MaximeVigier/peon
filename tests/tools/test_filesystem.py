from pathlib import Path
from typing import Any

import pytest

from peon.models.tool_spec import RiskLevel
from peon.tools.filesystem import DeleteFileTool, ListDirectoryTool, ReadFileTool
from peon.workspace import LocalWorkspace

_WORKSPACE = LocalWorkspace()


def test_spec_declares_read_file_with_required_path() -> None:
    spec = ReadFileTool(_WORKSPACE).spec

    assert spec.name == "read_file"
    assert spec.risk_level is RiskLevel.LOW
    assert spec.parameters_schema["required"] == ["path"]
    assert spec.parameters_schema["properties"]["path"]["type"] == "string"


def test_reading_an_existing_file_succeeds(tmp_path: Path) -> None:
    target = tmp_path / "note.txt"
    target.write_text("bonjour", encoding="utf-8")

    result = ReadFileTool(_WORKSPACE).execute({"path": str(target)})

    assert result.success is True
    assert result.output == "bonjour"
    assert result.error is None


def test_reading_a_missing_file_fails_without_raising(tmp_path: Path) -> None:
    missing = tmp_path / "absent.txt"

    result = ReadFileTool(_WORKSPACE).execute({"path": str(missing)})

    assert result.success is False
    assert result.error is not None


def test_missing_path_argument_fails_without_raising() -> None:
    result = ReadFileTool(_WORKSPACE).execute({})

    assert result.success is False
    assert result.error is not None


def test_non_string_path_argument_fails_without_raising() -> None:
    result = ReadFileTool(_WORKSPACE).execute({"path": 123})

    assert result.success is False
    assert result.error is not None


def test_reading_a_directory_fails_without_raising(tmp_path: Path) -> None:
    result = ReadFileTool(_WORKSPACE).execute({"path": str(tmp_path)})

    assert result.success is False
    assert result.error is not None


@pytest.mark.parametrize("arguments", [{}, {"path": 123}, {"path": None}, {"path": "   "}, {"path": ["a"]}])
def test_never_raises_for_any_invalid_arguments(arguments: dict[str, Any]) -> None:
    result = ReadFileTool(_WORKSPACE).execute(arguments)

    assert result.success is False


def test_list_directory_spec_declares_required_path() -> None:
    spec = ListDirectoryTool(_WORKSPACE).spec

    assert spec.name == "list_directory"
    assert spec.risk_level is RiskLevel.LOW
    assert spec.parameters_schema["required"] == ["path"]
    assert spec.parameters_schema["properties"]["path"]["type"] == "string"


def test_listing_an_existing_directory_succeeds(tmp_path: Path) -> None:
    (tmp_path / "b.txt").write_text("b", encoding="utf-8")
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "sub").mkdir()

    result = ListDirectoryTool(_WORKSPACE).execute({"path": str(tmp_path)})

    assert result.success is True
    assert result.output == ["a.txt", "b.txt", "sub"]
    assert result.error is None


def test_listing_an_empty_directory_succeeds_with_an_empty_list(tmp_path: Path) -> None:
    result = ListDirectoryTool(_WORKSPACE).execute({"path": str(tmp_path)})

    assert result.success is True
    assert result.output == []


def test_listing_a_missing_directory_fails_without_raising(tmp_path: Path) -> None:
    missing = tmp_path / "absent"

    result = ListDirectoryTool(_WORKSPACE).execute({"path": str(missing)})

    assert result.success is False
    assert result.error is not None


def test_list_directory_missing_path_argument_fails_without_raising() -> None:
    result = ListDirectoryTool(_WORKSPACE).execute({})

    assert result.success is False
    assert result.error is not None


def test_list_directory_non_string_path_argument_fails_without_raising() -> None:
    result = ListDirectoryTool(_WORKSPACE).execute({"path": 123})

    assert result.success is False
    assert result.error is not None


def test_listing_a_file_instead_of_a_directory_fails_without_raising(tmp_path: Path) -> None:
    target = tmp_path / "note.txt"
    target.write_text("bonjour", encoding="utf-8")

    result = ListDirectoryTool(_WORKSPACE).execute({"path": str(target)})

    assert result.success is False
    assert result.error is not None


@pytest.mark.parametrize("arguments", [{}, {"path": 123}, {"path": None}, {"path": "   "}, {"path": ["a"]}])
def test_list_directory_never_raises_for_any_invalid_arguments(arguments: dict[str, Any]) -> None:
    result = ListDirectoryTool(_WORKSPACE).execute(arguments)

    assert result.success is False


def test_delete_file_spec_declares_high_risk_with_required_path() -> None:
    spec = DeleteFileTool(_WORKSPACE).spec

    assert spec.name == "delete_file"
    assert spec.risk_level is RiskLevel.HIGH
    assert spec.parameters_schema["required"] == ["path"]
    assert spec.parameters_schema["properties"]["path"]["type"] == "string"


def test_deleting_an_existing_file_succeeds(tmp_path: Path) -> None:
    target = tmp_path / "note.txt"
    target.write_text("bonjour", encoding="utf-8")

    result = DeleteFileTool(_WORKSPACE).execute({"path": str(target)})

    assert result.success is True
    assert result.error is None
    assert result.output == {"path": str(target), "deleted": True}
    assert not target.exists()


def test_deleting_a_missing_file_fails_without_raising(tmp_path: Path) -> None:
    missing = tmp_path / "absent.txt"

    result = DeleteFileTool(_WORKSPACE).execute({"path": str(missing)})

    assert result.success is False
    assert result.error is not None


def test_delete_file_missing_path_argument_fails_without_raising() -> None:
    result = DeleteFileTool(_WORKSPACE).execute({})

    assert result.success is False
    assert result.error is not None


def test_delete_file_non_string_path_argument_fails_without_raising() -> None:
    result = DeleteFileTool(_WORKSPACE).execute({"path": 123})

    assert result.success is False
    assert result.error is not None


def test_deleting_a_directory_fails_without_raising(tmp_path: Path) -> None:
    # Path.unlink() sur un dossier leve IsADirectoryError (POSIX) ou
    # PermissionError (Windows) : les deux sont des sous-classes d'OSError,
    # deja capturees par DeleteFileTool.execute() comme tout autre echec attendu.
    result = DeleteFileTool(_WORKSPACE).execute({"path": str(tmp_path)})

    assert result.success is False
    assert result.error is not None


@pytest.mark.parametrize("arguments", [{}, {"path": 123}, {"path": None}, {"path": "   "}, {"path": ["a"]}])
def test_delete_file_never_raises_for_any_invalid_arguments(arguments: dict[str, Any]) -> None:
    result = DeleteFileTool(_WORKSPACE).execute(arguments)

    assert result.success is False

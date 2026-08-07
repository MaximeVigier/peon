from pathlib import Path
from typing import Any

import pytest

from peon.models.tool_spec import RiskLevel
from peon.tools.filesystem import ListDirectoryTool, ReadFileTool


def test_spec_declares_read_file_with_required_path() -> None:
    spec = ReadFileTool().spec

    assert spec.name == "read_file"
    assert spec.risk_level is RiskLevel.LOW
    assert spec.parameters_schema["required"] == ["path"]
    assert spec.parameters_schema["properties"]["path"]["type"] == "string"


def test_reading_an_existing_file_succeeds(tmp_path: Path) -> None:
    target = tmp_path / "note.txt"
    target.write_text("bonjour", encoding="utf-8")

    result = ReadFileTool().execute({"path": str(target)})

    assert result.success is True
    assert result.output == "bonjour"
    assert result.error is None


def test_reading_a_missing_file_fails_without_raising(tmp_path: Path) -> None:
    missing = tmp_path / "absent.txt"

    result = ReadFileTool().execute({"path": str(missing)})

    assert result.success is False
    assert result.error is not None


def test_missing_path_argument_fails_without_raising() -> None:
    result = ReadFileTool().execute({})

    assert result.success is False
    assert result.error is not None


def test_non_string_path_argument_fails_without_raising() -> None:
    result = ReadFileTool().execute({"path": 123})

    assert result.success is False
    assert result.error is not None


def test_reading_a_directory_fails_without_raising(tmp_path: Path) -> None:
    result = ReadFileTool().execute({"path": str(tmp_path)})

    assert result.success is False
    assert result.error is not None


@pytest.mark.parametrize("arguments", [{}, {"path": 123}, {"path": None}, {"path": "   "}, {"path": ["a"]}])
def test_never_raises_for_any_invalid_arguments(arguments: dict[str, Any]) -> None:
    result = ReadFileTool().execute(arguments)

    assert result.success is False


def test_list_directory_spec_declares_required_path() -> None:
    spec = ListDirectoryTool().spec

    assert spec.name == "list_directory"
    assert spec.risk_level is RiskLevel.LOW
    assert spec.parameters_schema["required"] == ["path"]
    assert spec.parameters_schema["properties"]["path"]["type"] == "string"


def test_listing_an_existing_directory_succeeds(tmp_path: Path) -> None:
    (tmp_path / "b.txt").write_text("b", encoding="utf-8")
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "sub").mkdir()

    result = ListDirectoryTool().execute({"path": str(tmp_path)})

    assert result.success is True
    assert result.output == ["a.txt", "b.txt", "sub"]
    assert result.error is None


def test_listing_an_empty_directory_succeeds_with_an_empty_list(tmp_path: Path) -> None:
    result = ListDirectoryTool().execute({"path": str(tmp_path)})

    assert result.success is True
    assert result.output == []


def test_listing_a_missing_directory_fails_without_raising(tmp_path: Path) -> None:
    missing = tmp_path / "absent"

    result = ListDirectoryTool().execute({"path": str(missing)})

    assert result.success is False
    assert result.error is not None


def test_list_directory_missing_path_argument_fails_without_raising() -> None:
    result = ListDirectoryTool().execute({})

    assert result.success is False
    assert result.error is not None


def test_list_directory_non_string_path_argument_fails_without_raising() -> None:
    result = ListDirectoryTool().execute({"path": 123})

    assert result.success is False
    assert result.error is not None


def test_listing_a_file_instead_of_a_directory_fails_without_raising(tmp_path: Path) -> None:
    target = tmp_path / "note.txt"
    target.write_text("bonjour", encoding="utf-8")

    result = ListDirectoryTool().execute({"path": str(target)})

    assert result.success is False
    assert result.error is not None


@pytest.mark.parametrize("arguments", [{}, {"path": 123}, {"path": None}, {"path": "   "}, {"path": ["a"]}])
def test_list_directory_never_raises_for_any_invalid_arguments(arguments: dict[str, Any]) -> None:
    result = ListDirectoryTool().execute(arguments)

    assert result.success is False

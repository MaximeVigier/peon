from pathlib import Path

import pytest

from peon.workspace import CommandResult, LocalWorkspace


def test_read_file_returns_its_content(tmp_path: Path) -> None:
    target = tmp_path / "note.txt"
    target.write_text("bonjour", encoding="utf-8")

    content = LocalWorkspace().read_file(str(target))

    assert content == "bonjour"


def test_read_file_propagates_os_error_for_a_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "absent.txt"

    with pytest.raises(OSError):
        LocalWorkspace().read_file(str(missing))


def test_list_directory_returns_entry_names(tmp_path: Path) -> None:
    (tmp_path / "b.txt").write_text("b", encoding="utf-8")
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "sub").mkdir()

    entries = LocalWorkspace().list_directory(str(tmp_path))

    assert set(entries) == {"a.txt", "b.txt", "sub"}


def test_list_directory_propagates_os_error_for_a_missing_directory(tmp_path: Path) -> None:
    missing = tmp_path / "absent"

    with pytest.raises(OSError):
        LocalWorkspace().list_directory(str(missing))


def test_run_command_returns_stdout_and_a_zero_return_code_on_success() -> None:
    result = LocalWorkspace().run_command("echo hello")

    assert isinstance(result, CommandResult)
    assert result.return_code == 0
    assert result.stdout.strip() == "hello"


def test_run_command_propagates_a_non_zero_return_code_without_raising() -> None:
    result = LocalWorkspace().run_command("exit 3")

    assert result.return_code == 3

import sys
from pathlib import Path

import pytest

from peon.workspace import CommandResult, LocalWorkspace


def _python_command(tmp_path: Path, script_body: str) -> str:
    """Construit une commande shell portable qui execute un script Python.

    Evite de dependre d'une commande Windows/Unix particuliere pour produire
    des octets precis sur stdout/stderr : le script est le mecanisme
    subprocess minimal et portable requis pour ces tests.
    """
    script = tmp_path / "workspace_probe.py"
    script.write_text(script_body, encoding="utf-8")
    return f'"{sys.executable}" "{script}"'


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


def test_delete_file_removes_the_file_from_disk(tmp_path: Path) -> None:
    target = tmp_path / "note.txt"
    target.write_text("bonjour", encoding="utf-8")

    LocalWorkspace().delete_file(str(target))

    assert not target.exists()


def test_delete_file_propagates_os_error_for_a_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "absent.txt"

    with pytest.raises(OSError):
        LocalWorkspace().delete_file(str(missing))


def test_run_command_returns_stdout_and_a_zero_return_code_on_success() -> None:
    result = LocalWorkspace().run_command("echo hello")

    assert isinstance(result, CommandResult)
    assert result.return_code == 0
    assert result.stdout.strip() == "hello"


def test_run_command_propagates_a_non_zero_return_code_without_raising() -> None:
    result = LocalWorkspace().run_command("exit 3")

    assert result.return_code == 3


def test_run_command_decodes_normal_utf8_stdout_and_stderr_correctly(tmp_path: Path) -> None:
    command = _python_command(
        tmp_path,
        "import sys\n"
        "sys.stdout.buffer.write('bonjour éàü'.encode('utf-8'))\n"
        "sys.stderr.buffer.write('avertissement éàü'.encode('utf-8'))\n",
    )

    result = LocalWorkspace().run_command(command)

    assert result.stdout == "bonjour éàü"
    assert result.stderr == "avertissement éàü"
    assert result.return_code == 0


def test_run_command_replaces_invalid_utf8_bytes_in_stdout_without_raising(tmp_path: Path) -> None:
    command = _python_command(
        tmp_path,
        "import sys\n"
        "sys.stdout.buffer.write(bytes([0x68, 0x65, 0xff, 0x6c, 0x6c, 0x6f]))\n",
    )

    result = LocalWorkspace().run_command(command)

    assert isinstance(result.stdout, str)
    assert "�" in result.stdout
    assert result.return_code == 0


def test_run_command_replaces_invalid_utf8_bytes_in_stderr_without_raising(tmp_path: Path) -> None:
    command = _python_command(
        tmp_path,
        "import sys\n"
        "sys.stderr.buffer.write(bytes([0x65, 0x72, 0xff, 0x72]))\n",
    )

    result = LocalWorkspace().run_command(command)

    assert isinstance(result.stderr, str)
    assert "�" in result.stderr
    assert result.return_code == 0


def test_run_command_handles_invalid_utf8_in_stdout_and_stderr_simultaneously_and_preserves_return_code(
    tmp_path: Path,
) -> None:
    command = _python_command(
        tmp_path,
        "import sys\n"
        "sys.stdout.buffer.write(bytes([0xff]))\n"
        "sys.stderr.buffer.write(bytes([0xff]))\n"
        "sys.exit(7)\n",
    )

    result = LocalWorkspace().run_command(command)

    assert isinstance(result.stdout, str)
    assert isinstance(result.stderr, str)
    assert "�" in result.stdout
    assert "�" in result.stderr
    assert result.return_code == 7

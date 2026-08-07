from typer.testing import CliRunner

from peon import __version__
from peon.cli import app

runner = CliRunner()


def test_version_flag_prints_version_and_exits() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_no_args_shows_help() -> None:
    result = runner.invoke(app, [])

    assert "Usage:" in result.output
    assert "Peon" in result.output

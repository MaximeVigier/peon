import typer
from rich.console import Console

from peon import __version__

app = typer.Typer(
    name="peon",
    help="Peon - agent oriente evenements, modulaire et independant du modele utilise.",
    no_args_is_help=True,
)
console = Console()


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"peon {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Afficher la version et quitter.",
    ),
) -> None:
    """Peon - agent oriente evenements."""


if __name__ == "__main__":
    app()

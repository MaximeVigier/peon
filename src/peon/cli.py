"""CLI minimale (Phase 5) : `peon run "<goal>"` et `peon resume`.

Ne reimplemente jamais la boucle ReAct : chaque commande construit un
`Runtime` via `build_runtime()` (composition.py, deja existant) puis pilote
uniquement son API publique (`run`, `resume_confirmation`, `resume_mission`,
`save_checkpoint`, `pending_confirmation`). Aucune connaissance de
`Reasoner`/`PolicyEngine`/`Executor`/`StateMachine` ici : ce sont des details
internes du Runtime.

`_build_llm`/`_build_tools` sont des points d'injection minimaux (pas un
framework de DI) : ils permettent aux tests de remplacer le LLM/les Tools
reels par des doubles sans reseau ni filesystem, exactement comme les autres
modules de tests de ce projet le font pour Runtime.

Stockage du process CLI (`_storage`, `InMemoryStorage`) : partage entre `run`
et `resume` pour que `peon resume` puisse reellement retrouver un Checkpoint
sauvegarde plus tot -- mais seulement **dans le meme process** (aucun backend
disque, voir README.md). `peon run` gerant les confirmations de facon
entierement interactive et synchrone, ce Checkpoint n'est utile en pratique
qu'en cas d'interruption pendant l'attente de confirmation, ou pour les
tests qui simulent ce scenario.
"""

import typer
from rich.console import Console

from peon import __version__
from peon.composition import build_runtime
from peon.llm import LLM
from peon.models.confirmation import ConfirmationRequest, ConfirmationResponse
from peon.models.mission import Mission, MissionStatus
from peon.providers.ollama import OllamaConfig, OllamaLLM
from peon.runtime import Runtime
from peon.storage import InMemoryStorage, Storage
from peon.tools.base import Tool
from peon.tools.filesystem import ListDirectoryTool, ReadFileTool
from peon.tools.shell import ShellTool
from peon.workspace import LocalWorkspace

app = typer.Typer(
    name="peon",
    help="Peon - agent oriente evenements, modulaire et independant du modele utilise.",
    no_args_is_help=True,
)
console = Console()

# Valeurs simples et explicites (pas de .env, pas de systeme de config) :
# seul fournisseur LLM concret disponible aujourd'hui est OllamaLLM.
_DEFAULT_MODEL = "llama3.1"
_DEFAULT_BASE_URL = "http://localhost:11434"
_DEFAULT_TIMEOUT_SECONDS = 60.0

# Storage du process CLI : voir docstring du module pour la portee reelle.
_storage: Storage = InMemoryStorage()


def _build_llm(model: str, base_url: str, timeout_seconds: float) -> LLM:
    return OllamaLLM(OllamaConfig(model=model, base_url=base_url, timeout_seconds=timeout_seconds))


def _build_tools() -> list[Tool]:
    workspace = LocalWorkspace()
    return [ReadFileTool(workspace), ListDirectoryTool(workspace), ShellTool(workspace)]


def _build_cli_runtime(model: str, base_url: str, timeout_seconds: float) -> Runtime:
    llm = _build_llm(model, base_url, timeout_seconds)
    return build_runtime(llm=llm, tools=_build_tools(), storage=_storage)


def _ask_confirmation(request: ConfirmationRequest) -> ConfirmationResponse:
    console.print()
    console.print("[bold yellow]Confirmation required[/bold yellow]")
    console.print(f"Tool: {request.action.tool_name}")
    console.print(f"Arguments: {request.action.arguments}")
    console.print(f"Reason: {request.reason}")
    granted = typer.confirm("Allow?", default=False)
    return ConfirmationResponse(request_id=request.id, granted=granted)


def _drive_to_completion(runtime: Runtime, mission: Mission) -> Mission:
    while mission.status is MissionStatus.AWAITING_CONFIRMATION:
        request = runtime.pending_confirmation
        assert request is not None
        # Point pertinent documente par Runtime.save_checkpoint() lui-meme :
        # juste apres que run()/resume_confirmation() ait rendu la main en
        # AWAITING_CONFIRMATION.
        runtime.save_checkpoint(mission)
        response = _ask_confirmation(request)
        mission = runtime.resume_confirmation(mission, response)
    return mission


def _print_final_status(mission: Mission) -> None:
    console.print()
    if mission.status is MissionStatus.SUCCEEDED:
        console.print(f"[bold green]Mission succeeded[/bold green] after {mission.iteration_count} iteration(s).")
    elif mission.status is MissionStatus.FAILED:
        console.print(f"[bold red]Mission failed[/bold red] after {mission.iteration_count} iteration(s).")
    elif mission.status is MissionStatus.MAX_ITERATIONS:
        console.print(f"[bold red]Max iterations reached[/bold red] ({mission.iteration_count} iteration(s)).")
    else:
        console.print(f"Mission ended with status: {mission.status.value}")


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


@app.command()
def run(
    goal: str = typer.Argument(..., help="Objectif a confier a la mission."),
    model: str = typer.Option(_DEFAULT_MODEL, help="Modele Ollama a utiliser."),
    base_url: str = typer.Option(_DEFAULT_BASE_URL, help="URL de base de l'API Ollama."),
    timeout_seconds: float = typer.Option(_DEFAULT_TIMEOUT_SECONDS, help="Timeout HTTP pour l'appel Ollama."),
    max_iterations: int | None = typer.Option(None, help="Nombre maximal d'iterations de raisonnement."),
) -> None:
    """Lance une mission jusqu'a son etat final, confirmations gerees en direct."""
    runtime = _build_cli_runtime(model, base_url, timeout_seconds)
    mission = runtime.run(goal, max_iterations=max_iterations)
    mission = _drive_to_completion(runtime, mission)
    _print_final_status(mission)


@app.command()
def resume(
    model: str = typer.Option(_DEFAULT_MODEL, help="Modele Ollama a utiliser."),
    base_url: str = typer.Option(_DEFAULT_BASE_URL, help="URL de base de l'API Ollama."),
    timeout_seconds: float = typer.Option(_DEFAULT_TIMEOUT_SECONDS, help="Timeout HTTP pour l'appel Ollama."),
) -> None:
    """Reprend la mission depuis le dernier Checkpoint disponible sur ce process."""
    checkpoint = _storage.load_checkpoint()
    if checkpoint is None:
        console.print("[bold red]No checkpoint found.[/bold red] Nothing to resume.")
        raise typer.Exit(code=1)

    runtime = _build_cli_runtime(model, base_url, timeout_seconds)
    mission = runtime.resume_mission(checkpoint)

    if runtime.pending_confirmation is not None:
        response = _ask_confirmation(runtime.pending_confirmation)
        mission = runtime.resume_confirmation(mission, response)
        mission = _drive_to_completion(runtime, mission)
    else:
        console.print(f"Checkpoint loaded, no pending confirmation (status: {mission.status.value}).")

    _print_final_status(mission)


if __name__ == "__main__":
    app()

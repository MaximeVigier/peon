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

Stockage du process CLI (`_storage`, `FileStorage`) : partage entre `run` et
`resume`, persiste le dernier `Checkpoint` et l'historique de l'`EventLog`
sur disque a un emplacement deterministe (`~/.peon/checkpoint.json` + son
fichier d'evenements associe, voir `_DEFAULT_CHECKPOINT_PATH`) -- `peon
resume` retrouve donc un Checkpoint *et* l'historique des Observations
produites avant l'arret, sauvegardes par une invocation `peon run`
anterieure, meme apres un redemarrage complet du process CLI :
`_drive_to_completion` persiste les evenements a chaque `save_checkpoint()`,
et `resume` recharge l'EventLog (`Runtime.load_event_log`) avant de
reconstruire le Runtime, pour que le Context reconstruit apres reprise soit
celui qu'aurait eu le process d'origine au meme point. `peon run` gerant les
confirmations de facon entierement interactive et synchrone, ce mecanisme
est utile en pratique surtout en cas d'interruption pendant l'attente de
confirmation.
"""

from pathlib import Path

import typer
from pydantic import ValidationError
from rich.console import Console

from peon import __version__
from peon.composition import build_runtime
from peon.event_log import EventLog
from peon.llm import LLM
from peon.models.confirmation import ConfirmationRequest, ConfirmationResponse
from peon.models.mission import Mission, MissionStatus
from peon.providers.ollama import OllamaConfig, OllamaLLM
from peon.runtime import Runtime
from peon.storage import CorruptedCheckpointError, CorruptedEventLogError, FileStorage, Storage
from peon.tools.base import Tool
from peon.tools.filesystem import DeleteFileTool, ListDirectoryTool, ReadFileTool
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

# Emplacement deterministe du Checkpoint, mono-mission (un seul fichier,
# remplace a chaque save_checkpoint()) : pas de config, pas de .env -- voir
# docstring du module pour la portee reelle.
_DEFAULT_CHECKPOINT_PATH = Path.home() / ".peon" / "checkpoint.json"

# Storage du process CLI : voir docstring du module pour la portee reelle.
_storage: Storage = FileStorage(_DEFAULT_CHECKPOINT_PATH)


def _build_llm(model: str, base_url: str, timeout_seconds: float) -> LLM:
    return OllamaLLM(OllamaConfig(model=model, base_url=base_url, timeout_seconds=timeout_seconds))


def _build_tools() -> list[Tool]:
    workspace = LocalWorkspace()
    return [ReadFileTool(workspace), ListDirectoryTool(workspace), ShellTool(workspace), DeleteFileTool(workspace)]


def _normalize_workspace_root(workspace_root: Path | None) -> Path | None:
    # Normalisation deterministe a la frontiere CLI (un chemin relatif est
    # ancre au repertoire courant au moment de l'invocation, pas plus tard) ;
    # PathRestrictionRule (guardrails.py) resout de nouveau ce chemin, cet
    # appel est donc idempotent, jamais une deuxieme source de verite.
    return workspace_root.resolve() if workspace_root is not None else None


def _build_cli_runtime(
    model: str,
    base_url: str,
    timeout_seconds: float,
    workspace_root: Path | None = None,
    event_log: EventLog | None = None,
) -> Runtime:
    llm = _build_llm(model, base_url, timeout_seconds)
    return build_runtime(
        llm=llm,
        tools=_build_tools(),
        storage=_storage,
        workspace_root=_normalize_workspace_root(workspace_root),
        event_log=event_log,
    )


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
        # AWAITING_CONFIRMATION. persist_events() est appele dans la foulee
        # pour que le Checkpoint ne soit jamais sauvegarde sans l'historique
        # (Observations) qui lui correspond : un `peon resume` ulterieur
        # retrouve alors le meme Context que celui qu'aurait eu ce process.
        runtime.save_checkpoint(mission)
        runtime.persist_events()
        response = _ask_confirmation(request)
        mission = runtime.resume_confirmation(mission, response)
    # Persiste aussi l'etat final (mission terminale, plus de confirmation en
    # attente), meme si la boucle ci-dessus n'a jamais tourne (mission finie
    # sans jamais faire de pause) : sans cet appel, le Checkpoint sur disque
    # reste bloque sur la derniere ConfirmationRequest en attente vue par
    # save_checkpoint() ci-dessus, deja resolue. Un `peon resume` ulterieur la
    # retrouverait telle quelle et re-executerait l'Action correspondante une
    # deuxieme fois (voir test_resuming_after_a_mission_already_succeeded_...
    # dans test_cli.py) - violation de l'invariant "exactement une execution".
    runtime.save_checkpoint(mission)
    runtime.persist_events()
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
    workspace_root: Path | None = typer.Option(
        None,
        "--workspace-root",
        help="Racine autorisee pour les arguments 'path' des Tools (PathRestrictionRule). "
        "Non fourni = aucune restriction, comportement historique.",
    ),
) -> None:
    """Lance une mission jusqu'a son etat final, confirmations gerees en direct."""
    runtime = _build_cli_runtime(model, base_url, timeout_seconds, workspace_root)
    # Mission (models/mission.py) valide goal/max_iterations via Pydantic (goal
    # non vide, max_iterations >= 1) : une valeur CLI invalide levait jusque-la
    # une ValidationError brute au lieu d'un message et d'un exit_code=1
    # propres, contrairement au traitement deja en place pour les erreurs
    # Storage ci-dessous (`resume`). Meme convention ici : message explicite,
    # sortie propre.
    try:
        mission = runtime.run(goal, max_iterations=max_iterations)
    except ValidationError as exc:
        console.print(f"[bold red]Invalid mission parameters.[/bold red] {exc}")
        raise typer.Exit(code=1) from exc
    mission = _drive_to_completion(runtime, mission)
    _print_final_status(mission)


@app.command()
def resume(
    model: str = typer.Option(_DEFAULT_MODEL, help="Modele Ollama a utiliser."),
    base_url: str = typer.Option(_DEFAULT_BASE_URL, help="URL de base de l'API Ollama."),
    timeout_seconds: float = typer.Option(_DEFAULT_TIMEOUT_SECONDS, help="Timeout HTTP pour l'appel Ollama."),
    workspace_root: Path | None = typer.Option(
        None,
        "--workspace-root",
        help="Racine autorisee pour les arguments 'path' des Tools (PathRestrictionRule). "
        "A fournir de nouveau ici : non persistee dans le Checkpoint, elle ne survit pas "
        "a un nouveau process CLI. Non fourni = aucune restriction, comportement historique.",
    ),
) -> None:
    """Reprend la mission depuis le dernier Checkpoint disponible sur ce process."""
    try:
        checkpoint = _storage.load_checkpoint()
    except CorruptedCheckpointError as exc:
        console.print(f"[bold red]Checkpoint file is corrupted.[/bold red] {exc}")
        raise typer.Exit(code=1) from exc
    if checkpoint is None:
        console.print("[bold red]No checkpoint found.[/bold red] Nothing to resume.")
        raise typer.Exit(code=1)

    # Recharge l'historique persiste (vide si aucun evenement n'a jamais ete
    # persiste, ex. checkpoint issu d'une version anterieure) avant de
    # construire le Runtime : resume_mission() reconstruit alors
    # self._observations depuis ce meme EventLog (voir Runtime.resume_mission).
    try:
        event_log = Runtime.load_event_log(_storage)
    except CorruptedEventLogError as exc:
        console.print(f"[bold red]Event log file is corrupted.[/bold red] {exc}")
        raise typer.Exit(code=1) from exc
    runtime = _build_cli_runtime(model, base_url, timeout_seconds, workspace_root, event_log=event_log)
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

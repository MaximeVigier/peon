"""Tests de la CLI minimale (Phase 5) : `peon run`, `peon resume`.

`_build_llm`/`_build_tools` sont les points d'injection exposes par
`peon.cli` (voir sa docstring) : chaque test les monkeypatch pour remplacer
le LLM/les Tools reels par des doubles locaux (meme convention que le reste
du projet, ex. `_ScriptedLLM` dans test_composition.py), sans reseau ni
filesystem reel. `_reset_storage` (fixture autouse) evite toute fuite d'etat
entre tests via le singleton `peon.cli._storage`.
"""

import ast
import inspect
import json
from http.server import HTTPServer
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from conftest import FakeOllamaHandler
from peon import __version__, cli
from peon.cli import app
from peon.llm import LLM
from peon.models.action import Action
from peon.models.checkpoint import Checkpoint
from peon.models.confirmation import ConfirmationRequest
from peon.models.mission import Mission, MissionStatus
from peon.models.tool_result import ToolResult
from peon.models.tool_spec import RiskLevel, ToolSpec
from peon.providers.ollama import OllamaLLM
from peon.storage import FileStorage, InMemoryStorage, Storage
from peon.tools.base import Tool

runner = CliRunner()


class _ScriptedLLM(LLM):
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)

    def generate(self, messages: list[dict[str, str]]) -> str:
        return self._responses.pop(0)


_PATH_SCHEMA = {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}


class _StubTool(Tool):
    def __init__(
        self,
        name: str,
        risk_level: RiskLevel,
        result: ToolResult | None = None,
        parameters_schema: dict[str, Any] | None = None,
    ) -> None:
        self._spec = ToolSpec(
            name=name,
            description=f"Outil {name}",
            parameters_schema=parameters_schema or {"type": "object", "properties": {}},
            risk_level=risk_level,
        )
        self._result = result if result is not None else ToolResult(success=True, output="ok")
        self.call_count = 0

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        self.call_count += 1
        return self._result


def _finish_response(outcome: str = "success", summary: str = "termine") -> str:
    return f'{{"kind": "finished", "outcome": "{outcome}", "summary": "{summary}", "confidence": 1.0}}'


def _action_response(tool_name: str, **arguments: Any) -> str:
    return json.dumps({"kind": "action", "reasoning": "x", "tool_name": tool_name, "arguments": arguments})


@pytest.fixture(autouse=True)
def _reset_storage() -> None:
    cli._storage = InMemoryStorage()


def _use_llm(monkeypatch: pytest.MonkeyPatch, responses: list[str]) -> _ScriptedLLM:
    llm = _ScriptedLLM(responses)
    monkeypatch.setattr(cli, "_build_llm", lambda *args, **kwargs: llm)
    return llm


def _use_tools(monkeypatch: pytest.MonkeyPatch, tools: list[Tool]) -> None:
    monkeypatch.setattr(cli, "_build_tools", lambda: tools)


# --- --version continue de fonctionner ----------------------------------------


def test_version_flag_prints_version_and_exits() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_no_args_shows_help() -> None:
    result = runner.invoke(app, [])

    assert "Usage:" in result.output
    assert "Peon" in result.output


# --- peon run : chemin nominal --------------------------------------------------


def test_run_accepts_a_goal_and_succeeds_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_llm(monkeypatch, [_finish_response("success", "rien a faire")])
    _use_tools(monkeypatch, [])

    result = runner.invoke(app, ["run", "verifier que la CLI demarre"])

    assert result.exit_code == 0
    assert "Mission succeeded" in result.output


def test_run_nominal_path_with_an_action_then_finish(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = _StubTool("read_file", RiskLevel.LOW, ToolResult(success=True, output="contenu"))
    _use_llm(monkeypatch, [_action_response("read_file", path="README.md"), _finish_response("success", "lu")])
    _use_tools(monkeypatch, [tool])

    result = runner.invoke(app, ["run", "lire le readme"])

    assert result.exit_code == 0
    assert tool.call_count == 1
    assert "Mission succeeded" in result.output


# --- peon run : goal invalide -----------------------------------------------------


def test_run_with_a_blank_goal_fails_cleanly_instead_of_a_raw_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regression : `Mission(goal=...)` (models/mission.py) rejette un goal
    # vide/blanc via un validator Pydantic (ValueError -> ValidationError),
    # mais rien ne capturait cette exception cote CLI avant `runtime.run()` :
    # `peon run "   "` laissait fuir une trace Python brute (ValidationError)
    # au lieu d'un message et d'un exit_code=1 propres, contrairement au
    # traitement deja en place pour les erreurs Storage (voir
    # test_resume_with_a_corrupted_checkpoint_file_fails_cleanly ci-dessous).
    _use_llm(monkeypatch, [])
    _use_tools(monkeypatch, [])

    result = runner.invoke(app, ["run", "   "])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)  # sortie propre, pas une trace Python non geree
    assert "goal" in result.output.lower()


def test_run_with_an_invalid_max_iterations_fails_cleanly_instead_of_a_raw_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Meme classe de bug que ci-dessus, via un deuxieme champ valide par
    # Mission (max_iterations >= 1) mais jamais par la CLI elle-meme :
    # `--max-iterations 0` levait la meme ValidationError brute.
    _use_llm(monkeypatch, [])
    _use_tools(monkeypatch, [])

    result = runner.invoke(app, ["run", "objectif valide", "--max-iterations", "0"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert "max_iterations" in result.output.lower() or "max-iterations" in result.output.lower()


# --- peon run : configuration LLM invalide (--model/--base-url/--timeout-seconds) --


def test_run_with_an_invalid_timeout_seconds_fails_cleanly_instead_of_a_raw_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Meme classe de bug que test_run_with_a_blank_goal_..., mais sur la
    # configuration Ollama : `_build_cli_runtime()` (donc `OllamaConfig`, qui
    # exige timeout_seconds > 0) etait appele hors du bloc try/except
    # ValidationError de `run()`, qui ne protege que `runtime.run(goal, ...)`.
    # `--timeout-seconds 0` laissait donc fuir une trace Python brute.
    _use_tools(monkeypatch, [])

    result = runner.invoke(app, ["run", "objectif valide", "--timeout-seconds", "0"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)  # sortie propre, pas une trace Python non geree
    assert "timeout_seconds" in result.output.lower()


def test_run_with_an_empty_model_fails_cleanly_instead_of_a_raw_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_tools(monkeypatch, [])

    result = runner.invoke(app, ["run", "objectif valide", "--model", ""])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert "model" in result.output.lower()


# --- peon run : confirmation ------------------------------------------------------


def test_run_confirmation_granted_executes_the_action_and_resumes(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = _StubTool("run_command", RiskLevel.HIGH, ToolResult(success=True, output="ok"))
    _use_llm(
        monkeypatch,
        [_action_response("run_command", command="echo hi"), _finish_response("success", "commande executee")],
    )
    _use_tools(monkeypatch, [tool])

    result = runner.invoke(app, ["run", "executer une commande"], input="y\n")

    assert result.exit_code == 0
    assert "Confirmation required" in result.output
    assert "Tool: run_command" in result.output
    assert tool.call_count == 1
    assert "Mission succeeded" in result.output


def test_run_confirmation_denied_keeps_existing_runtime_behavior(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = _StubTool("run_command", RiskLevel.HIGH)
    _use_llm(
        monkeypatch,
        [_action_response("run_command", command="echo hi"), _finish_response("failure", "annule")],
    )
    _use_tools(monkeypatch, [tool])

    result = runner.invoke(app, ["run", "executer une commande"], input="n\n")

    assert result.exit_code == 0
    assert "Confirmation required" in result.output
    assert tool.call_count == 0
    assert "Mission failed" in result.output


# --- peon run : delete_file, seul Tool HIGH reel de la CLI --------------------
#
# `_build_tools()` n'est pas monkeypatche ici (contrairement au reste de ce
# module) : le but est de verifier le wiring CLI reel (`cli._build_tools()`,
# donc `DeleteFileTool(LocalWorkspace())`), pas un double.


def test_run_with_real_delete_file_tool_requires_confirmation_and_deletes_on_grant(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "note.txt"
    target.write_text("bonjour", encoding="utf-8")
    _use_llm(
        monkeypatch,
        [_action_response("delete_file", path=str(target)), _finish_response("success", "fichier supprime")],
    )

    result = runner.invoke(app, ["run", "supprimer le fichier note.txt"], input="y\n")

    assert result.exit_code == 0
    assert "Confirmation required" in result.output
    assert "Tool: delete_file" in result.output
    assert not target.exists()
    assert "Mission succeeded" in result.output


def test_run_with_real_delete_file_tool_leaves_the_file_untouched_on_denial(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "note.txt"
    target.write_text("bonjour", encoding="utf-8")
    _use_llm(
        monkeypatch,
        [_action_response("delete_file", path=str(target)), _finish_response("failure", "annule")],
    )

    result = runner.invoke(app, ["run", "supprimer le fichier note.txt"], input="n\n")

    assert result.exit_code == 0
    assert "Confirmation required" in result.output
    assert target.exists()
    assert "Mission failed" in result.output


# --- peon run : --workspace-root ------------------------------------------------


def test_run_without_workspace_root_does_not_restrict_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Regression : comportement historique inchange quand l'option n'est pas
    # fournie, meme pour un chemin qui vivrait "n'importe ou".
    tool = _StubTool("read_file", RiskLevel.LOW, parameters_schema=_PATH_SCHEMA)
    _use_llm(
        monkeypatch,
        [_action_response("read_file", path=str(tmp_path / "anywhere.txt")), _finish_response("success", "lu")],
    )
    _use_tools(monkeypatch, [tool])

    result = runner.invoke(app, ["run", "lire un fichier"])

    assert result.exit_code == 0
    assert tool.call_count == 1
    assert "Mission succeeded" in result.output


def test_run_with_workspace_root_allows_a_path_inside_the_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tool = _StubTool("read_file", RiskLevel.LOW, parameters_schema=_PATH_SCHEMA)
    _use_llm(
        monkeypatch,
        [_action_response("read_file", path=str(tmp_path / "note.txt")), _finish_response("success", "lu")],
    )
    _use_tools(monkeypatch, [tool])

    result = runner.invoke(app, ["run", "lire une note", "--workspace-root", str(tmp_path)])

    assert result.exit_code == 0
    assert tool.call_count == 1
    assert "Mission succeeded" in result.output


def test_run_with_workspace_root_denies_a_path_outside_the_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    outside_path = str(tmp_path / "outside.txt")

    tool = _StubTool("read_file", RiskLevel.LOW, parameters_schema=_PATH_SCHEMA)
    _use_llm(
        monkeypatch,
        [_action_response("read_file", path=outside_path), _finish_response("success", "termine")],
    )
    _use_tools(monkeypatch, [tool])

    result = runner.invoke(app, ["run", "lire un fichier hors racine", "--workspace-root", str(workspace_root)])

    assert result.exit_code == 0
    assert tool.call_count == 0  # refuse par PathRestrictionRule, jamais execute
    assert "Mission succeeded" in result.output


# --- peon resume -------------------------------------------------------------------


def test_resume_without_checkpoint_fails_cleanly() -> None:
    result = runner.invoke(app, ["resume"])

    assert result.exit_code != 0
    assert "No checkpoint found" in result.output


def test_resume_with_a_corrupted_checkpoint_file_fails_cleanly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Regression : un Checkpoint illisible (disque corrompu, edition manuelle)
    # doit produire un message explicite cote CLI, jamais une trace Python brute.
    checkpoint_path = tmp_path / "checkpoint.json"
    checkpoint_path.write_text("ceci n'est pas du JSON valide {{{", encoding="utf-8")
    monkeypatch.setattr(cli, "_storage", FileStorage(checkpoint_path))

    result = runner.invoke(app, ["resume"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)  # sortie propre, pas une trace Python non geree
    assert "corrupted" in result.output.lower()


def test_resume_with_a_corrupted_event_log_file_fails_cleanly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    checkpoint_path = tmp_path / "checkpoint.json"
    events_path = tmp_path / "checkpoint.json.events.jsonl"
    storage = FileStorage(checkpoint_path)
    mission = Mission(goal="deployer depuis un checkpoint")
    storage.save_checkpoint(Checkpoint(mission=mission))
    events_path.write_text("ceci n'est pas un evenement JSON valide\n", encoding="utf-8")
    monkeypatch.setattr(cli, "_storage", storage)

    result = runner.invoke(app, ["resume"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)  # sortie propre, pas une trace Python non geree
    assert "corrupted" in result.output.lower()


# --- peon resume : configuration LLM invalide --------------------------------


def test_resume_with_an_invalid_timeout_seconds_fails_cleanly_instead_of_a_raw_validation_error() -> None:
    # Meme bug que sur `run`, chemin resume() : `_build_cli_runtime()` y est
    # appele hors de tout try/except (seuls CorruptedCheckpointError/
    # CorruptedEventLogError l'entourent avant), donc un Checkpoint valide
    # combine a `--timeout-seconds 0` laissait aussi fuir une trace brute.
    mission = Mission(goal="deployer depuis un checkpoint")
    cli._storage.save_checkpoint(Checkpoint(mission=mission))

    result = runner.invoke(app, ["resume", "--timeout-seconds", "0"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert "timeout_seconds" in result.output.lower()


def test_resuming_after_a_mission_already_succeeded_does_not_reexecute_the_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regression : une fois la Mission dans un etat terminal, le Checkpoint sur
    # disque doit refleter ce nouvel etat (pending_confirmation=None), sinon un
    # `peon resume` ulterieur (appele par erreur, ou par un script qui reessaie)
    # retrouve l'ancienne ConfirmationRequest deja resolue et re-execute l'Action
    # HIGH correspondante une deuxieme fois -- violation directe de l'invariant
    # "exactement une execution" (voir DeleteFileTool/RiskLevelRule).
    tool = _StubTool("run_command", RiskLevel.HIGH, ToolResult(success=True, output="ok"))
    _use_tools(monkeypatch, [tool])
    _use_llm(monkeypatch, [_action_response("run_command", command="echo hi"), _finish_response("success", "fait")])

    first = runner.invoke(app, ["run", "executer une commande"], input="y\n")
    assert first.exit_code == 0
    assert tool.call_count == 1

    _use_llm(monkeypatch, [_finish_response("success", "ne devrait pas etre appele")])
    second = runner.invoke(app, ["resume"], input="y\n")

    assert second.exit_code == 0
    assert tool.call_count == 1  # toujours 1 : aucune reexecution
    assert "no pending confirmation" in second.output.lower()


def test_resume_uses_the_checkpoint_mechanism(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = _StubTool("run_command", RiskLevel.HIGH, ToolResult(success=True, output="ok"))
    _use_tools(monkeypatch, [tool])
    _use_llm(monkeypatch, [_finish_response("success", "reprise terminee")])

    mission = Mission(goal="deployer depuis un checkpoint")
    mission.status = MissionStatus.AWAITING_CONFIRMATION
    request = ConfirmationRequest(
        mission_id=mission.id,
        action=Action(reasoning="x", tool_name="run_command", arguments={"command": "echo hi"}, risk_level=RiskLevel.HIGH),
        reason="tool jugee a risque",
    )
    cli._storage.save_checkpoint(Checkpoint(mission=mission, pending_confirmation=request))

    result = runner.invoke(app, ["resume"], input="y\n")

    assert result.exit_code == 0
    assert "Confirmation required" in result.output
    assert "Tool: run_command" in result.output
    assert tool.call_count == 1
    assert "Mission succeeded" in result.output


class _SimulatedCrash(Exception):
    pass


class _CrashAfterCalls(Storage):
    # Simule un arret brutal du process au N-ieme appel de `crash_method` :
    # les appels precedents atterrissent normalement sur le Storage
    # enveloppe, celui-la leve avant d'y toucher - meme convention que
    # tests/test_confirmation_idempotence.py (chantier « Idempotence durable
    # d'une Action HIGH apres confirmation »), duplique ici plutot
    # qu'importe : chaque module de test de ce projet reste autonome.
    def __init__(self, wrapped: Storage, *, crash_method: str, crash_on_call: int) -> None:
        self._wrapped = wrapped
        self._crash_method = crash_method
        self._crash_on_call = crash_on_call
        self._calls: dict[str, int] = {"save_checkpoint": 0, "save_events": 0}

    def _maybe_crash(self, method: str) -> None:
        self._calls[method] += 1
        if method == self._crash_method and self._calls[method] == self._crash_on_call:
            raise _SimulatedCrash(f"simulated crash on {method} call #{self._calls[method]}")

    def save_events(self, events):
        self._maybe_crash("save_events")
        self._wrapped.save_events(events)

    def load_events(self):
        return self._wrapped.load_events()

    def save_checkpoint(self, checkpoint):
        self._maybe_crash("save_checkpoint")
        self._wrapped.save_checkpoint(checkpoint)

    def load_checkpoint(self):
        return self._wrapped.load_checkpoint()


def test_run_crashing_right_after_a_granted_high_confirmation_never_reexecutes_on_resume(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Reproduit la fenetre de risque du chantier « Idempotence durable d'une
    # Action HIGH apres confirmation » au niveau CLI : `peon run` "crashe"
    # juste apres avoir reellement execute l'Action HIGH confirmee, avant que
    # cette execution ne soit elle-meme persistee. `_drive_to_completion`
    # sauvegarde un premier Checkpoint (AWAITING_CONFIRMATION) avant de
    # demander la confirmation (appel #1 a save_checkpoint) ;
    # `resume_confirmation()` en sauvegarde un deuxieme juste avant d'executer
    # l'Action (appel #2, doit reussir pour clore la fenetre) puis un
    # troisieme juste apres (appel #3, ou le crash est simule ici).
    checkpoint_path = tmp_path / "checkpoint.json"
    crashing_storage = _CrashAfterCalls(FileStorage(checkpoint_path), crash_method="save_checkpoint", crash_on_call=3)
    monkeypatch.setattr(cli, "_storage", crashing_storage)

    tool = _StubTool("run_command", RiskLevel.HIGH, ToolResult(success=True, output="ok"))
    _use_tools(monkeypatch, [tool])
    _use_llm(
        monkeypatch,
        [_action_response("run_command", command="echo hi"), _finish_response("success", "commande executee")],
    )

    first = runner.invoke(app, ["run", "executer une commande"], input="y\n")

    assert isinstance(first.exception, _SimulatedCrash)
    assert tool.call_count == 1  # executee malgre le "crash" simule juste apres

    # "Redemarrage" du process CLI : nouveau Storage non-crashant sur les
    # memes donnees deja atterries sur disque avant le crash simule.
    monkeypatch.setattr(cli, "_storage", FileStorage(checkpoint_path))
    _use_llm(monkeypatch, [_finish_response("success", "ne devrait jamais etre appele")])

    second = runner.invoke(app, ["resume"], input="y\n")

    assert second.exit_code == 0
    assert tool.call_count == 1  # toujours 1 : aucune reexecution
    assert "no pending confirmation" in second.output.lower()


def test_resume_finds_a_checkpoint_persisted_to_disk_by_a_separate_storage_instance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Simule un vrai redemarrage du process CLI : le Checkpoint est ecrit par
    # une premiere instance de FileStorage (storage_a), puis relu par une
    # deuxieme instance (storage_b, aucun etat Python partage avec storage_a)
    # branchee sur `cli._storage` avant l'appel a `peon resume`.
    checkpoint_path = tmp_path / "checkpoint.json"
    mission = Mission(goal="deployer depuis un checkpoint disque")
    mission.status = MissionStatus.AWAITING_CONFIRMATION
    request = ConfirmationRequest(
        mission_id=mission.id,
        action=Action(reasoning="x", tool_name="run_command", arguments={"command": "echo hi"}, risk_level=RiskLevel.HIGH),
        reason="tool jugee a risque",
    )
    storage_a = FileStorage(checkpoint_path)
    storage_a.save_checkpoint(Checkpoint(mission=mission, pending_confirmation=request))
    del storage_a

    monkeypatch.setattr(cli, "_storage", FileStorage(checkpoint_path))
    tool = _StubTool("run_command", RiskLevel.HIGH, ToolResult(success=True, output="ok"))
    _use_tools(monkeypatch, [tool])
    _use_llm(monkeypatch, [_finish_response("success", "reprise terminee")])

    result = runner.invoke(app, ["resume"], input="y\n")

    assert result.exit_code == 0
    assert "Confirmation required" in result.output
    assert "Tool: run_command" in result.output
    assert tool.call_count == 1
    assert "Mission succeeded" in result.output


# --- _build_llm : cablage reel vers OllamaLLM/OllamaConfig -------------------
#
# Tout le reste de ce module monkeypatche `_build_llm` (voir `_use_llm`) pour
# eviter le reseau : ce test verifie a part que l'implementation reelle
# traduit bien `--base-url`/`--timeout-seconds` en un `OllamaLLM` qui parle
# reellement a ce serveur - meme convention que
# test_composition.py::test_build_runtime_works_with_ollama_llm_without_real_network
# (vrai serveur HTTP local via `fake_ollama_server`, pas de reseau externe).


def test_build_llm_wires_base_url_into_a_working_ollama_client(fake_ollama_server: HTTPServer) -> None:
    FakeOllamaHandler.response_body = json.dumps(
        {"message": {"role": "assistant", "content": "reponse du faux serveur"}}
    ).encode("utf-8")
    host, port = fake_ollama_server.server_address

    llm = cli._build_llm("llama3.1", f"http://{host}:{port}", 5.0)

    assert isinstance(llm, OllamaLLM)
    assert llm.generate([{"role": "user", "content": "salut"}]) == "reponse du faux serveur"


def test_run_uses_the_cli_default_ollama_configuration_when_no_option_is_given(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _spying_build_llm(model: str, base_url: str, timeout_seconds: float):
        captured["model"] = model
        captured["base_url"] = base_url
        captured["timeout_seconds"] = timeout_seconds
        return _ScriptedLLM([_finish_response("termine")])

    monkeypatch.setattr(cli, "_build_llm", _spying_build_llm)
    _use_tools(monkeypatch, [])

    result = runner.invoke(app, ["run", "verifier la config Ollama par defaut"])

    assert result.exit_code == 0
    assert captured == {
        "model": cli._DEFAULT_MODEL,
        "base_url": cli._DEFAULT_BASE_URL,
        "timeout_seconds": cli._DEFAULT_TIMEOUT_SECONDS,
    }


# --- peon resume : --workspace-root -----------------------------------------------


def test_resume_with_workspace_root_denies_paths_outside_the_root_for_subsequent_actions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # `--workspace-root` n'affecte pas l'Action deja confirmee dans le
    # Checkpoint (executee sans repasser par le Policy Engine, voir
    # Runtime.resume_confirmation), mais doit bien s'appliquer aux Actions
    # suivantes de la boucle de raisonnement reprise -- c'est ce que ce test
    # verifie, pas juste que l'option existe.
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    outside_path = str(tmp_path / "outside.txt")

    mission = Mission(goal="deployer puis lire un fichier hors racine")
    mission.status = MissionStatus.AWAITING_CONFIRMATION
    request = ConfirmationRequest(
        mission_id=mission.id,
        action=Action(reasoning="x", tool_name="run_command", arguments={"command": "echo hi"}, risk_level=RiskLevel.HIGH),
        reason="tool jugee a risque",
    )
    cli._storage.save_checkpoint(Checkpoint(mission=mission, pending_confirmation=request))

    run_command_tool = _StubTool("run_command", RiskLevel.HIGH, ToolResult(success=True, output="ok"))
    read_file_tool = _StubTool("read_file", RiskLevel.LOW, parameters_schema=_PATH_SCHEMA)
    _use_tools(monkeypatch, [run_command_tool, read_file_tool])
    _use_llm(
        monkeypatch,
        [_action_response("read_file", path=outside_path), _finish_response("success", "termine")],
    )

    result = runner.invoke(app, ["resume", "--workspace-root", str(workspace_root)], input="y\n")

    assert result.exit_code == 0
    assert run_command_tool.call_count == 1  # action deja confirmee, executee normalement
    assert read_file_tool.call_count == 0  # nouvelle action, refusee par PathRestrictionRule
    assert "Mission succeeded" in result.output


# --- pas de reecriture de la boucle ReAct dans cli.py -----------------------------


def test_cli_module_does_not_duplicate_the_react_loop() -> None:
    # Analyse AST (pas une recherche texte, qui accrocherait aussi la
    # docstring du module) : aucun import ni reference de code vers les
    # rouages internes du Runtime -- seule son API publique est utilisee.
    tree = ast.parse(inspect.getsource(cli))
    identifiers = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
    } | {
        alias.asname or alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }

    forbidden = {"PolicyEngine", "Executor", "Reasoner", "LLMReasoner", "StateMachine", "transition"}
    assert identifiers.isdisjoint(forbidden)

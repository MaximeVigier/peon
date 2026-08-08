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
from typing import Any

import pytest
from typer.testing import CliRunner

from peon import __version__, cli
from peon.cli import app
from peon.llm import LLM
from peon.models.action import Action
from peon.models.checkpoint import Checkpoint
from peon.models.confirmation import ConfirmationRequest
from peon.models.mission import Mission, MissionStatus
from peon.models.tool_result import ToolResult
from peon.models.tool_spec import RiskLevel, ToolSpec
from peon.storage import InMemoryStorage
from peon.tools.base import Tool

runner = CliRunner()


class _ScriptedLLM(LLM):
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)

    def generate(self, messages: list[dict[str, str]]) -> str:
        return self._responses.pop(0)


class _StubTool(Tool):
    def __init__(self, name: str, risk_level: RiskLevel, result: ToolResult | None = None) -> None:
        self._spec = ToolSpec(
            name=name,
            description=f"Outil {name}",
            parameters_schema={"type": "object", "properties": {}},
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


# --- peon resume -------------------------------------------------------------------


def test_resume_without_checkpoint_fails_cleanly() -> None:
    result = runner.invoke(app, ["resume"])

    assert result.exit_code != 0
    assert "No checkpoint found" in result.output


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

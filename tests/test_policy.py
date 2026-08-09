from pathlib import Path
from typing import Any

import pytest

from peon.models.action import Action
from peon.models.tool_result import ToolResult
from peon.models.tool_spec import RiskLevel, ToolSpec
from peon.models.verdict import Verdict, VerdictType
from peon.policy import PolicyEngine
from peon.tool_registry import ToolRegistry
from peon.tools.base import Tool
from peon.tools.filesystem import DeleteFileTool
from peon.workspace import LocalWorkspace


class _StubTool(Tool):
    def __init__(self, name: str, risk_level: RiskLevel) -> None:
        self._spec = ToolSpec(
            name=name,
            description=f"Outil {name}",
            parameters_schema={"type": "object", "properties": {}},
            risk_level=risk_level,
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        raise AssertionError("le Policy Engine ne doit jamais executer un Tool")


def _registry(*tools: Tool) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    return registry


def _action(tool_name: str, **arguments: Any) -> Action:
    return Action(reasoning="x", tool_name=tool_name, arguments=arguments, risk_level=RiskLevel.LOW)


def test_low_risk_action_is_allowed() -> None:
    engine = PolicyEngine(_registry(_StubTool("read_file", RiskLevel.LOW)))

    verdict = engine.evaluate(_action("read_file", path="README.md"))

    assert verdict.type is VerdictType.ALLOWED


def test_untargeted_delete_of_an_unscoped_path_is_denied() -> None:
    engine = PolicyEngine(_registry(_StubTool("run_command", RiskLevel.HIGH)))

    verdict = engine.evaluate(_action("run_command", command="rm -rf /"))

    assert verdict.type is VerdictType.DENIED
    assert verdict.suggested_action is None


def test_high_risk_action_requires_confirmation() -> None:
    engine = PolicyEngine(_registry(_StubTool("git_push", RiskLevel.HIGH)))

    verdict = engine.evaluate(_action("git_push", remote="origin", branch="main"))

    assert verdict.type is VerdictType.REQUIRES_CONFIRMATION


def test_untargeted_delete_of_a_relative_path_is_rewritten() -> None:
    engine = PolicyEngine(_registry(_StubTool("run_command", RiskLevel.HIGH)))

    verdict = engine.evaluate(_action("run_command", command="rm -rf build"))

    assert verdict.type is VerdictType.REWRITE
    assert verdict.suggested_action is not None
    assert verdict.suggested_action.arguments["command"] == "rm -rf -- ./build"


def test_unknown_tool_is_denied() -> None:
    engine = PolicyEngine(_registry())

    verdict = engine.evaluate(_action("does_not_exist"))

    assert verdict.type is VerdictType.DENIED


def test_medium_risk_action_is_allowed() -> None:
    engine = PolicyEngine(_registry(_StubTool("run_command", RiskLevel.MEDIUM)))

    verdict = engine.evaluate(_action("run_command", command="echo hello"))

    assert verdict.type is VerdictType.ALLOWED


# --- Composition (guardrails.py, chaine ordonnee de PolicyRule) --------------


class _AlwaysNoneRule:
    def evaluate(self, action: Action) -> Verdict | None:
        return None


class _AlwaysDeniedRule:
    def __init__(self, reason: str) -> None:
        self._reason = reason

    def evaluate(self, action: Action) -> Verdict | None:
        return Verdict(type=VerdictType.DENIED, reason=self._reason)


class _AlwaysAllowedRule:
    def evaluate(self, action: Action) -> Verdict | None:
        return Verdict(type=VerdictType.ALLOWED, reason="toujours autorise")


def test_the_first_rule_that_returns_a_verdict_wins() -> None:
    engine = PolicyEngine(
        _registry(),
        rules=[_AlwaysDeniedRule("premiere regle"), _AlwaysAllowedRule()],
    )

    verdict = engine.evaluate(_action("anything"))

    assert verdict.type is VerdictType.DENIED
    assert verdict.reason == "premiere regle"


def test_a_rule_returning_none_lets_the_chain_continue() -> None:
    engine = PolicyEngine(
        _registry(),
        rules=[_AlwaysNoneRule(), _AlwaysDeniedRule("deuxieme regle")],
    )

    verdict = engine.evaluate(_action("anything"))

    assert verdict.type is VerdictType.DENIED
    assert verdict.reason == "deuxieme regle"


def test_no_rule_matching_fails_closed_instead_of_silently_allowing() -> None:
    engine = PolicyEngine(_registry(), rules=[_AlwaysNoneRule()])

    with pytest.raises(AssertionError):
        engine.evaluate(_action("anything"))


def test_dangerous_command_rule_takes_priority_over_the_generic_risk_level_rule() -> None:
    # rm -rf non cible sur un Tool HIGH : sans priorite, la regle generique
    # renverrait REQUIRES_CONFIRMATION avant meme d'atteindre la regle
    # specifique - invariant explicitement demande par la roadmap Phase 3.
    engine = PolicyEngine(_registry(_StubTool("run_command", RiskLevel.HIGH)))

    verdict = engine.evaluate(_action("run_command", command="rm -rf /"))

    assert verdict.type is VerdictType.DENIED


# --- Arguments (ArgumentsSchemaRule, via PolicyEngine) ------------------------


def _tool_with_path_schema(name: str, risk_level: RiskLevel) -> Tool:
    return _StubToolWithSchema(
        name,
        risk_level,
        {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    )


class _StubToolWithSchema(Tool):
    def __init__(self, name: str, risk_level: RiskLevel, parameters_schema: dict[str, Any]) -> None:
        self._spec = ToolSpec(
            name=name,
            description=f"Outil {name}",
            parameters_schema=parameters_schema,
            risk_level=risk_level,
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        raise AssertionError("le Policy Engine ne doit jamais executer un Tool")


def test_conforming_arguments_are_allowed() -> None:
    engine = PolicyEngine(_registry(_tool_with_path_schema("read_file", RiskLevel.LOW)))

    verdict = engine.evaluate(_action("read_file", path="README.md"))

    assert verdict.type is VerdictType.ALLOWED


def test_missing_required_argument_is_denied() -> None:
    engine = PolicyEngine(_registry(_tool_with_path_schema("read_file", RiskLevel.LOW)))

    verdict = engine.evaluate(_action("read_file"))

    assert verdict.type is VerdictType.DENIED


def test_wrong_argument_type_is_denied() -> None:
    engine = PolicyEngine(_registry(_tool_with_path_schema("read_file", RiskLevel.LOW)))

    verdict = engine.evaluate(_action("read_file", path=123))

    assert verdict.type is VerdictType.DENIED


# --- Paths (PathRestrictionRule, via PolicyEngine.workspace_root) ------------


def test_path_inside_the_configured_root_is_allowed(tmp_path: Path) -> None:
    engine = PolicyEngine(_registry(_tool_with_path_schema("read_file", RiskLevel.LOW)), workspace_root=tmp_path)

    verdict = engine.evaluate(_action("read_file", path=str(tmp_path / "note.txt")))

    assert verdict.type is VerdictType.ALLOWED


def test_path_outside_the_configured_root_is_denied(tmp_path: Path) -> None:
    engine = PolicyEngine(_registry(_tool_with_path_schema("read_file", RiskLevel.LOW)), workspace_root=tmp_path)

    verdict = engine.evaluate(_action("read_file", path=str(tmp_path.parent / "elsewhere.txt")))

    assert verdict.type is VerdictType.DENIED


def test_path_traversal_attempt_is_denied(tmp_path: Path) -> None:
    engine = PolicyEngine(_registry(_tool_with_path_schema("read_file", RiskLevel.LOW)), workspace_root=tmp_path)

    verdict = engine.evaluate(_action("read_file", path="../outside.txt"))

    assert verdict.type is VerdictType.DENIED


def test_normal_relative_path_is_allowed(tmp_path: Path) -> None:
    engine = PolicyEngine(_registry(_tool_with_path_schema("read_file", RiskLevel.LOW)), workspace_root=tmp_path)

    verdict = engine.evaluate(_action("read_file", path="notes/today.md"))

    assert verdict.type is VerdictType.ALLOWED


def test_path_restriction_rule_takes_priority_over_the_generic_risk_level_rule(tmp_path: Path) -> None:
    # Sans priorite, la regle generique renverrait REQUIRES_CONFIRMATION
    # (Tool HIGH) avant meme que la restriction de chemin ne soit consultee -
    # invariant d'ordre de la chaine par defaut (voir policy.py).
    engine = PolicyEngine(
        _registry(_tool_with_path_schema("dangerous_read", RiskLevel.HIGH)), workspace_root=tmp_path
    )

    verdict = engine.evaluate(_action("dangerous_read", path=str(tmp_path.parent / "elsewhere.txt")))

    assert verdict.type is VerdictType.DENIED


def test_without_a_configured_root_no_path_restriction_applies(tmp_path: Path) -> None:
    # Comportement par defaut (constructeur sans workspace_root) inchange :
    # aucune restriction de chemin, comme avant cette phase.
    engine = PolicyEngine(_registry(_tool_with_path_schema("read_file", RiskLevel.LOW)))

    verdict = engine.evaluate(_action("read_file", path=str(tmp_path.parent / "elsewhere.txt")))

    assert verdict.type is VerdictType.ALLOWED


# --- delete_file (Tool HIGH reel, exerce le chemin de confirmation) ----------
#
# Utilise le vrai DeleteFileTool (pas un _StubTool) : le Policy Engine ne fait
# que resoudre son ToolSpec via le registre (jamais execute()), donc l'y
# enregistrer avec un LocalWorkspace reel ici ne presente aucun risque - voir
# _StubTool.execute() plus haut, qui leve si jamais appele par erreur.


def _registry_with_delete_file() -> ToolRegistry:
    return _registry(DeleteFileTool(LocalWorkspace()))


def test_delete_file_is_high_risk_and_requires_confirmation() -> None:
    engine = PolicyEngine(_registry_with_delete_file())

    verdict = engine.evaluate(_action("delete_file", path="notes/today.md"))

    assert verdict.type is VerdictType.REQUIRES_CONFIRMATION


def test_delete_file_outside_the_workspace_root_is_denied_before_any_confirmation(tmp_path: Path) -> None:
    engine = PolicyEngine(_registry_with_delete_file(), workspace_root=tmp_path)

    verdict = engine.evaluate(_action("delete_file", path=str(tmp_path.parent / "elsewhere.txt")))

    assert verdict.type is VerdictType.DENIED


def test_delete_file_inside_the_workspace_root_requires_confirmation(tmp_path: Path) -> None:
    # Combinaison HIGH + chemin valide : PathRestrictionRule laisse passer
    # (None), c'est RiskLevelRule, en dernier recours, qui declenche la
    # confirmation - jamais une execution directe.
    engine = PolicyEngine(_registry_with_delete_file(), workspace_root=tmp_path)

    verdict = engine.evaluate(_action("delete_file", path=str(tmp_path / "note.txt")))

    assert verdict.type is VerdictType.REQUIRES_CONFIRMATION

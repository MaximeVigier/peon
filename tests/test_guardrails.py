"""Tests unitaires des regles individuelles (guardrails.py), independants de
`PolicyEngine` : chaque regle est exercee directement via `.evaluate(action)`.
Les scenarios de composition (ordre, premiere regle gagnante, `None`
laissant la main) et les cas deja couverts bout-en-bout via `PolicyEngine`
restent dans `test_policy.py`, pour ne pas disperser inutilement les tests.
"""

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from peon.guardrails import (
    ArgumentsSchemaRule,
    DangerousCommandRule,
    PathRestrictionRule,
    RiskLevelRule,
    ToolAuthorizationRule,
)
from peon.models.action import Action
from peon.models.tool_result import ToolResult
from peon.models.tool_spec import RiskLevel, ToolSpec
from peon.models.verdict import VerdictType
from peon.tool_registry import ToolRegistry
from peon.tools.base import Tool


class _StubTool(Tool):
    def __init__(self, name: str, risk_level: RiskLevel, parameters_schema: dict[str, Any] | None = None) -> None:
        self._spec = ToolSpec(
            name=name,
            description=f"Outil {name}",
            parameters_schema=parameters_schema or {"type": "object", "properties": {}},
            risk_level=risk_level,
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        raise AssertionError("une regle du Policy Engine ne doit jamais executer un Tool")


def _registry(*tools: Tool) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    return registry


def _action(tool_name: str, **arguments: Any) -> Action:
    return Action(reasoning="x", tool_name=tool_name, arguments=arguments, risk_level=RiskLevel.LOW)


_PATH_SCHEMA = {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}
_COMMAND_SCHEMA = {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}


# --- ToolAuthorizationRule ----------------------------------------------------


def test_tool_authorization_rule_allows_a_registered_tool_to_continue() -> None:
    rule = ToolAuthorizationRule(_registry(_StubTool("read_file", RiskLevel.LOW)))

    verdict = rule.evaluate(_action("read_file"))

    assert verdict is None


def test_tool_authorization_rule_denies_an_unregistered_tool() -> None:
    rule = ToolAuthorizationRule(_registry())

    verdict = rule.evaluate(_action("does_not_exist"))

    assert verdict is not None
    assert verdict.type is VerdictType.DENIED


# --- DangerousCommandRule ------------------------------------------------------


def test_dangerous_command_rule_denies_an_untargeted_absolute_rm_rf() -> None:
    rule = DangerousCommandRule()

    verdict = rule.evaluate(_action("run_command", command="rm -rf /"))

    assert verdict is not None
    assert verdict.type is VerdictType.DENIED


def test_dangerous_command_rule_rewrites_a_relative_untargeted_rm_rf() -> None:
    rule = DangerousCommandRule()

    verdict = rule.evaluate(_action("run_command", command="rm -rf build"))

    assert verdict is not None
    assert verdict.type is VerdictType.REWRITE
    assert verdict.suggested_action is not None
    assert verdict.suggested_action.arguments["command"] == "rm -rf -- ./build"


def test_dangerous_command_rule_does_not_apply_to_a_harmless_command() -> None:
    rule = DangerousCommandRule()

    verdict = rule.evaluate(_action("run_command", command="echo hello"))

    assert verdict is None


def test_dangerous_command_rule_does_not_apply_when_no_command_argument() -> None:
    rule = DangerousCommandRule()

    verdict = rule.evaluate(_action("read_file", path="README.md"))

    assert verdict is None


# --- ArgumentsSchemaRule --------------------------------------------------------


def test_arguments_schema_rule_allows_conforming_arguments() -> None:
    rule = ArgumentsSchemaRule(_registry(_StubTool("read_file", RiskLevel.LOW, _PATH_SCHEMA)))

    verdict = rule.evaluate(_action("read_file", path="README.md"))

    assert verdict is None


def test_arguments_schema_rule_denies_a_missing_required_field() -> None:
    rule = ArgumentsSchemaRule(_registry(_StubTool("read_file", RiskLevel.LOW, _PATH_SCHEMA)))

    verdict = rule.evaluate(_action("read_file"))

    assert verdict is not None
    assert verdict.type is VerdictType.DENIED
    assert "path" in verdict.reason


def test_arguments_schema_rule_denies_an_incorrect_type() -> None:
    rule = ArgumentsSchemaRule(_registry(_StubTool("read_file", RiskLevel.LOW, _PATH_SCHEMA)))

    verdict = rule.evaluate(_action("read_file", path=123))

    assert verdict is not None
    assert verdict.type is VerdictType.DENIED


def test_arguments_schema_rule_allows_undeclared_extra_fields_by_default() -> None:
    rule = ArgumentsSchemaRule(_registry(_StubTool("run_command", RiskLevel.MEDIUM, _COMMAND_SCHEMA)))

    verdict = rule.evaluate(_action("run_command", command="echo hi", extra="not declared"))

    assert verdict is None


def test_arguments_schema_rule_does_not_apply_to_an_unregistered_tool() -> None:
    rule = ArgumentsSchemaRule(_registry())

    verdict = rule.evaluate(_action("does_not_exist"))

    assert verdict is None  # ToolAuthorizationRule tranche ce cas, pas celle-ci


# --- PathRestrictionRule --------------------------------------------------------


def test_path_restriction_rule_allows_a_path_inside_the_root(tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_text("bonjour", encoding="utf-8")
    rule = PathRestrictionRule(_registry(_StubTool("read_file", RiskLevel.LOW, _PATH_SCHEMA)), tmp_path)

    verdict = rule.evaluate(_action("read_file", path=str(tmp_path / "note.txt")))

    assert verdict is None


def test_path_restriction_rule_allows_a_normal_relative_path(tmp_path: Path) -> None:
    rule = PathRestrictionRule(_registry(_StubTool("read_file", RiskLevel.LOW, _PATH_SCHEMA)), tmp_path)

    verdict = rule.evaluate(_action("read_file", path="sub/note.txt"))

    assert verdict is None


def test_path_restriction_rule_denies_a_path_outside_the_root(tmp_path: Path) -> None:
    outside = tmp_path.parent / "elsewhere.txt"
    rule = PathRestrictionRule(_registry(_StubTool("read_file", RiskLevel.LOW, _PATH_SCHEMA)), tmp_path)

    verdict = rule.evaluate(_action("read_file", path=str(outside)))

    assert verdict is not None
    assert verdict.type is VerdictType.DENIED


def test_path_restriction_rule_denies_a_traversal_attempt(tmp_path: Path) -> None:
    rule = PathRestrictionRule(_registry(_StubTool("read_file", RiskLevel.LOW, _PATH_SCHEMA)), tmp_path)

    verdict = rule.evaluate(_action("read_file", path="../outside.txt"))

    assert verdict is not None
    assert verdict.type is VerdictType.DENIED


def test_path_restriction_rule_does_not_apply_to_a_tool_without_a_path_parameter(tmp_path: Path) -> None:
    rule = PathRestrictionRule(_registry(_StubTool("run_command", RiskLevel.MEDIUM, _COMMAND_SCHEMA)), tmp_path)

    verdict = rule.evaluate(_action("run_command", command="rm -rf /"))

    assert verdict is None  # DangerousCommandRule s'en charge, pas celle-ci


def test_path_restriction_rule_handles_a_nonexistent_path_inside_the_root(tmp_path: Path) -> None:
    # Path.resolve() ne requiert pas que le chemin existe (strict=False par
    # defaut) : un chemin qui n'a jamais ete cree doit rester autorise s'il
    # se resout sous la racine, sans lever d'exception.
    rule = PathRestrictionRule(_registry(_StubTool("read_file", RiskLevel.LOW, _PATH_SCHEMA)), tmp_path)

    verdict = rule.evaluate(_action("read_file", path=str(tmp_path / "does" / "not" / "exist.txt")))

    assert verdict is None


def test_path_restriction_rule_handles_a_nonexistent_path_outside_the_root(tmp_path: Path) -> None:
    rule = PathRestrictionRule(_registry(_StubTool("read_file", RiskLevel.LOW, _PATH_SCHEMA)), tmp_path)

    verdict = rule.evaluate(_action("read_file", path=str(tmp_path.parent / "does" / "not" / "exist.txt")))

    assert verdict is not None
    assert verdict.type is VerdictType.DENIED


def test_path_restriction_rule_denies_a_sibling_directory_sharing_a_name_prefix(tmp_path: Path) -> None:
    # tmp_path="…/root", chemin candidat sous "…/root-evil" : un simple
    # startswith() sur la representation texte confondrait les deux, mais la
    # comparaison par Path.parents (utilisee par la regle) ne le fait pas.
    sibling = tmp_path.parent / f"{tmp_path.name}-evil" / "note.txt"
    rule = PathRestrictionRule(_registry(_StubTool("read_file", RiskLevel.LOW, _PATH_SCHEMA)), tmp_path)

    verdict = rule.evaluate(_action("read_file", path=str(sibling)))

    assert verdict is not None
    assert verdict.type is VerdictType.DENIED


@pytest.mark.skipif(sys.platform != "win32", reason="casse/separateurs specifiques a Windows")
def test_path_restriction_rule_is_case_insensitive_and_slash_normalized_on_windows(tmp_path: Path) -> None:
    # pathlib.WindowsPath compare par casefold et normalise '/'  vs '\\' :
    # une variation de casse ou de separateur dans l'argument 'path' ne doit
    # donc pas faire echapper artificiellement a la racine.
    rule = PathRestrictionRule(_registry(_StubTool("read_file", RiskLevel.LOW, _PATH_SCHEMA)), tmp_path)

    upper_case_path = str(tmp_path).upper() + "\\NOTE.TXT"
    forward_slash_path = str(tmp_path).replace("\\", "/") + "/sub/note.txt"

    assert rule.evaluate(_action("read_file", path=upper_case_path)) is None
    assert rule.evaluate(_action("read_file", path=forward_slash_path)) is None


@pytest.mark.skipif(sys.platform != "win32", reason="jonctions specifiques a Windows")
def test_path_restriction_rule_denies_a_junction_that_escapes_the_root(tmp_path: Path) -> None:
    # Une jonction NTFS a l'interieur de la racine mais pointant en dehors ne
    # doit pas permettre d'echapper a la restriction : Path.resolve() suit la
    # jonction jusqu'a sa cible reelle avant le containment check.
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")

    junction = root / "escape"
    created = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
        capture_output=True,
        text=True,
    )
    if created.returncode != 0:
        pytest.skip(f"impossible de creer une jonction NTFS dans cet environnement : {created.stderr}")

    rule = PathRestrictionRule(_registry(_StubTool("read_file", RiskLevel.LOW, _PATH_SCHEMA)), root)

    verdict = rule.evaluate(_action("read_file", path=str(junction / "secret.txt")))

    assert verdict is not None
    assert verdict.type is VerdictType.DENIED


# --- RiskLevelRule ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("risk_level", "expected"),
    [
        (RiskLevel.LOW, VerdictType.ALLOWED),
        (RiskLevel.MEDIUM, VerdictType.ALLOWED),
        (RiskLevel.HIGH, VerdictType.REQUIRES_CONFIRMATION),
    ],
)
def test_risk_level_rule_maps_risk_to_the_expected_verdict(risk_level: RiskLevel, expected: VerdictType) -> None:
    rule = RiskLevelRule(_registry(_StubTool("some_tool", risk_level)))

    verdict = rule.evaluate(_action("some_tool"))

    assert verdict is not None
    assert verdict.type is expected


def test_risk_level_rule_does_not_apply_to_an_unregistered_tool() -> None:
    rule = RiskLevelRule(_registry())

    verdict = rule.evaluate(_action("does_not_exist"))

    assert verdict is None  # ToolAuthorizationRule tranche ce cas, pas celle-ci

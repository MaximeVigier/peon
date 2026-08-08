"""Tests unitaires des regles individuelles (guardrails.py), independants de
`PolicyEngine` : chaque regle est exercee directement via `.evaluate(action)`.
Les scenarios de composition (ordre, premiere regle gagnante, `None`
laissant la main) et les cas deja couverts bout-en-bout via `PolicyEngine`
restent dans `test_policy.py`, pour ne pas disperser inutilement les tests.
"""

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

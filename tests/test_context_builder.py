from typing import Any

import pytest
from pydantic import ValidationError

from peon.context_builder import ContextBuilder
from peon.models.context import Context
from peon.models.mission import MissionStatus
from peon.models.observation import Observation, ObservationKind
from peon.models.tool_result import ToolResult
from peon.models.tool_spec import RiskLevel, ToolSpec
from peon.tool_registry import ToolRegistry
from peon.tools.base import Tool


class _StubTool(Tool):
    def __init__(self, name: str, risk_level: RiskLevel = RiskLevel.LOW) -> None:
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
        raise AssertionError("le Context Builder ne doit jamais executer un Tool")


def _registry(*tools: Tool) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    return registry


def _observation() -> Observation:
    return Observation(kind=ObservationKind.EXECUTION_RESULT, summary="fichier lu avec succes")


def test_build_returns_a_context_with_the_given_mission_fields() -> None:
    builder = ContextBuilder(_registry())

    context = builder.build(
        mission_goal="corriger le bug de parsing",
        mission_status=MissionStatus.REASONING,
        mission_iteration_count=2,
        observations=[],
    )

    assert isinstance(context, Context)
    assert context.mission_goal == "corriger le bug de parsing"
    assert context.mission_status is MissionStatus.REASONING
    assert context.mission_iteration_count == 2


def test_build_carries_observations_as_given() -> None:
    builder = ContextBuilder(_registry())
    observation = _observation()

    context = builder.build(
        mission_goal="x",
        mission_status=MissionStatus.REASONING,
        mission_iteration_count=0,
        observations=[observation],
    )

    assert context.observations == [observation]


def test_build_with_no_tools_returns_an_empty_list() -> None:
    builder = ContextBuilder(_registry())

    context = builder.build(
        mission_goal="x",
        mission_status=MissionStatus.REASONING,
        mission_iteration_count=0,
        observations=[],
    )

    assert context.available_tools == []


def test_build_exposes_tool_specs_not_concrete_tools() -> None:
    read_file = _StubTool("read_file")
    run_command = _StubTool("run_command", risk_level=RiskLevel.HIGH)
    builder = ContextBuilder(_registry(read_file, run_command))

    context = builder.build(
        mission_goal="x",
        mission_status=MissionStatus.REASONING,
        mission_iteration_count=0,
        observations=[],
    )

    assert len(context.available_tools) == 2
    assert read_file.spec in context.available_tools
    assert run_command.spec in context.available_tools
    assert all(isinstance(tool, ToolSpec) for tool in context.available_tools)


def test_build_reflects_the_registry_at_call_time() -> None:
    registry = _registry()
    builder = ContextBuilder(registry)
    registry.register(_StubTool("read_file"))

    context = builder.build(
        mission_goal="x",
        mission_status=MissionStatus.REASONING,
        mission_iteration_count=0,
        observations=[],
    )

    assert len(context.available_tools) == 1


def test_build_propagates_context_validation() -> None:
    builder = ContextBuilder(_registry())

    with pytest.raises(ValidationError):
        builder.build(
            mission_goal="   ",
            mission_status=MissionStatus.REASONING,
            mission_iteration_count=0,
            observations=[],
        )

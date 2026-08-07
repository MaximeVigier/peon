import pytest
from pydantic import ValidationError

from peon.models.context import Context
from peon.models.mission import MissionStatus
from peon.models.observation import Observation, ObservationKind
from peon.models.tool_spec import RiskLevel, ToolSpec


def _observation() -> Observation:
    return Observation(kind=ObservationKind.EXECUTION_RESULT, summary="fichier lu avec succes")


def _tool_spec() -> ToolSpec:
    return ToolSpec(
        name="read_file",
        description="Lit le contenu d'un fichier texte",
        parameters_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        risk_level=RiskLevel.LOW,
    )


def test_defaults_are_sensible() -> None:
    context = Context(
        mission_goal="corriger le bug de parsing",
        mission_status=MissionStatus.REASONING,
        mission_iteration_count=0,
    )

    assert context.observations == []
    assert context.available_tools == []


def test_observations_are_carried_as_given() -> None:
    observation = _observation()
    context = Context(
        mission_goal="corriger le bug de parsing",
        mission_status=MissionStatus.REASONING,
        mission_iteration_count=1,
        observations=[observation],
    )

    assert context.observations == [observation]


def test_available_tools_are_carried_as_given() -> None:
    tool_spec = _tool_spec()
    context = Context(
        mission_goal="corriger le bug de parsing",
        mission_status=MissionStatus.REASONING,
        mission_iteration_count=0,
        available_tools=[tool_spec],
    )

    assert context.available_tools == [tool_spec]


@pytest.mark.parametrize("mission_goal", ["", "   "])
def test_blank_mission_goal_is_rejected(mission_goal: str) -> None:
    with pytest.raises(ValidationError):
        Context(mission_goal=mission_goal, mission_status=MissionStatus.REASONING, mission_iteration_count=0)


def test_mission_goal_is_stripped() -> None:
    context = Context(
        mission_goal="  corriger le bug de parsing  ",
        mission_status=MissionStatus.REASONING,
        mission_iteration_count=0,
    )

    assert context.mission_goal == "corriger le bug de parsing"


def test_negative_iteration_count_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Context(mission_goal="x", mission_status=MissionStatus.REASONING, mission_iteration_count=-1)


def test_invalid_mission_status_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Context(mission_goal="x", mission_status="not_a_real_status", mission_iteration_count=0)


def test_context_is_frozen() -> None:
    context = Context(mission_goal="x", mission_status=MissionStatus.REASONING, mission_iteration_count=0)

    with pytest.raises(ValidationError):
        context.mission_iteration_count = 1  # type: ignore[misc]


@pytest.mark.parametrize("mission_status", list(MissionStatus))
def test_every_mission_status_is_accepted(mission_status: MissionStatus) -> None:
    context = Context(mission_goal="x", mission_status=mission_status, mission_iteration_count=0)

    assert context.mission_status is mission_status

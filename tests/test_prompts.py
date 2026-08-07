from peon.models.context import Context
from peon.models.mission import MissionStatus
from peon.models.observation import Observation, ObservationKind
from peon.models.tool_spec import RiskLevel, ToolSpec
from peon.prompts import PromptBuilder


def _context(**overrides: object) -> Context:
    defaults: dict[str, object] = {
        "mission_goal": "corriger le bug de parsing",
        "mission_status": MissionStatus.REASONING,
        "mission_iteration_count": 1,
        "observations": [],
        "available_tools": [],
    }
    defaults.update(overrides)
    return Context(**defaults)  # type: ignore[arg-type]


def test_build_returns_a_system_message_then_a_user_message() -> None:
    messages = PromptBuilder().build(_context())

    assert [message["role"] for message in messages] == ["system", "user"]
    assert all(isinstance(message["content"], str) and message["content"] for message in messages)


def test_build_is_deterministic_for_the_same_context() -> None:
    context = _context()

    first = PromptBuilder().build(context)
    second = PromptBuilder().build(context)

    assert first == second


def test_user_message_contains_mission_goal_status_and_iteration() -> None:
    context = _context(
        mission_goal="corriger le bug X",
        mission_status=MissionStatus.REASONING,
        mission_iteration_count=3,
    )

    user_content = PromptBuilder().build(context)[1]["content"]

    assert "corriger le bug X" in user_content
    assert MissionStatus.REASONING.value in user_content
    assert "3" in user_content


def test_user_message_lists_available_tools() -> None:
    tool = ToolSpec(
        name="read_file",
        description="lit un fichier",
        parameters_schema={"type": "object"},
        risk_level=RiskLevel.LOW,
    )

    user_content = PromptBuilder().build(_context(available_tools=[tool]))[1]["content"]

    assert "read_file" in user_content
    assert "lit un fichier" in user_content


def test_user_message_signals_no_tools_when_none_available() -> None:
    user_content = PromptBuilder().build(_context(available_tools=[]))[1]["content"]

    assert "(aucun)" in user_content


def test_user_message_lists_observations() -> None:
    observation = Observation(kind=ObservationKind.EXECUTION_RESULT, summary="fichier lu avec succes")

    user_content = PromptBuilder().build(_context(observations=[observation]))[1]["content"]

    assert "fichier lu avec succes" in user_content
    assert ObservationKind.EXECUTION_RESULT.value in user_content


def test_user_message_signals_no_observations_when_none_recorded() -> None:
    user_content = PromptBuilder().build(_context(observations=[]))[1]["content"]

    assert "(aucune)" in user_content

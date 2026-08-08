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


def test_user_message_includes_the_real_output_of_a_successful_execution_result() -> None:
    # Regression du bug decouvert avec Ollama : un summary generique ("outil
    # 'x' execute avec succes") ne suffit pas, le LLM doit voir le contenu
    # reel produit par l'outil (ici la sortie de `grep`), pas seulement le
    # fait que l'execution a reussi.
    observation = Observation(
        kind=ObservationKind.EXECUTION_RESULT,
        summary="outil 'run_command' execute avec succes",
        details={"tool_name": "run_command", "output": {"stdout": "mission2/gamma.txt:PEON_SECRET=42\n", "stderr": ""}},
    )

    user_content = PromptBuilder().build(_context(observations=[observation]))[1]["content"]

    assert "PEON_SECRET=42" in user_content


def test_user_message_includes_string_output_of_a_successful_execution_result() -> None:
    observation = Observation(
        kind=ObservationKind.EXECUTION_RESULT,
        summary="outil 'read_file' execute avec succes",
        details={"tool_name": "read_file", "output": "contenu reel du fichier"},
    )

    user_content = PromptBuilder().build(_context(observations=[observation]))[1]["content"]

    assert "contenu reel du fichier" in user_content


def test_user_message_truncates_a_very_large_execution_result_output() -> None:
    huge_output = "x" * 5000
    observation = Observation(
        kind=ObservationKind.EXECUTION_RESULT,
        summary="outil 'read_file' execute avec succes",
        details={"tool_name": "read_file", "output": huge_output},
    )

    user_content = PromptBuilder().build(_context(observations=[observation]))[1]["content"]

    assert huge_output not in user_content
    assert "tronque" in user_content
    assert "x" * 2000 in user_content


def test_user_message_does_not_alter_execution_error_rendering() -> None:
    # Comportement des erreurs inchange : pas de ligne "resultat" ajoutee,
    # seul le summary (deja informatif, voir Runtime._execute_action) est
    # rendu, comme avant le correctif.
    observation = Observation(
        kind=ObservationKind.EXECUTION_ERROR,
        summary="la commande a echoue avec le code 1",
        details={"tool_name": "run_command", "category": "tool_failure", "return_code": 1},
    )

    user_content = PromptBuilder().build(_context(observations=[observation]))[1]["content"]

    assert "la commande a echoue avec le code 1" in user_content
    assert "resultat" not in user_content


def test_user_message_ignores_missing_or_none_output_on_execution_result() -> None:
    observation = Observation(
        kind=ObservationKind.EXECUTION_RESULT,
        summary="outil 'noop' execute avec succes",
        details={"tool_name": "noop", "output": None},
    )

    user_content = PromptBuilder().build(_context(observations=[observation]))[1]["content"]

    assert "resultat" not in user_content

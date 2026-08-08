from pathlib import Path

from peon.context_builder import ContextBuilder
from peon.event_log import EventLog
from peon.executor import Executor
from peon.models.context import Context
from peon.models.decision import ActionDecision, Decision, FinishDecision
from peon.models.events import EventType
from peon.models.mission import MissionStatus
from peon.models.observation import ObservationKind
from peon.policy import PolicyEngine
from peon.reasoner import Reasoner
from peon.runtime import Runtime
from peon.tool_registry import ToolRegistry
from peon.tools.filesystem import ReadFileTool
from peon.workspace import LocalWorkspace

_CONFIG_CONTENT = (
    "[server]\n"
    "host = 127.0.0.1\n"
    "port = 8080\n"
    "\n"
    "[app]\n"
    "name = peon\n"
    "debug = false\n"
)
_EXPECTED_MARKER = "port = 8080"


class _ReadConfigThenConfirmReasoner(Reasoner):
    # Le chemin du fichier est une donnee externe (fournie a la construction,
    # comme le ferait un utilisateur reel) mais le choix de l'outil et la
    # confirmation du contenu ne viennent que du Context recu : le nom
    # d'outil est cherche dans context.available_tools (jamais suppose), et
    # la confirmation finale lit context.observations (jamais le fichier
    # directement) - reproduit un comportement LLM deterministe et non
    # hallucine.
    def __init__(self, path: str) -> None:
        self._path = path

    def decide(self, context: Context) -> Decision:
        if not context.observations:
            return self._request_read(context)
        return self._confirm_from_observation(context)

    def _request_read(self, context: Context) -> Decision:
        tool_spec = next((spec for spec in context.available_tools if spec.name == "read_file"), None)
        if tool_spec is None:
            return FinishDecision(
                outcome="failure",
                summary="l'outil 'read_file' n'est pas disponible dans le Context, mission abandonnee",
                confidence=1.0,
            )
        return ActionDecision(
            reasoning="lire le fichier de configuration demande par la mission",
            tool_name=tool_spec.name,
            arguments={"path": self._path},
        )

    @staticmethod
    def _confirm_from_observation(context: Context) -> Decision:
        latest = context.observations[-1]
        output = (latest.details or {}).get("output") if latest.kind is ObservationKind.EXECUTION_RESULT else None
        if isinstance(output, str) and _EXPECTED_MARKER in output:
            return FinishDecision(
                outcome="success",
                summary=f"contenu confirme : le fichier de configuration contient bien '{_EXPECTED_MARKER}'",
                confidence=1.0,
            )
        return FinishDecision(
            outcome="failure",
            summary="le contenu lu ne correspond pas au contenu attendu du fichier de configuration",
            confidence=1.0,
        )


def test_full_cycle_reads_a_config_file_and_confirms_its_content(tmp_path: Path) -> None:
    config_file = tmp_path / "app.conf"
    config_file.write_text(_CONFIG_CONTENT, encoding="utf-8")

    registry = ToolRegistry()
    registry.register(ReadFileTool(LocalWorkspace()))
    event_log = EventLog()
    runtime = Runtime(
        context_builder=ContextBuilder(registry),
        reasoner=_ReadConfigThenConfirmReasoner(str(config_file)),
        policy_engine=PolicyEngine(registry),
        executor=Executor(registry),
        event_log=event_log,
    )

    mission = runtime.run("Lis un fichier de configuration et confirme son contenu.")

    assert mission.status is MissionStatus.SUCCEEDED
    assert mission.iteration_count == 2

    assert len(runtime.observations) == 1
    observation = runtime.observations[0]
    assert observation.kind is ObservationKind.EXECUTION_RESULT
    assert observation.details is not None
    assert observation.details["tool_name"] == "read_file"
    assert observation.details["output"] == config_file.read_text(encoding="utf-8")
    assert _EXPECTED_MARKER in observation.details["output"]

    events = event_log.list_events()

    policy_events = [event for event in events if event.type is EventType.POLICY_EVALUATED]
    assert len(policy_events) == 1
    assert policy_events[0].payload["type"] == "allowed"

    assert any(event.type is EventType.TOOL_EXECUTION_COMPLETED for event in events)
    assert not any(event.type is EventType.TOOL_EXECUTION_FAILED for event in events)

    assert [event.type for event in events] == [
        EventType.MISSION_CREATED,
        EventType.STATE_TRANSITIONED,
        EventType.CONTEXT_BUILT,
        EventType.DECISION_RECEIVED,
        EventType.POLICY_EVALUATED,
        EventType.STATE_TRANSITIONED,
        EventType.TOOL_EXECUTION_COMPLETED,
        EventType.STATE_TRANSITIONED,
        EventType.OBSERVATION_PRODUCED,
        EventType.CONTEXT_BUILT,
        EventType.DECISION_RECEIVED,
        EventType.MISSION_SUCCEEDED,
        EventType.STATE_TRANSITIONED,
    ]

    mission_succeeded_event = next(event for event in events if event.type is EventType.MISSION_SUCCEEDED)
    assert _EXPECTED_MARKER in mission_succeeded_event.payload["summary"]


def test_reasoner_refuses_to_act_when_context_has_no_matching_tool(tmp_path: Path) -> None:
    config_file = tmp_path / "app.conf"
    config_file.write_text(_CONFIG_CONTENT, encoding="utf-8")

    empty_registry = ToolRegistry()  # read_file volontairement absent du Context
    event_log = EventLog()
    runtime = Runtime(
        context_builder=ContextBuilder(empty_registry),
        reasoner=_ReadConfigThenConfirmReasoner(str(config_file)),
        policy_engine=PolicyEngine(empty_registry),
        executor=Executor(empty_registry),
        event_log=event_log,
    )

    mission = runtime.run("Lis un fichier de configuration et confirme son contenu.")

    assert mission.status is MissionStatus.FAILED
    assert not any(event.type is EventType.TOOL_EXECUTION_COMPLETED for event in event_log.list_events())
    assert runtime.observations == []

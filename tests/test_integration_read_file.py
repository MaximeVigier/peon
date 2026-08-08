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


class _ReadThenFinishReasoner(Reasoner):
    # Chemin et nom d'outil fixes a la construction, jamais devines a partir
    # du Context ou du systeme de fichiers : reproduit une reponse LLM deja
    # connue et verifiee a l'avance, sans comportement non deterministe.
    def __init__(self, path: str) -> None:
        self._path = path
        self._call_count = 0

    def decide(self, context: Context) -> Decision:
        self._call_count += 1
        if self._call_count == 1:
            return ActionDecision(
                reasoning="lire le fichier demande par la mission",
                tool_name="read_file",
                arguments={"path": self._path},
            )
        return FinishDecision(outcome="success", summary="fichier lu avec succes", confidence=1.0)


def test_full_cycle_reads_a_real_file_through_every_component(tmp_path: Path) -> None:
    target = tmp_path / "mission.txt"
    target.write_text("contenu reel du fichier", encoding="utf-8")

    registry = ToolRegistry()
    registry.register(ReadFileTool(LocalWorkspace()))
    event_log = EventLog()
    runtime = Runtime(
        context_builder=ContextBuilder(registry),
        reasoner=_ReadThenFinishReasoner(str(target)),
        policy_engine=PolicyEngine(registry),
        executor=Executor(registry),
        event_log=event_log,
    )

    mission = runtime.run(f"lire le contenu de {target.name}")

    assert mission.status is MissionStatus.SUCCEEDED
    assert mission.iteration_count == 2

    assert len(runtime.observations) == 1
    observation = runtime.observations[0]
    assert observation.kind is ObservationKind.EXECUTION_RESULT
    assert observation.details is not None
    assert observation.details["tool_name"] == "read_file"
    assert observation.details["output"] == "contenu reel du fichier"

    assert [event.type for event in event_log.list_events()] == [
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


def test_full_cycle_reports_a_missing_file_as_an_execution_error(tmp_path: Path) -> None:
    missing = tmp_path / "absent.txt"

    registry = ToolRegistry()
    registry.register(ReadFileTool(LocalWorkspace()))
    runtime = Runtime(
        context_builder=ContextBuilder(registry),
        reasoner=_ReadThenFinishReasoner(str(missing)),
        policy_engine=PolicyEngine(registry),
        executor=Executor(registry),
        event_log=EventLog(),
    )

    runtime.run("lire un fichier absent")

    assert len(runtime.observations) == 1
    assert runtime.observations[0].kind is ObservationKind.EXECUTION_ERROR

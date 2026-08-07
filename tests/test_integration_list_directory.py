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
from peon.tools.filesystem import ListDirectoryTool


class _ListThenFinishReasoner(Reasoner):
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
                reasoning="lister le contenu du dossier demande par la mission",
                tool_name="list_directory",
                arguments={"path": self._path},
            )
        return FinishDecision(outcome="success", summary="dossier liste avec succes", confidence=1.0)


def test_full_cycle_lists_a_real_directory_through_every_component(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "b.txt").write_text("b", encoding="utf-8")

    registry = ToolRegistry()
    registry.register(ListDirectoryTool())
    assert registry.exists("list_directory")

    event_log = EventLog()
    runtime = Runtime(
        context_builder=ContextBuilder(registry),
        reasoner=_ListThenFinishReasoner(str(tmp_path)),
        policy_engine=PolicyEngine(registry),
        executor=Executor(registry),
        event_log=event_log,
    )

    mission = runtime.run(f"lister le contenu de {tmp_path.name}")

    assert mission.status is MissionStatus.SUCCEEDED
    assert mission.iteration_count == 2

    assert len(runtime.observations) == 1
    observation = runtime.observations[0]
    assert observation.kind is ObservationKind.EXECUTION_RESULT
    assert observation.details is not None
    assert observation.details["tool_name"] == "list_directory"
    assert observation.details["output"] == ["a.txt", "b.txt"]

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


def test_full_cycle_reports_a_missing_directory_as_an_execution_error(tmp_path: Path) -> None:
    missing = tmp_path / "absent"

    registry = ToolRegistry()
    registry.register(ListDirectoryTool())
    runtime = Runtime(
        context_builder=ContextBuilder(registry),
        reasoner=_ListThenFinishReasoner(str(missing)),
        policy_engine=PolicyEngine(registry),
        executor=Executor(registry),
        event_log=EventLog(),
    )

    runtime.run("lister un dossier absent")

    assert len(runtime.observations) == 1
    assert runtime.observations[0].kind is ObservationKind.EXECUTION_ERROR

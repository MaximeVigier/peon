from typing import Any

import pytest

from peon.context_builder import ContextBuilder
from peon.event_log import EventLog
from peon.executor import Executor
from peon.models.context import Context
from peon.models.decision import ActionDecision, Decision, FinishDecision
from peon.models.events import Event, EventType
from peon.models.mission import MissionStatus
from peon.models.observation import ObservationKind
from peon.models.tool_result import ToolResult
from peon.models.tool_spec import RiskLevel, ToolSpec
from peon.policy import PolicyEngine
from peon.reasoner import Reasoner
from peon.runtime import Runtime, StorageNotConfiguredError
from peon.storage import InMemoryStorage
from peon.tool_registry import ToolRegistry
from peon.tools.base import Tool


class _StubTool(Tool):
    def __init__(self, name: str) -> None:
        self._spec = ToolSpec(
            name=name,
            description=f"Outil {name}",
            parameters_schema={"type": "object", "properties": {}},
            risk_level=RiskLevel.LOW,
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        return ToolResult(success=True, output="contenu du fichier")


class _ScriptedReasoner(Reasoner):
    # Decisions fixees a l'avance, jamais devinees depuis le Context : reproduit
    # une sequence de reponses LLM deja connue, sans comportement non deterministe.
    def __init__(self, decisions: list[Decision]) -> None:
        self._decisions = list(decisions)

    def decide(self, context: Context) -> Decision:
        return self._decisions.pop(0)


def _registry(*tools: Tool) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    return registry


def test_persist_events_without_storage_fails_cleanly() -> None:
    registry = _registry()
    runtime = Runtime(
        context_builder=ContextBuilder(registry),
        reasoner=_ScriptedReasoner([FinishDecision(outcome="success", summary="fini", confidence=1.0)]),
        policy_engine=PolicyEngine(registry),
        executor=Executor(registry),
        event_log=EventLog(),
    )
    runtime.run("mission sans storage")

    with pytest.raises(StorageNotConfiguredError):
        runtime.persist_events()


def test_persist_events_saves_the_full_event_log_to_storage() -> None:
    registry = _registry()
    event_log = EventLog()
    storage = InMemoryStorage()
    runtime = Runtime(
        context_builder=ContextBuilder(registry),
        reasoner=_ScriptedReasoner([FinishDecision(outcome="success", summary="fini", confidence=1.0)]),
        policy_engine=PolicyEngine(registry),
        executor=Executor(registry),
        event_log=event_log,
        storage=storage,
    )

    runtime.run("mission avec storage")
    runtime.persist_events()

    assert storage.load_events() == event_log.list_events()


def test_load_event_log_replays_events_in_order() -> None:
    storage = InMemoryStorage()
    first = Event(type=EventType.MISSION_CREATED)
    second = Event(type=EventType.MISSION_SUCCEEDED)
    storage.save_events([first, second])

    reloaded = Runtime.load_event_log(storage)

    assert reloaded.list_events() == [first, second]


def test_load_event_log_from_empty_storage_returns_an_empty_event_log() -> None:
    reloaded = Runtime.load_event_log(InMemoryStorage())

    assert reloaded.list_events() == []


def test_full_round_trip_through_storage_preserves_observations_for_the_context_builder() -> None:
    registry = _registry(_StubTool("read_file"))
    event_log = EventLog()
    storage = InMemoryStorage()
    runtime = Runtime(
        context_builder=ContextBuilder(registry),
        reasoner=_ScriptedReasoner(
            [
                ActionDecision(reasoning="lire", tool_name="read_file", arguments={}),
                FinishDecision(outcome="success", summary="fini", confidence=1.0),
            ]
        ),
        policy_engine=PolicyEngine(registry),
        executor=Executor(registry),
        event_log=event_log,
        storage=storage,
    )

    mission = runtime.run("lire un fichier puis arreter le process")
    runtime.persist_events()

    # Le processus "redemarre" : on ne reutilise ni le Runtime ni l'EventLog
    # d'origine, seulement ce qui a ete persiste dans Storage.
    reloaded_event_log = Runtime.load_event_log(storage)

    context = ContextBuilder(registry).build_from_event_log(
        mission_goal=mission.goal,
        mission_status=mission.status,
        mission_iteration_count=mission.iteration_count,
        event_log=reloaded_event_log,
    )

    assert len(context.observations) == 1
    observation = context.observations[0]
    assert observation.kind is ObservationKind.EXECUTION_RESULT
    assert observation.details == {"tool_name": "read_file", "output": "contenu du fichier"}
    assert context.observations == runtime.observations

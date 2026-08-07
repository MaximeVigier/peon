import uuid
from typing import Any

import pytest

from peon.context_builder import ContextBuilder, MalformedObservationEventError
from peon.event_log import EventLog
from peon.executor import Executor
from peon.models.context import Context
from peon.models.decision import ActionDecision, Decision, FinishDecision
from peon.models.events import Event, EventType
from peon.models.mission import MissionStatus
from peon.models.observation import Observation, ObservationKind
from peon.models.tool_result import ToolResult
from peon.models.tool_spec import RiskLevel, ToolSpec
from peon.policy import PolicyEngine
from peon.reasoner import Reasoner
from peon.runtime import Runtime
from peon.tool_registry import ToolRegistry
from peon.tools.base import Tool


def _registry(*tools: Tool) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    return registry


def _observation_event(observation: Observation) -> Event:
    return Event(type=EventType.OBSERVATION_PRODUCED, payload=observation.model_dump(mode="json"))


def test_build_from_event_log_extracts_observations_in_order() -> None:
    first = Observation(kind=ObservationKind.EXECUTION_RESULT, summary="premiere action")
    second = Observation(
        kind=ObservationKind.EXECUTION_ERROR,
        summary="deuxieme action a echoue",
        details={"tool_name": "read_file"},
    )
    event_log = EventLog()
    event_log.append(_observation_event(first))
    event_log.append(_observation_event(second))
    builder = ContextBuilder(_registry())

    context = builder.build_from_event_log(
        mission_goal="x",
        mission_status=MissionStatus.REASONING,
        mission_iteration_count=2,
        event_log=event_log,
    )

    assert context.observations == [first, second]


def test_build_from_event_log_with_empty_event_log_returns_no_observations() -> None:
    builder = ContextBuilder(_registry())

    context = builder.build_from_event_log(
        mission_goal="x",
        mission_status=MissionStatus.REASONING,
        mission_iteration_count=0,
        event_log=EventLog(),
    )

    assert context.observations == []


def test_build_from_event_log_ignores_non_observation_events() -> None:
    observation = Observation(kind=ObservationKind.SYSTEM_INFO, summary="info")
    event_log = EventLog()
    event_log.append(Event(type=EventType.MISSION_CREATED, payload={"mission_id": str(uuid.uuid4())}))
    event_log.append(Event(type=EventType.TOOL_EXECUTION_STARTED, payload={"tool_name": "read_file"}))
    event_log.append(_observation_event(observation))
    event_log.append(Event(type=EventType.STATE_TRANSITIONED, payload={"from": "executing", "to": "reasoning"}))
    event_log.append(Event(type=EventType.CONFIRMATION_GRANTED, payload={"request_id": str(uuid.uuid4())}))
    builder = ContextBuilder(_registry())

    context = builder.build_from_event_log(
        mission_goal="x",
        mission_status=MissionStatus.REASONING,
        mission_iteration_count=0,
        event_log=event_log,
    )

    assert context.observations == [observation]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"summary": "kind manquant"},
        {"kind": "not_a_real_kind", "summary": "kind invalide"},
        {"kind": ObservationKind.SYSTEM_INFO.value, "summary": ""},
    ],
)
def test_build_from_event_log_raises_on_malformed_observation_payload(payload: dict[str, Any]) -> None:
    event_log = EventLog()
    event_log.append(Event(type=EventType.OBSERVATION_PRODUCED, payload=payload))
    builder = ContextBuilder(_registry())

    with pytest.raises(MalformedObservationEventError):
        builder.build_from_event_log(
            mission_goal="x",
            mission_status=MissionStatus.REASONING,
            mission_iteration_count=0,
            event_log=event_log,
        )


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
        return ToolResult(success=True, output="ok")


class _RecordingReasoner(Reasoner):
    # Decisions fixees a l'avance, jamais devinees depuis le Context : reproduit
    # une sequence de reponses LLM deja connue, sans comportement non deterministe.
    def __init__(self, decisions: list[Decision]) -> None:
        self._decisions = list(decisions)
        self.received_contexts: list[Context] = []

    def decide(self, context: Context) -> Decision:
        self.received_contexts.append(context)
        return self._decisions.pop(0)


def test_runtime_feeds_the_reasoner_a_context_built_from_the_event_log() -> None:
    registry = _registry(_StubTool("read_file"))
    reasoner = _RecordingReasoner(
        [
            ActionDecision(reasoning="lire", tool_name="read_file", arguments={}),
            FinishDecision(outcome="success", summary="fait", confidence=1.0),
        ]
    )
    event_log = EventLog()
    runtime = Runtime(
        context_builder=ContextBuilder(registry),
        reasoner=reasoner,
        policy_engine=PolicyEngine(registry),
        executor=Executor(registry),
        event_log=event_log,
    )

    runtime.run("lire un fichier")

    assert reasoner.received_contexts[0].observations == []

    observation_events = event_log.list_events_by_type(EventType.OBSERVATION_PRODUCED)
    assert len(observation_events) == 1
    assert reasoner.received_contexts[1].observations == [Observation(**observation_events[0].payload)]

import uuid
from typing import Any, Literal

import pytest

from peon.context_builder import ContextBuilder
from peon.event_log import EventLog
from peon.executor import Executor
from peon.models.confirmation import ConfirmationResponse
from peon.models.context import Context
from peon.models.decision import ActionDecision, Decision, FinishDecision
from peon.models.events import EventType
from peon.models.mission import Mission, MissionStatus
from peon.models.observation import ObservationKind
from peon.models.tool_result import ToolResult
from peon.models.tool_spec import RiskLevel, ToolSpec
from peon.policy import PolicyEngine
from peon.reasoner import Reasoner
from peon.runtime import (
    ConfirmationMissionMismatchError,
    Runtime,
    UnknownConfirmationRequestError,
)
from peon.tool_registry import ToolRegistry
from peon.tools.base import Tool


class _StubTool(Tool):
    def __init__(self, name: str, risk_level: RiskLevel, result: ToolResult | None = None) -> None:
        self._spec = ToolSpec(
            name=name,
            description=f"Outil {name}",
            parameters_schema={"type": "object", "properties": {}},
            risk_level=risk_level,
        )
        self._result = result if result is not None else ToolResult(success=True, output="ok")

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        return self._result


class _ScriptedReasoner(Reasoner):
    # Decisions fixees a l'avance, jamais devinees depuis le Context : reproduit
    # une sequence de reponses LLM deja connue, sans comportement non deterministe.
    def __init__(self, decisions: list[Decision]) -> None:
        self._decisions = list(decisions)

    def decide(self, context: Context) -> Decision:
        return self._decisions.pop(0)


def _action_decision(tool_name: str, **arguments: Any) -> ActionDecision:
    return ActionDecision(reasoning="x", tool_name=tool_name, arguments=arguments)


def _finish(outcome: Literal["success", "failure"], summary: str = "termine") -> FinishDecision:
    return FinishDecision(outcome=outcome, summary=summary, confidence=1.0)


def _registry(*tools: Tool) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    return registry


def _runtime(registry: ToolRegistry, reasoner: Reasoner) -> tuple[Runtime, EventLog]:
    event_log = EventLog()
    runtime = Runtime(
        context_builder=ContextBuilder(registry),
        reasoner=reasoner,
        policy_engine=PolicyEngine(registry),
        executor=Executor(registry),
        event_log=event_log,
    )
    return runtime, event_log


def test_high_risk_action_produces_a_confirmation_request() -> None:
    reasoner = _ScriptedReasoner([_action_decision("git_push")])
    runtime, event_log = _runtime(_registry(_StubTool("git_push", RiskLevel.HIGH)), reasoner)

    mission = runtime.run("publier les changements")

    assert mission.status is MissionStatus.AWAITING_CONFIRMATION
    request = runtime.pending_confirmation
    assert request is not None
    assert request.mission_id == mission.id
    assert request.action.tool_name == "git_push"
    assert [e.type for e in event_log.list_events()][-2:] == [
        EventType.STATE_TRANSITIONED,
        EventType.CONFIRMATION_REQUESTED,
    ]


def test_granted_confirmation_executes_the_suspended_action() -> None:
    reasoner = _ScriptedReasoner([_action_decision("git_push"), _finish("success")])
    runtime, event_log = _runtime(_registry(_StubTool("git_push", RiskLevel.HIGH)), reasoner)

    mission = runtime.run("publier les changements")
    request_id = runtime.pending_confirmation.id

    resumed = runtime.resume_confirmation(mission, ConfirmationResponse(request_id=request_id, granted=True))

    assert resumed is mission
    assert mission.status is MissionStatus.SUCCEEDED
    assert runtime.pending_confirmation is None

    assert len(runtime.observations) == 1
    assert runtime.observations[0].kind is ObservationKind.EXECUTION_RESULT
    assert runtime.observations[0].details["tool_name"] == "git_push"

    event_types = [e.type for e in event_log.list_events()]
    assert EventType.CONFIRMATION_GRANTED in event_types
    assert EventType.TOOL_EXECUTION_COMPLETED in event_types
    assert event_types.index(EventType.CONFIRMATION_GRANTED) < event_types.index(EventType.TOOL_EXECUTION_COMPLETED)


def test_denied_confirmation_produces_an_observation_and_returns_to_reasoning() -> None:
    reasoner = _ScriptedReasoner([_action_decision("git_push"), _finish("failure")])
    runtime, event_log = _runtime(_registry(_StubTool("git_push", RiskLevel.HIGH)), reasoner)

    mission = runtime.run("publier les changements")
    request_id = runtime.pending_confirmation.id

    resumed = runtime.resume_confirmation(
        mission, ConfirmationResponse(request_id=request_id, granted=False, note="pas maintenant")
    )

    assert resumed is mission
    assert runtime.pending_confirmation is None
    assert len(runtime.observations) == 1
    assert runtime.observations[0].kind is ObservationKind.CONFIRMATION_DENIED
    assert runtime.observations[0].details["note"] == "pas maintenant"

    payloads = [(e.type, e.payload) for e in event_log.list_events()]
    denied_index = next(i for i, (t, _) in enumerate(payloads) if t is EventType.CONFIRMATION_DENIED)
    transition_type, transition_payload = payloads[denied_index + 1]
    assert transition_type is EventType.STATE_TRANSITIONED
    assert transition_payload["from"] == "awaiting_confirmation"
    assert transition_payload["to"] == "reasoning"

    # La boucle reactive reprend ensuite normalement : le Reasoner est rappele
    # et la mission se termine (echec, scripte) au lieu de rester bloquee.
    assert mission.status is MissionStatus.FAILED


def test_resume_with_unknown_request_id_fails_cleanly() -> None:
    reasoner = _ScriptedReasoner([_action_decision("git_push")])
    runtime, _ = _runtime(_registry(_StubTool("git_push", RiskLevel.HIGH)), reasoner)

    mission = runtime.run("publier les changements")

    with pytest.raises(UnknownConfirmationRequestError):
        runtime.resume_confirmation(mission, ConfirmationResponse(request_id=uuid.uuid4(), granted=True))

    # Etat inchange : la confirmation en attente n'a pas ete consommee par erreur.
    assert runtime.pending_confirmation is not None
    assert mission.status is MissionStatus.AWAITING_CONFIRMATION


def test_resume_for_the_wrong_mission_fails_cleanly() -> None:
    reasoner = _ScriptedReasoner([_action_decision("git_push")])
    runtime, _ = _runtime(_registry(_StubTool("git_push", RiskLevel.HIGH)), reasoner)

    mission = runtime.run("publier les changements")
    request_id = runtime.pending_confirmation.id
    other_mission = Mission(goal="une autre mission sans rapport")

    with pytest.raises(ConfirmationMissionMismatchError):
        runtime.resume_confirmation(other_mission, ConfirmationResponse(request_id=request_id, granted=True))

    assert runtime.pending_confirmation is not None
    assert mission.status is MissionStatus.AWAITING_CONFIRMATION

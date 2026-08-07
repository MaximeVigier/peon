from typing import Any, Literal

from peon.context_builder import ContextBuilder
from peon.event_log import EventLog
from peon.executor import Executor
from peon.models.context import Context
from peon.models.decision import ActionDecision, Decision, FinishDecision
from peon.models.events import EventType
from peon.models.mission import MissionStatus
from peon.models.observation import ObservationKind
from peon.models.tool_result import ToolResult
from peon.models.tool_spec import RiskLevel, ToolSpec
from peon.policy import PolicyEngine
from peon.reasoner import Reasoner
from peon.runtime import Runtime
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


def _registry(*tools: Tool) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    return registry


def _action_decision(tool_name: str, **arguments: Any) -> ActionDecision:
    return ActionDecision(reasoning="x", tool_name=tool_name, arguments=arguments)


def _finish(outcome: Literal["success", "failure"], summary: str = "termine") -> FinishDecision:
    return FinishDecision(outcome=outcome, summary=summary, confidence=1.0)


class _ScriptedReasoner(Reasoner):
    def __init__(self, decisions: list[Decision]) -> None:
        self._decisions = list(decisions)
        self.received_contexts: list[Context] = []

    def decide(self, context: Context) -> Decision:
        self.received_contexts.append(context)
        return self._decisions.pop(0)


class _RepeatingReasoner(Reasoner):
    def __init__(self, decision: Decision) -> None:
        self._decision = decision
        self.call_count = 0

    def decide(self, context: Context) -> Decision:
        self.call_count += 1
        return self._decision


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


def test_immediate_success_finishes_the_mission() -> None:
    reasoner = _ScriptedReasoner([_finish("success")])
    runtime, event_log = _runtime(_registry(), reasoner)

    mission = runtime.run("corriger le bug de parsing")

    assert mission.status is MissionStatus.SUCCEEDED
    assert mission.iteration_count == 1
    assert [event.type for event in event_log.list_events()] == [
        EventType.MISSION_CREATED,
        EventType.STATE_TRANSITIONED,
        EventType.CONTEXT_BUILT,
        EventType.DECISION_RECEIVED,
        EventType.MISSION_SUCCEEDED,
        EventType.STATE_TRANSITIONED,
    ]


def test_immediate_failure_fails_the_mission() -> None:
    reasoner = _ScriptedReasoner([_finish("failure")])
    runtime, _ = _runtime(_registry(), reasoner)

    mission = runtime.run("tache impossible")

    assert mission.status is MissionStatus.FAILED


def test_allowed_action_executes_then_mission_finishes() -> None:
    reasoner = _ScriptedReasoner([_action_decision("read_file", path="README.md"), _finish("success")])
    runtime, event_log = _runtime(_registry(_StubTool("read_file", RiskLevel.LOW)), reasoner)

    mission = runtime.run("lire le readme")

    assert mission.status is MissionStatus.SUCCEEDED
    assert mission.iteration_count == 2
    assert len(runtime.observations) == 1
    assert runtime.observations[0].kind is ObservationKind.EXECUTION_RESULT
    assert EventType.TOOL_EXECUTION_COMPLETED in [e.type for e in event_log.list_events()]


def test_second_context_includes_the_observation_from_the_first_cycle() -> None:
    reasoner = _ScriptedReasoner([_action_decision("read_file"), _finish("success")])
    runtime, _ = _runtime(_registry(_StubTool("read_file", RiskLevel.LOW)), reasoner)

    runtime.run("lire le readme")

    assert reasoner.received_contexts[0].observations == []
    assert len(reasoner.received_contexts[1].observations) == 1
    observation = reasoner.received_contexts[1].observations[0]
    assert observation.kind is ObservationKind.EXECUTION_RESULT
    # Round-trip par l'EventLog (build_from_event_log) : les details doivent
    # survivre, pas seulement kind/summary.
    assert observation.details == {"tool_name": "read_file", "output": "ok"}


def test_high_risk_action_pauses_on_confirmation() -> None:
    reasoner = _ScriptedReasoner([_action_decision("git_push")])
    runtime, event_log = _runtime(_registry(_StubTool("git_push", RiskLevel.HIGH)), reasoner)

    mission = runtime.run("publier les changements")

    assert mission.status is MissionStatus.AWAITING_CONFIRMATION
    assert runtime.pending_confirmation is not None
    assert runtime.pending_confirmation.action.tool_name == "git_push"
    assert EventType.CONFIRMATION_REQUESTED in [e.type for e in event_log.list_events()]


def test_denied_action_produces_an_observation_and_continues() -> None:
    reasoner = _ScriptedReasoner([_action_decision("run_command", command="rm -rf /"), _finish("failure")])
    runtime, _ = _runtime(_registry(_StubTool("run_command", RiskLevel.HIGH)), reasoner)

    mission = runtime.run("nettoyer le disque")

    assert mission.status is MissionStatus.FAILED
    assert mission.iteration_count == 2
    assert len(runtime.observations) == 1
    assert runtime.observations[0].kind is ObservationKind.POLICY_REJECTION


def test_rewrite_action_carries_the_suggestion_in_the_observation() -> None:
    reasoner = _ScriptedReasoner([_action_decision("run_command", command="rm -rf build"), _finish("success")])
    runtime, _ = _runtime(_registry(_StubTool("run_command", RiskLevel.HIGH)), reasoner)

    runtime.run("nettoyer le build")

    observation = runtime.observations[0]
    assert observation.kind is ObservationKind.POLICY_REJECTION
    assert observation.details is not None
    assert observation.details["suggested_arguments"]["command"] == "rm -rf -- ./build"


def test_unknown_tool_is_denied_without_crashing() -> None:
    reasoner = _ScriptedReasoner([_action_decision("does_not_exist"), _finish("failure")])
    runtime, _ = _runtime(_registry(), reasoner)

    mission = runtime.run("appeler un outil inexistant")

    assert mission.status is MissionStatus.FAILED
    assert runtime.observations[0].kind is ObservationKind.POLICY_REJECTION


def test_max_iterations_stops_the_loop() -> None:
    reasoner = _RepeatingReasoner(_action_decision("read_file"))
    runtime, _ = _runtime(_registry(_StubTool("read_file", RiskLevel.LOW)), reasoner)

    mission = runtime.run("boucle infinie", max_iterations=2)

    assert mission.status is MissionStatus.MAX_ITERATIONS
    assert mission.iteration_count == 3
    assert reasoner.call_count == 2


def test_tool_execution_error_produces_an_execution_error_observation() -> None:
    failing_result = ToolResult(success=False, error="fichier introuvable")
    reasoner = _ScriptedReasoner([_action_decision("read_file"), _finish("failure")])
    runtime, _ = _runtime(_registry(_StubTool("read_file", RiskLevel.LOW, result=failing_result)), reasoner)

    runtime.run("lire un fichier absent")

    assert runtime.observations[0].kind is ObservationKind.EXECUTION_ERROR

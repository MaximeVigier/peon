import uuid
from typing import Any, Literal

import pytest

from peon.context_builder import ContextBuilder
from peon.event_log import EventLog
from peon.executor import Executor
from peon.models.action import Action
from peon.models.checkpoint import Checkpoint
from peon.models.confirmation import ConfirmationRequest, ConfirmationResponse
from peon.models.context import Context
from peon.models.decision import ActionDecision, Decision, FinishDecision
from peon.models.mission import Mission, MissionStatus
from peon.models.observation import ObservationKind
from peon.models.tool_result import ToolResult
from peon.models.tool_spec import RiskLevel, ToolSpec
from peon.policy import PolicyEngine
from peon.reasoner import Reasoner
from peon.runtime import Runtime, StorageNotConfiguredError, UnknownConfirmationRequestError
from peon.storage import InMemoryStorage, Storage
from peon.tool_registry import ToolRegistry
from peon.tools.base import Tool


class _StubTool(Tool):
    def __init__(self, name: str, risk_level: RiskLevel) -> None:
        self._spec = ToolSpec(
            name=name,
            description=f"Outil {name}",
            parameters_schema={"type": "object", "properties": {}},
            risk_level=risk_level,
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        return ToolResult(success=True, output="ok")


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


def _runtime(registry: ToolRegistry, reasoner: Reasoner, *, storage: Storage | None = None) -> tuple[Runtime, EventLog]:
    event_log = EventLog()
    runtime = Runtime(
        context_builder=ContextBuilder(registry),
        reasoner=reasoner,
        policy_engine=PolicyEngine(registry),
        executor=Executor(registry),
        event_log=event_log,
        storage=storage,
    )
    return runtime, event_log


def _confirmation_request(mission: Mission) -> ConfirmationRequest:
    action = Action(
        reasoning="publier les changements",
        tool_name="git_push",
        arguments={"branch": "main"},
        risk_level=RiskLevel.HIGH,
    )
    return ConfirmationRequest(mission_id=mission.id, action=action, reason="commande jugee dangereuse")


# --- A. Round-trip -----------------------------------------------------------


def test_checkpoint_round_trips_through_storage() -> None:
    mission = Mission(goal="deployer le service", max_iterations=5)
    mission.status = MissionStatus.AWAITING_CONFIRMATION
    mission.iteration_count = 2
    request = _confirmation_request(mission)
    checkpoint = Checkpoint(mission=mission, pending_confirmation=request)
    storage = InMemoryStorage()

    storage.save_checkpoint(checkpoint)
    reloaded = storage.load_checkpoint()

    assert reloaded == checkpoint
    assert reloaded is not checkpoint
    assert reloaded.mission is not mission


def test_checkpoint_serializes_to_and_from_json() -> None:
    mission = Mission(goal="deployer le service")
    request = _confirmation_request(mission)
    checkpoint = Checkpoint(mission=mission, pending_confirmation=request)

    reloaded = Checkpoint.model_validate_json(checkpoint.model_dump_json())

    assert reloaded == checkpoint


def test_checkpoint_without_pending_confirmation_round_trips() -> None:
    mission = Mission(goal="lire un fichier")
    checkpoint = Checkpoint(mission=mission, pending_confirmation=None)
    storage = InMemoryStorage()

    storage.save_checkpoint(checkpoint)
    reloaded = storage.load_checkpoint()

    assert reloaded == checkpoint
    assert reloaded.pending_confirmation is None


# --- B. Crash simule : deux instances Runtime distinctes ---------------------


def test_crash_and_resume_restores_state_on_a_brand_new_runtime() -> None:
    storage = InMemoryStorage()
    registry = _registry(_StubTool("git_push", RiskLevel.HIGH))

    runtime_a, _ = _runtime(registry, _ScriptedReasoner([_action_decision("git_push")]), storage=storage)
    mission = runtime_a.run("publier les changements")
    assert mission.status is MissionStatus.AWAITING_CONFIRMATION

    runtime_a.save_checkpoint(mission)
    del runtime_a  # simule l'arret du process : plus aucune reference vers Runtime A

    loaded_checkpoint = storage.load_checkpoint()
    assert loaded_checkpoint is not None

    runtime_b, event_log_b = _runtime(registry, _ScriptedReasoner([_finish("success")]), storage=storage)
    resumed_mission = runtime_b.resume_mission(loaded_checkpoint)

    assert resumed_mission.id == mission.id
    assert resumed_mission.status is MissionStatus.AWAITING_CONFIRMATION
    assert runtime_b.pending_confirmation is not None
    assert runtime_b.pending_confirmation.action.tool_name == "git_push"
    # Nouveau Runtime = EventLog vierge : rien n'est rejoue automatiquement.
    assert event_log_b.list_events() == []


def test_save_checkpoint_without_storage_fails_cleanly() -> None:
    registry = _registry(_StubTool("git_push", RiskLevel.HIGH))
    runtime, _ = _runtime(registry, _ScriptedReasoner([_action_decision("git_push")]))

    mission = runtime.run("publier les changements")

    with pytest.raises(StorageNotConfiguredError):
        runtime.save_checkpoint(mission)


# --- C. Reprise d'une confirmation apres redemarrage --------------------------


def test_resume_confirmation_after_restart_when_granted_executes_the_action() -> None:
    storage = InMemoryStorage()
    registry = _registry(_StubTool("git_push", RiskLevel.HIGH))

    runtime_a, _ = _runtime(registry, _ScriptedReasoner([_action_decision("git_push")]), storage=storage)
    mission = runtime_a.run("publier les changements")
    runtime_a.save_checkpoint(mission)
    request_id = runtime_a.pending_confirmation.id
    del runtime_a

    checkpoint = storage.load_checkpoint()
    runtime_b, _ = _runtime(registry, _ScriptedReasoner([_finish("success")]), storage=storage)
    resumed_mission = runtime_b.resume_mission(checkpoint)

    result = runtime_b.resume_confirmation(
        resumed_mission, ConfirmationResponse(request_id=request_id, granted=True)
    )

    assert result.status is MissionStatus.SUCCEEDED
    assert runtime_b.pending_confirmation is None
    assert len(runtime_b.observations) == 1
    assert runtime_b.observations[0].kind is ObservationKind.EXECUTION_RESULT
    assert runtime_b.observations[0].details["tool_name"] == "git_push"


def test_resume_confirmation_after_restart_when_denied_returns_to_reasoning() -> None:
    storage = InMemoryStorage()
    registry = _registry(_StubTool("git_push", RiskLevel.HIGH))

    runtime_a, _ = _runtime(registry, _ScriptedReasoner([_action_decision("git_push")]), storage=storage)
    mission = runtime_a.run("publier les changements")
    runtime_a.save_checkpoint(mission)
    request_id = runtime_a.pending_confirmation.id
    del runtime_a

    checkpoint = storage.load_checkpoint()
    runtime_b, _ = _runtime(registry, _ScriptedReasoner([_finish("failure")]), storage=storage)
    resumed_mission = runtime_b.resume_mission(checkpoint)

    result = runtime_b.resume_confirmation(
        resumed_mission, ConfirmationResponse(request_id=request_id, granted=False, note="pas maintenant")
    )

    assert result.status is MissionStatus.FAILED
    assert runtime_b.pending_confirmation is None
    assert len(runtime_b.observations) == 1
    assert runtime_b.observations[0].kind is ObservationKind.CONFIRMATION_DENIED
    assert runtime_b.observations[0].details["note"] == "pas maintenant"


def test_resume_confirmation_after_restart_with_wrong_request_id_fails_cleanly() -> None:
    storage = InMemoryStorage()
    registry = _registry(_StubTool("git_push", RiskLevel.HIGH))

    runtime_a, _ = _runtime(registry, _ScriptedReasoner([_action_decision("git_push")]), storage=storage)
    mission = runtime_a.run("publier les changements")
    runtime_a.save_checkpoint(mission)
    del runtime_a

    checkpoint = storage.load_checkpoint()
    runtime_b, _ = _runtime(registry, _ScriptedReasoner([]), storage=storage)
    resumed_mission = runtime_b.resume_mission(checkpoint)

    with pytest.raises(UnknownConfirmationRequestError):
        runtime_b.resume_confirmation(
            resumed_mission, ConfirmationResponse(request_id=uuid.uuid4(), granted=True)
        )


# --- D. Absence de checkpoint --------------------------------------------------


def test_load_checkpoint_from_empty_storage_returns_none() -> None:
    storage = InMemoryStorage()

    assert storage.load_checkpoint() is None


# --- E. Fidelite des champs importants -----------------------------------------


def test_round_trip_preserves_mission_and_confirmation_fields() -> None:
    mission = Mission(goal="deployer le service", max_iterations=7)
    mission.status = MissionStatus.AWAITING_CONFIRMATION
    mission.iteration_count = 3
    request = _confirmation_request(mission)
    checkpoint = Checkpoint(mission=mission, pending_confirmation=request)
    storage = InMemoryStorage()

    storage.save_checkpoint(checkpoint)
    reloaded = storage.load_checkpoint()

    assert reloaded.mission.id == mission.id
    assert reloaded.mission.goal == mission.goal
    assert reloaded.mission.status == mission.status
    assert reloaded.mission.iteration_count == mission.iteration_count
    assert reloaded.mission.max_iterations == mission.max_iterations

    assert reloaded.pending_confirmation.id == request.id
    assert reloaded.pending_confirmation.mission_id == request.mission_id
    assert reloaded.pending_confirmation.reason == request.reason
    assert reloaded.pending_confirmation.action.tool_name == request.action.tool_name
    assert reloaded.pending_confirmation.action.arguments == request.action.arguments
    assert reloaded.pending_confirmation.action.risk_level == request.action.risk_level

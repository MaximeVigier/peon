from typing import Any

from peon.context_builder import ContextBuilder
from peon.event_log import EventLog
from peon.executor import Executor
from peon.models.action import Action
from peon.models.checkpoint import Checkpoint
from peon.models.confirmation import ConfirmationRequest, ConfirmationResponse
from peon.models.context import Context
from peon.models.decision import ActionDecision, FinishDecision
from peon.models.events import Event, EventType
from peon.models.execution_error import ExecutionError
from peon.models.mission import Mission, MissionStatus
from peon.models.observation import Observation, ObservationKind
from peon.models.tool_spec import RiskLevel
from peon.models.verdict import Verdict, VerdictType
from peon.policy import PolicyEngine
from peon.reasoner import Reasoner
from peon.storage import Storage
from peon.state_machine import (
    ConfirmationDenied,
    ConfirmationGranted,
    MaxIterationsReached,
    MissionCreated,
    MissionEvent,
    MissionFailed,
    MissionSucceeded,
    PolicyEvaluated,
    ToolExecutionFinished,
    transition,
)
from peon.tracing import NoOpTracer, Tracer


class UnknownConfirmationRequestError(Exception):
    pass


class ConfirmationMissionMismatchError(Exception):
    pass


class StorageNotConfiguredError(Exception):
    pass


class Runtime:
    def __init__(
        self,
        *,
        context_builder: ContextBuilder,
        reasoner: Reasoner,
        policy_engine: PolicyEngine,
        executor: Executor,
        event_log: EventLog,
        storage: Storage | None = None,
        tracer: Tracer | None = None,
    ) -> None:
        self._context_builder = context_builder
        self._reasoner = reasoner
        self._policy_engine = policy_engine
        self._executor = executor
        self._event_log = event_log
        self._storage = storage
        self._tracer = tracer if tracer is not None else NoOpTracer()
        self._observations: list[Observation] = []
        self._pending_confirmation: ConfirmationRequest | None = None

    @property
    def observations(self) -> list[Observation]:
        return list(self._observations)

    @property
    def pending_confirmation(self) -> ConfirmationRequest | None:
        return self._pending_confirmation

    def persist_events(self) -> None:
        # Instantane complet de l'EventLog courant vers Storage : pas de suivi
        # incremental/delta, hors perimetre de cette phase (voir CONTEXT.md).
        if self._storage is None:
            raise StorageNotConfiguredError("no Storage was injected into this Runtime")
        self._storage.save_events(self._event_log.list_events())

    @staticmethod
    def load_event_log(storage: Storage) -> EventLog:
        event_log = EventLog()
        for event in storage.load_events():
            event_log.append(event)
        return event_log

    def save_checkpoint(self, mission: Mission) -> Checkpoint:
        # Instantane explicite, a la demande (meme philosophie que
        # persist_events()) : jamais declenche automatiquement par le cycle de
        # raisonnement. Le point pertinent pour cette phase est apres que
        # run()/resume_confirmation() ait rendu la main en AWAITING_CONFIRMATION,
        # mais rien n'empeche d'appeler cette methode a un autre moment.
        if self._storage is None:
            raise StorageNotConfiguredError("no Storage was injected into this Runtime")
        checkpoint = Checkpoint(mission=mission, pending_confirmation=self._pending_confirmation)
        self._storage.save_checkpoint(checkpoint)
        return checkpoint

    def resume_mission(self, checkpoint: Checkpoint) -> Mission:
        # Restaure uniquement ce qu'un nouveau Runtime a besoin de savoir pour
        # que resume_confirmation() fonctionne : la Mission et la confirmation
        # en attente. Ne rejoue aucun evenement (EventLog reste vierge tant que
        # load_event_log() n'est pas appele separement) et ne duplique aucune
        # logique de la boucle ReAct existante.
        self._pending_confirmation = checkpoint.pending_confirmation
        self._observations = []
        return checkpoint.mission

    def run(self, goal: str, *, max_iterations: int | None = None) -> Mission:
        with self._tracer.start_span("runtime.run"):
            mission = (
                Mission(goal=goal) if max_iterations is None else Mission(goal=goal, max_iterations=max_iterations)
            )
            self._observations = []
            self._pending_confirmation = None

            self._log_event(EventType.MISSION_CREATED, {"mission_id": str(mission.id), "goal": mission.goal})
            self._advance(mission, MissionCreated())

            self._run_reasoning_loop(mission)

            return mission

    def resume_confirmation(self, mission: Mission, response: ConfirmationResponse) -> Mission:
        with self._tracer.start_span("runtime.resume_confirmation"):
            request = self._take_pending_confirmation(mission, response)

            if response.granted:
                self._log_event(
                    EventType.CONFIRMATION_GRANTED,
                    {"request_id": str(request.id), "tool_name": request.action.tool_name},
                )
                self._advance(mission, ConfirmationGranted())
                self._execute_action(mission, request.action)
            else:
                self._log_event(
                    EventType.CONFIRMATION_DENIED,
                    {"request_id": str(request.id), "tool_name": request.action.tool_name, "note": response.note},
                )
                self._advance(mission, ConfirmationDenied())
                self._record_observation(self._confirmation_denied_observation(request, response))

            self._run_reasoning_loop(mission)

            return mission

    def _take_pending_confirmation(self, mission: Mission, response: ConfirmationResponse) -> ConfirmationRequest:
        request = self._pending_confirmation
        if request is None or request.id != response.request_id:
            raise UnknownConfirmationRequestError(
                f"no pending confirmation matches request_id '{response.request_id}'"
            )
        if request.mission_id != mission.id:
            raise ConfirmationMissionMismatchError(
                f"confirmation request '{request.id}' belongs to mission '{request.mission_id}', not '{mission.id}'"
            )
        self._pending_confirmation = None
        return request

    @staticmethod
    def _confirmation_denied_observation(request: ConfirmationRequest, response: ConfirmationResponse) -> Observation:
        details: dict[str, Any] = {"tool_name": request.action.tool_name, "reason": request.reason}
        if response.note is not None:
            details["note"] = response.note
        return Observation(
            kind=ObservationKind.CONFIRMATION_DENIED,
            summary=f"confirmation refusee par l'utilisateur pour l'outil '{request.action.tool_name}'",
            details=details,
        )

    def _run_reasoning_loop(self, mission: Mission) -> None:
        while mission.status is MissionStatus.REASONING:
            self._run_reasoning_cycle(mission)

    def _run_reasoning_cycle(self, mission: Mission) -> None:
        mission.iteration_count += 1
        if mission.iteration_count > mission.max_iterations:
            self._log_event(EventType.MAX_ITERATIONS_REACHED, {"iteration_count": mission.iteration_count})
            self._advance(mission, MaxIterationsReached())
            return

        context = self._context_builder.build_from_event_log(
            mission_goal=mission.goal,
            mission_status=mission.status,
            mission_iteration_count=mission.iteration_count,
            event_log=self._event_log,
        )
        self._log_event(
            EventType.CONTEXT_BUILT,
            {
                "observation_count": len(context.observations),
                "available_tool_count": len(context.available_tools),
            },
        )

        with self._tracer.start_span("reasoner.decide"):
            decision = self._reasoner.decide(context)
        self._log_event(EventType.DECISION_RECEIVED, {"kind": decision.kind})

        if isinstance(decision, FinishDecision):
            self._finish_mission(mission, decision)
            return

        self._handle_action_decision(mission, context, decision)

    def _finish_mission(self, mission: Mission, decision: FinishDecision) -> None:
        if decision.outcome == "success":
            self._log_event(EventType.MISSION_SUCCEEDED, {"summary": decision.summary})
            self._advance(mission, MissionSucceeded())
        else:
            self._log_event(EventType.MISSION_FAILED, {"summary": decision.summary})
            self._advance(mission, MissionFailed())

    def _handle_action_decision(self, mission: Mission, context: Context, decision: ActionDecision) -> None:
        action = self._to_action(decision, context)

        verdict = self._policy_engine.evaluate(action)
        self._log_event(EventType.POLICY_EVALUATED, {"type": verdict.type.value, "reason": verdict.reason})
        self._advance(mission, PolicyEvaluated(verdict=verdict))

        if verdict.type is VerdictType.ALLOWED:
            self._execute_action(mission, action)
        elif verdict.type is VerdictType.REQUIRES_CONFIRMATION:
            self._request_confirmation(mission, action, verdict)
        else:
            self._reject_action(verdict)

    def _to_action(self, decision: ActionDecision, context: Context) -> Action:
        tool_spec = next((spec for spec in context.available_tools if spec.name == decision.tool_name), None)
        risk_level = tool_spec.risk_level if tool_spec is not None else RiskLevel.HIGH
        return Action(
            reasoning=decision.reasoning,
            tool_name=decision.tool_name,
            arguments=decision.arguments,
            risk_level=risk_level,
        )

    def _execute_action(self, mission: Mission, action: Action) -> None:
        with self._tracer.start_span("executor.run", tool_name=action.tool_name):
            result = self._executor.run(action)

        if isinstance(result, ExecutionError):
            self._log_event(
                EventType.TOOL_EXECUTION_FAILED,
                {"tool_name": action.tool_name, "category": result.category.value, "message": result.message},
            )
            observation = Observation(
                kind=ObservationKind.EXECUTION_ERROR,
                summary=result.message,
                details={"tool_name": action.tool_name, "category": result.category.value, **(result.details or {})},
            )
        else:
            self._log_event(
                EventType.TOOL_EXECUTION_COMPLETED,
                {"tool_name": action.tool_name, "return_code": result.return_code},
            )
            observation = Observation(
                kind=ObservationKind.EXECUTION_RESULT,
                summary=f"outil '{action.tool_name}' execute avec succes",
                details={"tool_name": action.tool_name, "output": result.output},
            )

        self._advance(mission, ToolExecutionFinished())
        self._record_observation(observation)

    def _request_confirmation(self, mission: Mission, action: Action, verdict: Verdict) -> None:
        request = ConfirmationRequest(mission_id=mission.id, action=action, reason=verdict.reason)
        self._pending_confirmation = request
        self._log_event(
            EventType.CONFIRMATION_REQUESTED,
            {"request_id": str(request.id), "tool_name": action.tool_name, "reason": request.reason},
        )

    def _reject_action(self, verdict: Verdict) -> None:
        details: dict[str, Any] = {"verdict_type": verdict.type.value}
        if verdict.suggested_action is not None:
            details["suggested_tool_name"] = verdict.suggested_action.tool_name
            details["suggested_arguments"] = verdict.suggested_action.arguments
        observation = Observation(kind=ObservationKind.POLICY_REJECTION, summary=verdict.reason, details=details)
        self._record_observation(observation)

    def _record_observation(self, observation: Observation) -> None:
        self._observations.append(observation)
        # model_dump(mode="json") pour que le payload reste exactement ce qui
        # permet a ContextBuilder.build_from_event_log de reconstruire un
        # Observation fidele (kind + summary + details), pas juste un resume.
        self._log_event(EventType.OBSERVATION_PRODUCED, observation.model_dump(mode="json"))

    def _advance(self, mission: Mission, event: MissionEvent) -> None:
        previous_status = mission.status
        next_status = transition(previous_status, event)
        mission.status = next_status
        self._log_event(
            EventType.STATE_TRANSITIONED,
            {"from": previous_status.value, "to": next_status.value, "event": event.kind},
        )

    def _log_event(self, event_type: EventType, payload: dict[str, Any]) -> None:
        self._event_log.append(Event(type=event_type, payload=payload))

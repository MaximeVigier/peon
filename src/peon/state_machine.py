from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

from peon.models.mission import MissionStatus
from peon.models.verdict import Verdict, VerdictType


class _Event(BaseModel):
    model_config = ConfigDict(frozen=True)


class MissionCreated(_Event):
    kind: Literal["mission_created"] = "mission_created"


class PolicyEvaluated(_Event):
    kind: Literal["policy_evaluated"] = "policy_evaluated"
    verdict: Verdict


class MissionSucceeded(_Event):
    kind: Literal["mission_succeeded"] = "mission_succeeded"


class MissionFailed(_Event):
    kind: Literal["mission_failed"] = "mission_failed"


class MaxIterationsReached(_Event):
    kind: Literal["max_iterations_reached"] = "max_iterations_reached"


class ConfirmationGranted(_Event):
    kind: Literal["confirmation_granted"] = "confirmation_granted"


class ConfirmationDenied(_Event):
    kind: Literal["confirmation_denied"] = "confirmation_denied"


class ToolExecutionFinished(_Event):
    kind: Literal["tool_execution_finished"] = "tool_execution_finished"


class AbortRequested(_Event):
    kind: Literal["abort_requested"] = "abort_requested"


MissionEvent = Annotated[
    Union[
        MissionCreated,
        PolicyEvaluated,
        MissionSucceeded,
        MissionFailed,
        MaxIterationsReached,
        ConfirmationGranted,
        ConfirmationDenied,
        ToolExecutionFinished,
        AbortRequested,
    ],
    Field(discriminator="kind"),
]


class InvalidTransitionError(Exception):
    def __init__(self, state: MissionStatus, event: MissionEvent) -> None:
        super().__init__(f"event '{event.kind}' is not valid from state '{state.value}'")
        self.state = state
        self.event = event


_TERMINAL_STATES = frozenset(
    {
        MissionStatus.SUCCEEDED,
        MissionStatus.FAILED,
        MissionStatus.MAX_ITERATIONS,
        MissionStatus.ABORTED,
    }
)

_VERDICT_TRANSITIONS: dict[VerdictType, MissionStatus] = {
    VerdictType.ALLOWED: MissionStatus.EXECUTING,
    VerdictType.DENIED: MissionStatus.REASONING,
    VerdictType.REWRITE: MissionStatus.REASONING,
    VerdictType.REQUIRES_CONFIRMATION: MissionStatus.AWAITING_CONFIRMATION,
}

# Table de transitions : toutes les paires (etat, evenement) qui n'exigent pas de
# sous-routage sur le contenu de l'evenement. PolicyEvaluated (sous-route sur
# verdict.type) et AbortRequested (valide depuis tout etat non terminal) sont
# geres a part dans transition() plutot que d'etre repetes ligne par ligne ici.
_TRANSITIONS: dict[tuple[MissionStatus, str], MissionStatus] = {
    (MissionStatus.CREATED, "mission_created"): MissionStatus.REASONING,
    (MissionStatus.REASONING, "mission_succeeded"): MissionStatus.SUCCEEDED,
    (MissionStatus.REASONING, "mission_failed"): MissionStatus.FAILED,
    (MissionStatus.REASONING, "max_iterations_reached"): MissionStatus.MAX_ITERATIONS,
    (MissionStatus.AWAITING_CONFIRMATION, "confirmation_granted"): MissionStatus.EXECUTING,
    (MissionStatus.AWAITING_CONFIRMATION, "confirmation_denied"): MissionStatus.REASONING,
    (MissionStatus.EXECUTING, "tool_execution_finished"): MissionStatus.REASONING,
}


def transition(state: MissionStatus, event: MissionEvent) -> MissionStatus:
    if isinstance(event, AbortRequested):
        if state in _TERMINAL_STATES:
            raise InvalidTransitionError(state, event)
        return MissionStatus.ABORTED

    if isinstance(event, PolicyEvaluated):
        if state is not MissionStatus.REASONING:
            raise InvalidTransitionError(state, event)
        return _VERDICT_TRANSITIONS[event.verdict.type]

    next_state = _TRANSITIONS.get((state, event.kind))
    if next_state is None:
        raise InvalidTransitionError(state, event)
    return next_state

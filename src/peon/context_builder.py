from pydantic import ValidationError

from peon.event_log import EventLog
from peon.models.context import Context
from peon.models.events import Event, EventType
from peon.models.mission import MissionStatus
from peon.models.observation import Observation
from peon.tool_registry import ToolRegistry


class MalformedObservationEventError(Exception):
    pass


class ContextBuilder:
    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def build(
        self,
        *,
        mission_goal: str,
        mission_status: MissionStatus,
        mission_iteration_count: int,
        observations: list[Observation],
    ) -> Context:
        return Context(
            mission_goal=mission_goal,
            mission_status=mission_status,
            mission_iteration_count=mission_iteration_count,
            observations=observations,
            available_tools=self._registry.list_tools(),
        )

    def build_from_event_log(
        self,
        *,
        mission_goal: str,
        mission_status: MissionStatus,
        mission_iteration_count: int,
        event_log: EventLog,
    ) -> Context:
        observations = [
            self._to_observation(event) for event in event_log.list_events_by_type(EventType.OBSERVATION_PRODUCED)
        ]
        return self.build(
            mission_goal=mission_goal,
            mission_status=mission_status,
            mission_iteration_count=mission_iteration_count,
            observations=observations,
        )

    @staticmethod
    def _to_observation(event: Event) -> Observation:
        # Le Runtime est le seul producteur d'evenements OBSERVATION_PRODUCED :
        # un payload invalide signale une violation de contrat interne (bug),
        # pas une entree externe a recuperer silencieusement.
        try:
            return Observation(**event.payload)
        except ValidationError as exc:
            raise MalformedObservationEventError(
                f"event '{event.id}' payload is not a valid Observation: {exc}"
            ) from exc

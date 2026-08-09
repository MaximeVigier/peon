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
            self._to_observation(event)
            for event in self._current_mission_events(event_log)
            if event.type is EventType.OBSERVATION_PRODUCED
        ]
        return self.build(
            mission_goal=mission_goal,
            mission_status=mission_status,
            mission_iteration_count=mission_iteration_count,
            observations=observations,
        )

    @staticmethod
    def _current_mission_events(event_log: EventLog) -> list[Event]:
        # Storage.save_events() est append-only pour toujours, sans notion de
        # Mission (voir storage.py) : une EventLog rechargee depuis un Storage
        # partage entre plusieurs invocations `peon run` successives peut donc
        # porter l'historique de plusieurs Missions bout a bout, alors que
        # Checkpoint ne retient jamais que la derniere (mono-mission). Seuls
        # les evenements survenus depuis le plus recent MISSION_CREATED
        # appartiennent a la Mission en cours de reprise ; sans ce filtrage,
        # les Observation d'une Mission anterieure et sans rapport fuiteraient
        # dans le Context reconstruit (voir tests/test_resume_history.py,
        # cas 5). Aucun MISSION_CREATED trouve (EventLog construite a la main
        # par un test, ou historique legacy) : comportement inchange, tous les
        # evenements sont consideres comme appartenant a la Mission courante.
        all_events = event_log.list_events()
        start = 0
        for index, event in enumerate(all_events):
            if event.type is EventType.MISSION_CREATED:
                start = index
        return all_events[start:]

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

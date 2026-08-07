from peon.models.events import Event, EventType


class EventLog:
    def __init__(self) -> None:
        self._events: list[Event] = []

    def append(self, event: Event) -> None:
        self._events.append(event)

    def list_events(self) -> list[Event]:
        return list(self._events)

    def list_events_by_type(self, event_type: EventType) -> list[Event]:
        return [event for event in self._events if event.type is event_type]

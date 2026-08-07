from abc import ABC, abstractmethod

from peon.models.events import Event


class Storage(ABC):
    @abstractmethod
    def save_events(self, events: list[Event]) -> None: ...

    @abstractmethod
    def load_events(self) -> list[Event]: ...


class InMemoryStorage(Storage):
    def __init__(self) -> None:
        self._events: list[Event] = []

    def save_events(self, events: list[Event]) -> None:
        self._events.extend(events)

    def load_events(self) -> list[Event]:
        return list(self._events)

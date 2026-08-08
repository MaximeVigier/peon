from abc import ABC, abstractmethod

from peon.models.checkpoint import Checkpoint
from peon.models.events import Event


class Storage(ABC):
    @abstractmethod
    def save_events(self, events: list[Event]) -> None: ...

    @abstractmethod
    def load_events(self) -> list[Event]: ...

    @abstractmethod
    def save_checkpoint(self, checkpoint: Checkpoint) -> None: ...

    @abstractmethod
    def load_checkpoint(self) -> Checkpoint | None: ...


class InMemoryStorage(Storage):
    def __init__(self) -> None:
        self._events: list[Event] = []
        self._checkpoint: Checkpoint | None = None

    def save_events(self, events: list[Event]) -> None:
        self._events.extend(events)

    def load_events(self) -> list[Event]:
        return list(self._events)

    def save_checkpoint(self, checkpoint: Checkpoint) -> None:
        # Un seul checkpoint retenu a la fois (pas d'historique de checkpoints) :
        # coherent avec le perimetre mono-Mission de cette phase, chaque appel
        # remplace le precedent plutot que de l'accumuler (contrairement a
        # save_events, qui reste append-only).
        self._checkpoint = checkpoint.model_copy(deep=True)

    def load_checkpoint(self) -> Checkpoint | None:
        # Copie profonde retournee, jamais la reference interne : meme garantie
        # que load_events(), necessaire ici car Checkpoint embarque une Mission
        # mutable (contrairement a Event, immuable).
        if self._checkpoint is None:
            return None
        return self._checkpoint.model_copy(deep=True)

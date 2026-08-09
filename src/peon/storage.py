import os
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path

from pydantic import ValidationError

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


class CorruptedCheckpointError(Exception):
    pass


class CorruptedEventLogError(Exception):
    pass


class FileStorage(Storage):
    # Persiste le Checkpoint (JSON) et l'EventLog (JSON Lines, un evenement
    # par ligne) sur disque, chacun dans son propre fichier : le fichier
    # d'evenements est nomme d'apres le chemin du Checkpoint
    # (`<checkpoint>.events.jsonl`), donc un seul chemin suffit toujours a
    # construire un FileStorage complet -- pas de parametre supplementaire.
    # Un seul Checkpoint retenu a la fois (remplace a chaque
    # save_checkpoint(), comme InMemoryStorage) ; les evenements restent
    # append-only comme le contrat Storage l'exige (voir InMemoryStorage).
    def __init__(self, checkpoint_path: Path) -> None:
        self._checkpoint_path = checkpoint_path
        self._events_path = checkpoint_path.with_name(checkpoint_path.name + ".events.jsonl")

    def save_events(self, events: list[Event]) -> None:
        if not events:
            return
        # Reecriture atomique du fichier complet (evenements deja presents +
        # nouveaux), meme pattern que save_checkpoint() (tempfile + os.replace)
        # plutot qu'un append() brut : un crash pendant l'ecriture laisse le
        # fichier precedent intact plutot qu'une derniere ligne tronquee/corrompue.
        combined = self.load_events() + list(events)
        self._events_path.parent.mkdir(parents=True, exist_ok=True)
        payload = "".join(event.model_dump_json() + "\n" for event in combined)
        fd, tmp_name = tempfile.mkstemp(
            dir=self._events_path.parent, prefix=".events-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
                tmp_file.write(payload)
            os.replace(tmp_name, self._events_path)
        except BaseException:
            os.unlink(tmp_name)
            raise

    def load_events(self) -> list[Event]:
        if not self._events_path.exists():
            return []
        raw = self._events_path.read_text(encoding="utf-8")
        events: list[Event] = []
        for line_number, line in enumerate(raw.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                events.append(Event.model_validate_json(line))
            except ValidationError as exc:
                raise CorruptedEventLogError(
                    f"le fichier d'evenements '{self._events_path}' est invalide "
                    f"a la ligne {line_number} : {exc}"
                ) from exc
        return events

    def save_checkpoint(self, checkpoint: Checkpoint) -> None:
        self._checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        # Ecriture atomique (tempfile + os.replace) : un crash pendant
        # l'ecriture laisse l'ancien checkpoint intact plutot qu'un fichier
        # tronque/corrompu.
        fd, tmp_name = tempfile.mkstemp(
            dir=self._checkpoint_path.parent, prefix=".checkpoint-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
                tmp_file.write(checkpoint.model_dump_json())
            os.replace(tmp_name, self._checkpoint_path)
        except BaseException:
            os.unlink(tmp_name)
            raise

    def load_checkpoint(self) -> Checkpoint | None:
        if not self._checkpoint_path.exists():
            return None
        raw = self._checkpoint_path.read_text(encoding="utf-8")
        try:
            return Checkpoint.model_validate_json(raw)
        except ValidationError as exc:
            raise CorruptedCheckpointError(
                f"le fichier checkpoint '{self._checkpoint_path}' est invalide : {exc}"
            ) from exc

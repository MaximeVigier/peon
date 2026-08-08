"""Port d'observabilite technique (durees, spans), separe du vocabulaire
metier de l'Event Log.

`Runtime -> Tracer`, jamais `Runtime -> EventLog <- Tracer` : le `Tracer` est
un collaborateur optionnel injecte au constructeur du `Runtime`. Il n'ecrit
rien dans l'`EventLog` et l'`EventLog` ne sait pas qu'il existe -- aucun
`EventType` ajoute, aucun evenement de tracing journalise. `tracer=None` (le
defaut) reproduit exactement le comportement observable pre-Phase 4 via
`NoOpTracer`.

Abstraction volontairement minimale (spans nommes, attributs techniques,
duree, exception) : ni systeme complet de metrics/logging distribue, ni
dependance a OpenTelemetry a ce stade. Le contrat (`start_span` en context
manager, `Span.set_attribute`/`record_exception`) est pense pour qu'un futur
adaptateur OpenTelemetry puisse s'y brancher sans reecriture.
"""

from abc import ABC, abstractmethod
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any


class Span(ABC):
    @abstractmethod
    def set_attribute(self, key: str, value: Any) -> None: ...

    @abstractmethod
    def record_exception(self, exc: BaseException) -> None: ...


class Tracer(ABC):
    @contextmanager
    def start_span(self, name: str, **attributes: Any) -> Iterator[Span]:
        # Try/except/finally centralise ici, pas dans chaque implementation :
        # garantit qu'un span se ferme toujours -- meme si l'operation tracee
        # leve -- sans qu'un adaptateur concret puisse se tromper sur ce
        # point (voir tests/test_tracing.py, "exception ne bloque pas la
        # fermeture du span").
        span = self._start_span(name)
        for key, value in attributes.items():
            span.set_attribute(key, value)
        try:
            yield span
        except BaseException as exc:
            span.record_exception(exc)
            raise
        finally:
            self._end_span(span)

    @abstractmethod
    def _start_span(self, name: str) -> Span: ...

    @abstractmethod
    def _end_span(self, span: Span) -> None: ...


class NoOpSpan(Span):
    def set_attribute(self, key: str, value: Any) -> None:
        pass

    def record_exception(self, exc: BaseException) -> None:
        pass


_NOOP_SPAN = NoOpSpan()


class NoOpTracer(Tracer):
    def _start_span(self, name: str) -> Span:
        return _NOOP_SPAN

    def _end_span(self, span: Span) -> None:
        pass

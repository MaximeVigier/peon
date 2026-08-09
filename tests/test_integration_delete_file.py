"""Cycle complet bout-en-bout pour `delete_file` (`DeleteFileTool`, `RiskLevel.HIGH`).

Contrairement a `tests/test_integration_read_file.py` (Tool `LOW`, execute
directement), ce module exerce le seul chemin du pipeline qu'aucun Tool `LOW`/
`MEDIUM` existant ne pouvait declencher en usage reel : `REQUIRES_CONFIRMATION`
-> `AWAITING_CONFIRMATION` -> `resume_confirmation()`. Aucun mock interne : un
vrai `DeleteFileTool`/`LocalWorkspace` opere sur un vrai fichier disque
(`tmp_path`), ce qui permet de verifier l'invariant de securite central par un
fait observable (le fichier existe encore ou non), pas seulement par le statut
de la Mission.
"""

from pathlib import Path
from typing import Literal

import pytest

from peon.context_builder import ContextBuilder
from peon.event_log import EventLog
from peon.executor import Executor
from peon.models.checkpoint import Checkpoint
from peon.models.confirmation import ConfirmationResponse
from peon.models.context import Context
from peon.models.decision import ActionDecision, Decision, FinishDecision
from peon.models.events import Event
from peon.models.mission import MissionStatus
from peon.models.observation import ObservationKind
from peon.policy import PolicyEngine
from peon.reasoner import Reasoner
from peon.runtime import Runtime, UnknownConfirmationRequestError
from peon.storage import InMemoryStorage, Storage
from peon.tool_registry import ToolRegistry
from peon.tools.filesystem import DeleteFileTool
from peon.workspace import LocalWorkspace


class _SimulatedCrash(Exception):
    pass


class _CrashAfterCalls(Storage):
    # Meme convention que tests/test_confirmation_idempotence.py (chantier
    # « Idempotence durable d'une Action HIGH apres confirmation ») : simule
    # un arret brutal du process au N-ieme appel de `crash_method`, sans
    # importer le module de test dedie (chaque module reste autonome).
    def __init__(self, wrapped: Storage, *, crash_method: str, crash_on_call: int) -> None:
        self._wrapped = wrapped
        self._crash_method = crash_method
        self._crash_on_call = crash_on_call
        self._calls: dict[str, int] = {"save_checkpoint": 0, "save_events": 0}

    def _maybe_crash(self, method: str) -> None:
        self._calls[method] += 1
        if method == self._crash_method and self._calls[method] == self._crash_on_call:
            raise _SimulatedCrash(f"simulated crash on {method} call #{self._calls[method]}")

    def save_events(self, events: list[Event]) -> None:
        self._maybe_crash("save_events")
        self._wrapped.save_events(events)

    def load_events(self) -> list[Event]:
        return self._wrapped.load_events()

    def save_checkpoint(self, checkpoint: Checkpoint) -> None:
        self._maybe_crash("save_checkpoint")
        self._wrapped.save_checkpoint(checkpoint)

    def load_checkpoint(self) -> Checkpoint | None:
        return self._wrapped.load_checkpoint()


class _DeleteThenFinishReasoner(Reasoner):
    # Chemin et issue fixes a la construction, jamais devines a partir du
    # Context ou du filesystem : reproduit une reponse LLM deja connue, sans
    # comportement non deterministe (meme convention que
    # test_integration_read_file.py::_ReadThenFinishReasoner).
    def __init__(self, path: str, outcome: Literal["success", "failure"] = "success") -> None:
        self._path = path
        self._outcome = outcome
        self._call_count = 0

    def decide(self, context: Context) -> Decision:
        self._call_count += 1
        if self._call_count == 1:
            return ActionDecision(
                reasoning="supprimer le fichier demande par la mission",
                tool_name="delete_file",
                arguments={"path": self._path},
            )
        return FinishDecision(outcome=self._outcome, summary="mission terminee", confidence=1.0)


class _FinishReasoner(Reasoner):
    # Utilise cote Runtime B (checkpoint/reprise) : l'Action delete_file
    # confirmee provient deja du Checkpoint, pas d'une nouvelle Decision - ce
    # Reasoner n'a donc plus qu'a signaler la fin de mission au premier appel.
    def __init__(self, outcome: Literal["success", "failure"] = "success") -> None:
        self._outcome = outcome

    def decide(self, context: Context) -> Decision:
        return FinishDecision(outcome=self._outcome, summary="mission reprise terminee", confidence=1.0)


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(DeleteFileTool(LocalWorkspace()))
    return registry


def _runtime(registry: ToolRegistry, reasoner: Reasoner, *, storage: InMemoryStorage | None = None) -> Runtime:
    return Runtime(
        context_builder=ContextBuilder(registry),
        reasoner=reasoner,
        policy_engine=PolicyEngine(registry),
        executor=Executor(registry),
        event_log=EventLog(),
        storage=storage,
    )


def test_high_risk_delete_requires_confirmation_and_never_executes_before_it(tmp_path: Path) -> None:
    target = tmp_path / "mission.txt"
    target.write_text("contenu reel du fichier", encoding="utf-8")

    runtime = _runtime(_registry(), _DeleteThenFinishReasoner(str(target)))

    mission = runtime.run(f"supprimer {target.name}")

    assert mission.status is MissionStatus.AWAITING_CONFIRMATION
    assert runtime.pending_confirmation is not None
    assert runtime.pending_confirmation.action.tool_name == "delete_file"
    # Invariant de securite central : le fichier existe toujours, l'Action
    # n'a pas ete executee avant la confirmation.
    assert target.exists()


def test_granted_confirmation_actually_deletes_the_file(tmp_path: Path) -> None:
    target = tmp_path / "mission.txt"
    target.write_text("contenu reel du fichier", encoding="utf-8")

    runtime = _runtime(_registry(), _DeleteThenFinishReasoner(str(target)))
    mission = runtime.run(f"supprimer {target.name}")
    request_id = runtime.pending_confirmation.id

    resumed = runtime.resume_confirmation(mission, ConfirmationResponse(request_id=request_id, granted=True))

    assert resumed.status is MissionStatus.SUCCEEDED
    assert not target.exists()
    assert runtime.observations[0].kind is ObservationKind.EXECUTION_RESULT
    assert runtime.observations[0].details["tool_name"] == "delete_file"
    assert runtime.observations[0].details["output"] == {"path": str(target), "deleted": True}


def test_denied_confirmation_leaves_the_file_untouched(tmp_path: Path) -> None:
    target = tmp_path / "mission.txt"
    target.write_text("contenu reel du fichier", encoding="utf-8")

    runtime = _runtime(_registry(), _DeleteThenFinishReasoner(str(target), outcome="failure"))
    mission = runtime.run(f"supprimer {target.name}")
    request_id = runtime.pending_confirmation.id

    resumed = runtime.resume_confirmation(mission, ConfirmationResponse(request_id=request_id, granted=False))

    assert resumed.status is MissionStatus.FAILED
    assert target.exists()
    assert runtime.observations[0].kind is ObservationKind.CONFIRMATION_DENIED


def test_delete_file_confirmation_survives_a_checkpoint_and_resume_on_a_new_runtime(tmp_path: Path) -> None:
    # Meme scenario que test_checkpoint.py (crash simule, deux instances
    # Runtime distinctes), mais avec le vrai DeleteFileTool plutot qu'un
    # _StubTool : la reprise doit aboutir a une suppression reelle sur disque,
    # pas seulement a un statut de Mission correct.
    target = tmp_path / "mission.txt"
    target.write_text("contenu reel du fichier", encoding="utf-8")
    storage = InMemoryStorage()

    runtime_a = _runtime(_registry(), _DeleteThenFinishReasoner(str(target)), storage=storage)
    mission = runtime_a.run(f"supprimer {target.name}")
    assert mission.status is MissionStatus.AWAITING_CONFIRMATION
    assert target.exists()

    runtime_a.save_checkpoint(mission)
    del runtime_a  # simule l'arret du process avant que l'utilisateur ne reponde

    checkpoint = storage.load_checkpoint()
    assert checkpoint is not None
    assert checkpoint.pending_confirmation.action.tool_name == "delete_file"

    runtime_b = _runtime(_registry(), _FinishReasoner(), storage=storage)
    resumed_mission = runtime_b.resume_mission(checkpoint)
    assert resumed_mission.status is MissionStatus.AWAITING_CONFIRMATION
    assert runtime_b.pending_confirmation is not None

    result = runtime_b.resume_confirmation(
        resumed_mission, ConfirmationResponse(request_id=runtime_b.pending_confirmation.id, granted=True)
    )

    assert result.status is MissionStatus.SUCCEEDED
    assert not target.exists()


def test_delete_file_is_never_executed_twice_when_the_process_crashes_right_after_deleting(tmp_path: Path) -> None:
    # Le scenario concret cite par le chantier « Idempotence durable d'une
    # Action HIGH apres confirmation » : delete_file execute une premiere
    # fois, puis un crash simule juste apres (avant la persistance de cette
    # execution) - une reprise ulterieure ne doit jamais retrouver la
    # ConfirmationRequest d'origine comme encore "en attente" et donc ne
    # jamais retenter la suppression.
    target = tmp_path / "mission.txt"
    target.write_text("contenu reel du fichier", encoding="utf-8")
    storage = _CrashAfterCalls(InMemoryStorage(), crash_method="save_checkpoint", crash_on_call=2)

    runtime = _runtime(_registry(), _DeleteThenFinishReasoner(str(target)), storage=storage)
    mission = runtime.run(f"supprimer {target.name}")
    request_id = runtime.pending_confirmation.id

    with pytest.raises(_SimulatedCrash):
        runtime.resume_confirmation(mission, ConfirmationResponse(request_id=request_id, granted=True))

    # Le fichier a bien ete reellement supprime avant que la persistance de
    # cette suppression ne "crashe".
    assert not target.exists()

    underlying_storage = storage._wrapped
    persisted = underlying_storage.load_checkpoint()
    assert persisted.pending_confirmation is None
    assert persisted.mission.status is MissionStatus.EXECUTING

    # "Nouveau process" : nouveau Runtime avec l'EventLog reconstruit depuis
    # le Storage non-crashant, comme test_delete_file_confirmation_survives_a_checkpoint_and_resume_on_a_new_runtime
    # ci-dessus mais avec l'historique persiste en plus.
    reloaded_event_log = Runtime.load_event_log(underlying_storage)
    registry = _registry()
    runtime_b = Runtime(
        context_builder=ContextBuilder(registry),
        reasoner=_FinishReasoner(),
        policy_engine=PolicyEngine(registry),
        executor=Executor(registry),
        event_log=reloaded_event_log,
        storage=underlying_storage,
    )
    resumed_mission = runtime_b.resume_mission(persisted)

    assert runtime_b.pending_confirmation is None
    with pytest.raises(UnknownConfirmationRequestError):
        runtime_b.resume_confirmation(resumed_mission, ConfirmationResponse(request_id=request_id, granted=True))

    # Toujours supprime une seule fois : aucune reprise n'a retente l'Action.
    assert not target.exists()

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

from peon.context_builder import ContextBuilder
from peon.event_log import EventLog
from peon.executor import Executor
from peon.models.confirmation import ConfirmationResponse
from peon.models.context import Context
from peon.models.decision import ActionDecision, Decision, FinishDecision
from peon.models.mission import MissionStatus
from peon.models.observation import ObservationKind
from peon.policy import PolicyEngine
from peon.reasoner import Reasoner
from peon.runtime import Runtime
from peon.storage import InMemoryStorage
from peon.tool_registry import ToolRegistry
from peon.tools.filesystem import DeleteFileTool
from peon.workspace import LocalWorkspace


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

"""Idempotence durable d'une Action HIGH apres confirmation (chantier
« Idempotence durable d'une Action HIGH apres confirmation »).

Risque cible : entre le moment ou `resume_confirmation(granted=True)`
consomme la ConfirmationRequest et execute reellement l'Action, et le moment
ou ce fait devient durable (`save_checkpoint`/`persist_events`), une fenetre
de crash existait ou un `peon resume` ulterieur pouvait retrouver le meme
Checkpoint `AWAITING_CONFIRMATION` et re-executer l'Action HIGH une deuxieme
fois. `Runtime.resume_confirmation()` rend desormais durable, via un point de
persistance interne (`_checkpoint_if_configured`, reutilise les memes
`save_checkpoint()`/`persist_events()` deja publics) juste avant *et* juste
apres `_execute_action()` : `pending_confirmation` est deja `None` sur disque
avant que l'effet de bord reel ne se produise, donc plus aucun chemin de
reprise ne peut jamais reconstruire une ConfirmationRequest encore "en
attente" pour cette Action.

`_CrashAfterCalls` (Storage) simule un arret brutal du process a un point
precis : les appels avant le N-ieme atterrissent normalement sur le Storage
enveloppe, le N-ieme leve avant d'y toucher - meme effet observable qu'un
crash reel a cet instant (le fichier concerne n'a jamais ete modifie), sans
avoir a tuer un vrai process pour le prouver dans chaque scenario.
"""

import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

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
from peon.models.tool_result import ToolResult
from peon.models.tool_spec import RiskLevel, ToolSpec
from peon.policy import PolicyEngine
from peon.reasoner import Reasoner
from peon.runtime import Runtime, UnknownConfirmationRequestError
from peon.storage import FileStorage, InMemoryStorage, Storage
from peon.tool_registry import ToolRegistry
from peon.tools.base import Tool


class _SimulatedCrash(Exception):
    pass


class _CrashAfterCalls(Storage):
    def __init__(self, wrapped: Storage, *, crash_method: str, crash_on_call: int) -> None:
        self._wrapped = wrapped
        self._crash_method = crash_method
        self._crash_on_call = crash_on_call
        self._calls: dict[str, int] = {"save_checkpoint": 0, "save_events": 0}

    def call_count(self, method: str) -> int:
        return self._calls[method]

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


class _CountingTool(Tool):
    def __init__(self, name: str, risk_level: RiskLevel, result: ToolResult | None = None) -> None:
        self._spec = ToolSpec(
            name=name,
            description=f"Outil {name}",
            parameters_schema={"type": "object", "properties": {}},
            risk_level=risk_level,
        )
        self._result = result if result is not None else ToolResult(success=True, output="ok")
        self.call_count = 0

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        self.call_count += 1
        return self._result


class _ScriptedReasoner(Reasoner):
    def __init__(self, decisions: list[Decision]) -> None:
        self._decisions = list(decisions)

    def decide(self, context: Context) -> Decision:
        return self._decisions.pop(0)


def _registry(*tools: Tool) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    return registry


def _runtime(
    registry: ToolRegistry, reasoner: Reasoner, *, storage: Storage | None = None, event_log: EventLog | None = None
) -> tuple[Runtime, EventLog]:
    event_log = event_log if event_log is not None else EventLog()
    runtime = Runtime(
        context_builder=ContextBuilder(registry),
        reasoner=reasoner,
        policy_engine=PolicyEngine(registry),
        executor=Executor(registry),
        event_log=event_log,
        storage=storage,
    )
    return runtime, event_log


# --- 1. Crash juste apres l'execution, avant sa propre persistance -----------


def test_crash_immediately_after_execution_but_before_its_persistence_never_reexecutes() -> None:
    tool = _CountingTool("deploy", RiskLevel.HIGH)
    underlying = InMemoryStorage()
    storage = _CrashAfterCalls(underlying, crash_method="save_checkpoint", crash_on_call=2)
    registry = _registry(tool)
    reasoner = _ScriptedReasoner([ActionDecision(reasoning="deployer", tool_name="deploy", arguments={})])
    runtime, _ = _runtime(registry, reasoner, storage=storage)

    mission = runtime.run("deployer en production")
    assert mission.status is MissionStatus.AWAITING_CONFIRMATION
    request_id = runtime.pending_confirmation.id

    with pytest.raises(_SimulatedCrash):
        runtime.resume_confirmation(mission, ConfirmationResponse(request_id=request_id, granted=True))

    # L'Action a bien ete executee une fois avant que sa persistance ne
    # "crashe" juste apres - c'est precisement la fenetre de risque ciblee.
    assert tool.call_count == 1

    # Etat durable au moment du crash : le premier save_checkpoint() (juste
    # avant l'execution) est passe, le second (juste apres) a leve avant
    # d'atteindre le Storage enveloppe.
    persisted = underlying.load_checkpoint()
    assert persisted is not None
    assert persisted.pending_confirmation is None
    assert persisted.mission.status is MissionStatus.EXECUTING

    # "Nouveau process" : nouveau Runtime, Storage non-crashant sur les memes
    # donnees deja atterries sur disque.
    reloaded_event_log = Runtime.load_event_log(underlying)
    assert any(event.type.value == "confirmation_granted" for event in reloaded_event_log.list_events())
    runtime_b, _ = _runtime(registry, _ScriptedReasoner([]), storage=underlying, event_log=reloaded_event_log)
    resumed_mission = runtime_b.resume_mission(persisted)

    # Plus aucune confirmation en attente : resume_confirmation() ne peut
    # structurellement plus etre rappele une deuxieme fois pour cette Action.
    assert runtime_b.pending_confirmation is None
    with pytest.raises(UnknownConfirmationRequestError):
        runtime_b.resume_confirmation(resumed_mission, ConfirmationResponse(request_id=request_id, granted=True))

    # Le Tool n'a jamais ete rappele.
    assert tool.call_count == 1
    # Historique coherent : l'execution interrompue n'a jamais produit
    # d'Observation durable (le crash a eu lieu avant persist_events()) -
    # aucune Observation fantome/partielle n'apparait apres reprise.
    assert runtime_b.observations == []


def test_crash_immediately_after_execution_through_two_distinct_file_storage_instances(tmp_path: Path) -> None:
    # Meme scenario que le test precedent, mais avec un vrai FileStorage sur
    # disque et une deuxieme instance totalement neuve pour la reprise (aucun
    # etat Python partage), comme les autres tests de crash simule de ce
    # projet (voir test_resume_history.py::test_cas3_*).
    checkpoint_path = tmp_path / "checkpoint.json"
    tool = _CountingTool("deploy", RiskLevel.HIGH)
    registry = _registry(tool)
    storage_a = _CrashAfterCalls(FileStorage(checkpoint_path), crash_method="save_checkpoint", crash_on_call=2)
    reasoner = _ScriptedReasoner([ActionDecision(reasoning="deployer", tool_name="deploy", arguments={})])
    runtime_a, _ = _runtime(registry, reasoner, storage=storage_a)

    mission = runtime_a.run("deployer en production")
    request_id = runtime_a.pending_confirmation.id

    with pytest.raises(_SimulatedCrash):
        runtime_a.resume_confirmation(mission, ConfirmationResponse(request_id=request_id, granted=True))
    assert tool.call_count == 1
    del runtime_a, storage_a

    storage_b = FileStorage(checkpoint_path)
    persisted = storage_b.load_checkpoint()
    assert persisted is not None
    assert persisted.pending_confirmation is None
    assert persisted.mission.status is MissionStatus.EXECUTING

    reloaded_event_log = Runtime.load_event_log(storage_b)
    runtime_b, _ = _runtime(registry, _ScriptedReasoner([]), storage=storage_b, event_log=reloaded_event_log)
    resumed_mission = runtime_b.resume_mission(persisted)

    assert runtime_b.pending_confirmation is None
    with pytest.raises(UnknownConfirmationRequestError):
        runtime_b.resume_confirmation(resumed_mission, ConfirmationResponse(request_id=request_id, granted=True))
    assert tool.call_count == 1


# --- 2. Action HIGH reussie avant crash : reconnue durablement ---------------


def test_a_successful_execution_persisted_before_the_crash_is_durably_recognized_and_not_replayed() -> None:
    tool = _CountingTool("deploy", RiskLevel.HIGH, ToolResult(success=True, output="deploiement ok"))
    storage = InMemoryStorage()
    registry = _registry(tool)
    reasoner = _ScriptedReasoner(
        [
            ActionDecision(reasoning="deployer", tool_name="deploy", arguments={}),
            FinishDecision(outcome="success", summary="fait", confidence=1.0),
        ]
    )
    runtime, _ = _runtime(registry, reasoner, storage=storage)

    mission = runtime.run("deployer en production")
    request_id = runtime.pending_confirmation.id

    # Aucun crash simule ici : les deux points de persistance internes a
    # resume_confirmation() (avant/apres _execute_action) atterrissent tous
    # les deux normalement. La suite de la boucle de raisonnement (qui menera
    # a SUCCEEDED) se deroule ensuite en memoire, sans autre persistance -
    # "simuler l'arret du process" revient donc simplement a n'en tenir aucun
    # compte et a repartir uniquement de ce qui est sur disque a cet instant.
    runtime.resume_confirmation(mission, ConfirmationResponse(request_id=request_id, granted=True))
    assert tool.call_count == 1

    persisted = storage.load_checkpoint()
    assert persisted.pending_confirmation is None

    reloaded_event_log = Runtime.load_event_log(storage)
    runtime_b, _ = _runtime(registry, _ScriptedReasoner([]), storage=storage, event_log=reloaded_event_log)
    resumed_mission = runtime_b.resume_mission(persisted)

    # L'issue reussie est bien reconnue durablement : l'Observation de
    # l'execution est presente sans qu'aucune deuxieme execution n'ait eu
    # lieu pour la produire.
    assert any(
        obs.kind is ObservationKind.EXECUTION_RESULT and obs.details.get("tool_name") == "deploy"
        for obs in runtime_b.observations
    )
    assert runtime_b.pending_confirmation is None
    with pytest.raises(UnknownConfirmationRequestError):
        runtime_b.resume_confirmation(resumed_mission, ConfirmationResponse(request_id=request_id, granted=True))
    assert tool.call_count == 1


# --- 3. Action HIGH echouee avant crash : pas de rejeu aveugle ---------------


def test_a_failed_execution_persisted_before_the_crash_is_not_blindly_replayed() -> None:
    tool = _CountingTool("deploy", RiskLevel.HIGH, ToolResult(success=False, error="deploiement refuse par le serveur"))
    storage = InMemoryStorage()
    registry = _registry(tool)
    reasoner = _ScriptedReasoner(
        [
            ActionDecision(reasoning="deployer", tool_name="deploy", arguments={}),
            FinishDecision(outcome="failure", summary="deploiement en echec", confidence=1.0),
        ]
    )
    runtime, _ = _runtime(registry, reasoner, storage=storage)

    mission = runtime.run("deployer en production")
    request_id = runtime.pending_confirmation.id

    runtime.resume_confirmation(mission, ConfirmationResponse(request_id=request_id, granted=True))
    assert tool.call_count == 1

    persisted = storage.load_checkpoint()
    assert persisted.pending_confirmation is None

    reloaded_event_log = Runtime.load_event_log(storage)
    runtime_b, _ = _runtime(registry, _ScriptedReasoner([]), storage=storage, event_log=reloaded_event_log)
    resumed_mission = runtime_b.resume_mission(persisted)

    assert any(obs.kind is ObservationKind.EXECUTION_ERROR for obs in runtime_b.observations)
    assert runtime_b.pending_confirmation is None
    with pytest.raises(UnknownConfirmationRequestError):
        runtime_b.resume_confirmation(resumed_mission, ConfirmationResponse(request_id=request_id, granted=True))
    # L'echec n'est jamais rejoue "aveuglement" : aucune deuxieme execution.
    assert tool.call_count == 1


# --- 4. Reprise normale apres confirmation, sans crash : zero regression ----


def test_normal_confirmation_flow_with_storage_configured_is_unaffected() -> None:
    tool = _CountingTool("deploy", RiskLevel.HIGH)
    storage = InMemoryStorage()
    registry = _registry(tool)
    reasoner = _ScriptedReasoner(
        [
            ActionDecision(reasoning="deployer", tool_name="deploy", arguments={}),
            FinishDecision(outcome="success", summary="fait", confidence=1.0),
        ]
    )
    runtime, _ = _runtime(registry, reasoner, storage=storage)

    mission = runtime.run("deployer en production")
    request_id = runtime.pending_confirmation.id
    result = runtime.resume_confirmation(mission, ConfirmationResponse(request_id=request_id, granted=True))

    assert result.status is MissionStatus.SUCCEEDED
    assert tool.call_count == 1
    assert runtime.pending_confirmation is None


# --- 5. Compatibilite Checkpoint : aucun nouveau champ -----------------------


def test_checkpoint_schema_unchanged_and_old_style_checkpoints_still_resume_normally() -> None:
    # Cette solution ne modifie ni Checkpoint ni Storage : un Checkpoint
    # construit "a l'ancienne" (avant ce chantier) doit continuer a resumer
    # normalement, sans migration.
    assert set(Checkpoint.model_fields.keys()) == {"mission", "pending_confirmation", "created_at"}

    tool = _CountingTool("deploy", RiskLevel.HIGH)
    storage = InMemoryStorage()
    registry = _registry(tool)
    reasoner = _ScriptedReasoner([ActionDecision(reasoning="deployer", tool_name="deploy", arguments={})])
    runtime_a, _ = _runtime(registry, reasoner, storage=storage)
    mission = runtime_a.run("deployer en production")
    runtime_a.save_checkpoint(mission)
    request_id = runtime_a.pending_confirmation.id
    del runtime_a

    checkpoint = storage.load_checkpoint()
    runtime_b, _ = _runtime(registry, _ScriptedReasoner([FinishDecision(outcome="success", summary="fait", confidence=1.0)]), storage=storage)
    resumed_mission = runtime_b.resume_mission(checkpoint)

    result = runtime_b.resume_confirmation(
        resumed_mission, ConfirmationResponse(request_id=request_id, granted=True)
    )
    assert result.status is MissionStatus.SUCCEEDED
    assert tool.call_count == 1


# --- 6. LOW/MEDIUM inchanges : aucune persistance interne declenchee --------


def test_low_and_medium_actions_never_trigger_the_new_internal_persistence() -> None:
    tool = _CountingTool("read_file", RiskLevel.LOW)
    underlying = InMemoryStorage()
    # Configure pour crasher des le tout premier save_checkpoint() : si le
    # nouveau point de persistance interne etait (a tort) atteint pour une
    # Action LOW/MEDIUM, ce test leverait _SimulatedCrash.
    storage = _CrashAfterCalls(underlying, crash_method="save_checkpoint", crash_on_call=1)
    registry = _registry(tool)
    reasoner = _ScriptedReasoner(
        [
            ActionDecision(reasoning="lire", tool_name="read_file", arguments={}),
            FinishDecision(outcome="success", summary="fini", confidence=1.0),
        ]
    )
    runtime, _ = _runtime(registry, reasoner, storage=storage)

    mission = runtime.run("lire un fichier")

    assert mission.status is MissionStatus.SUCCEEDED
    assert tool.call_count == 1
    # _checkpoint_if_configured() n'est cable que dans resume_confirmation(),
    # jamais dans _execute_action()/_handle_action_decision() : un chemin
    # ALLOWED (LOW/MEDIUM) ne le declenche donc jamais, meme avec un Storage
    # configure.
    assert storage.call_count("save_checkpoint") == 0


# --- 7. Une Action distincte proposee ulterieurement continue de fonctionner


def test_a_later_distinct_high_action_is_independently_protected_by_the_same_mechanism() -> None:
    deploy = _CountingTool("deploy", RiskLevel.HIGH)
    publish = _CountingTool("publish", RiskLevel.HIGH)
    underlying = InMemoryStorage()
    # Les deux premiers save_checkpoint() (avant/apres la 1ere confirmation)
    # doivent reussir ; le 4eme (apres l'execution de la 2eme confirmation)
    # doit crasher - reproduit la meme fenetre que le test 1, mais pour une
    # Action differente survenant plus tard dans la meme Mission.
    storage = _CrashAfterCalls(underlying, crash_method="save_checkpoint", crash_on_call=4)
    registry = _registry(deploy, publish)
    reasoner = _ScriptedReasoner(
        [
            ActionDecision(reasoning="deployer", tool_name="deploy", arguments={}),
            ActionDecision(reasoning="publier", tool_name="publish", arguments={}),
        ]
    )
    runtime, _ = _runtime(registry, reasoner, storage=storage)

    mission = runtime.run("deployer puis publier")
    first_request_id = runtime.pending_confirmation.id

    # Premiere confirmation : accordee normalement, aucun crash.
    mission = runtime.resume_confirmation(mission, ConfirmationResponse(request_id=first_request_id, granted=True))
    assert deploy.call_count == 1
    assert mission.status is MissionStatus.AWAITING_CONFIRMATION
    second_request_id = runtime.pending_confirmation.id
    assert second_request_id != first_request_id

    # Deuxieme confirmation : le crash simule frappe juste apres l'execution.
    with pytest.raises(_SimulatedCrash):
        runtime.resume_confirmation(mission, ConfirmationResponse(request_id=second_request_id, granted=True))
    assert publish.call_count == 1
    assert deploy.call_count == 1  # la premiere Action reste inchangee

    reloaded_event_log = Runtime.load_event_log(underlying)
    runtime_b, _ = _runtime(registry, _ScriptedReasoner([]), storage=underlying, event_log=reloaded_event_log)
    persisted = underlying.load_checkpoint()
    resumed_mission = runtime_b.resume_mission(persisted)

    assert runtime_b.pending_confirmation is None
    with pytest.raises(UnknownConfirmationRequestError):
        runtime_b.resume_confirmation(resumed_mission, ConfirmationResponse(request_id=second_request_id, granted=True))
    assert deploy.call_count == 1
    assert publish.call_count == 1


# --- 8. Deux vrais processus Python, avec le Tool delete_file de production -


def test_two_distinct_python_processes_never_delete_twice_across_a_simulated_crash(tmp_path: Path) -> None:
    checkpoint_path = tmp_path / "checkpoint.json"
    target = tmp_path / "mission.txt"
    target.write_text("contenu reel du fichier", encoding="utf-8")
    tests_dir = str(Path(__file__).parent)

    producer = textwrap.dedent(
        f"""
        import sys
        sys.path.insert(0, {tests_dir!r})
        from pathlib import Path
        from peon.context_builder import ContextBuilder
        from peon.event_log import EventLog
        from peon.executor import Executor
        from peon.models.confirmation import ConfirmationResponse
        from peon.models.decision import ActionDecision
        from peon.policy import PolicyEngine
        from peon.runtime import Runtime
        from peon.storage import FileStorage
        from peon.tool_registry import ToolRegistry
        from peon.tools.filesystem import DeleteFileTool
        from peon.workspace import LocalWorkspace
        from test_confirmation_idempotence import _CrashAfterCalls, _ScriptedReasoner, _SimulatedCrash

        registry = ToolRegistry()
        registry.register(DeleteFileTool(LocalWorkspace()))
        storage = _CrashAfterCalls(
            FileStorage(Path({str(checkpoint_path)!r})), crash_method="save_checkpoint", crash_on_call=2
        )
        runtime = Runtime(
            context_builder=ContextBuilder(registry),
            reasoner=_ScriptedReasoner([
                ActionDecision(reasoning="supprimer", tool_name="delete_file", arguments={{"path": {str(target)!r}}}),
            ]),
            policy_engine=PolicyEngine(registry),
            executor=Executor(registry),
            event_log=EventLog(),
            storage=storage,
        )
        mission = runtime.run("supprimer le fichier mission.txt")
        request_id = runtime.pending_confirmation.id
        try:
            runtime.resume_confirmation(mission, ConfirmationResponse(request_id=request_id, granted=True))
            raise SystemExit("le crash simule n'a pas eu lieu")
        except _SimulatedCrash:
            pass
        assert not Path({str(target)!r}).exists(), "le fichier doit avoir ete reellement supprime"
        print("PRODUCER_OK")
        print(f"REQUEST_ID={{request_id}}")
        """
    )

    producer_result = subprocess.run(
        [sys.executable, "-c", producer], capture_output=True, text=True, cwd=tests_dir, timeout=30
    )
    assert producer_result.returncode == 0, producer_result.stderr
    assert "PRODUCER_OK" in producer_result.stdout
    request_id_line = next(line for line in producer_result.stdout.splitlines() if line.startswith("REQUEST_ID="))
    request_id = request_id_line.split("=", 1)[1]

    consumer = textwrap.dedent(
        f"""
        import sys
        sys.path.insert(0, {tests_dir!r})
        from pathlib import Path
        from peon.context_builder import ContextBuilder
        from peon.event_log import EventLog
        from peon.executor import Executor
        from peon.models.confirmation import ConfirmationResponse
        from peon.models.mission import MissionStatus
        from peon.policy import PolicyEngine
        from peon.runtime import Runtime, UnknownConfirmationRequestError
        from peon.storage import FileStorage
        from peon.tool_registry import ToolRegistry
        from peon.tools.filesystem import DeleteFileTool
        from peon.workspace import LocalWorkspace
        from test_confirmation_idempotence import _ScriptedReasoner

        registry = ToolRegistry()
        registry.register(DeleteFileTool(LocalWorkspace()))
        storage = FileStorage(Path({str(checkpoint_path)!r}))
        checkpoint = storage.load_checkpoint()
        assert checkpoint is not None
        assert checkpoint.pending_confirmation is None
        assert checkpoint.mission.status is MissionStatus.EXECUTING
        event_log = Runtime.load_event_log(storage)
        runtime = Runtime(
            context_builder=ContextBuilder(registry),
            reasoner=_ScriptedReasoner([]),
            policy_engine=PolicyEngine(registry),
            executor=Executor(registry),
            event_log=event_log,
            storage=storage,
        )
        mission = runtime.resume_mission(checkpoint)
        assert runtime.pending_confirmation is None

        try:
            runtime.resume_confirmation(
                mission, ConfirmationResponse(request_id="{request_id}", granted=True)
            )
            raise SystemExit("resume_confirmation() n'aurait jamais du reussir ici")
        except UnknownConfirmationRequestError:
            pass

        assert not Path({str(target)!r}).exists(), "le fichier ne doit pas reapparaitre ni etre re-traite"
        print("CONSUMER_OK")
        """
    )

    consumer_result = subprocess.run(
        [sys.executable, "-c", consumer], capture_output=True, text=True, cwd=tests_dir, timeout=30
    )
    assert consumer_result.returncode == 0, consumer_result.stderr
    assert "CONSUMER_OK" in consumer_result.stdout

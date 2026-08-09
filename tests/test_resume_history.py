"""Reprise durable avec historique (chantier « reprise reellement durable »).

Cas couverts (voir CONTEXT.md/ARCHITECTURE.md pour la decision d'architecture
retenue -- Option B : Storage devient la source persistante des evenements,
`Runtime.load_event_log(storage)` + `ContextBuilder.build_from_event_log`
reconstruisent le Context, `Checkpoint` reste inchange) :

1. Reprise apres plusieurs Actions reussies -- l'historique (Observations)
   accumule par Runtime A avant l'arret doit produire, sur un Runtime B tout
   neuf, un Context strictement identique au point de reprise.
2. Reprise en attente de confirmation -- la ConfirmationRequest ET
   l'historique qui la precede survivent tous les deux.
3. Crash / nouveau process -- demontre via deux instances *distinctes* de
   FileStorage (memes fichiers sur disque, aucun etat Python partage), et
   optionnellement deux process Python separes.

Aucune nouvelle boucle de raisonnement ici : chaque scenario reutilise
uniquement l'API publique existante (`run`, `resume_mission`,
`resume_confirmation`, `save_checkpoint`, `persist_events`,
`Runtime.load_event_log`), meme convention que test_checkpoint.py
(`_runtime()` retourne `(Runtime, EventLog)`, jamais un attribut prive).
"""

import json
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

from peon.composition import build_runtime
from peon.context_builder import ContextBuilder
from peon.event_log import EventLog
from peon.executor import Executor
from peon.llm import LLM
from peon.models.confirmation import ConfirmationResponse
from peon.models.context import Context
from peon.models.decision import ActionDecision, Decision, FinishDecision
from peon.models.mission import MissionStatus
from peon.models.observation import ObservationKind
from peon.models.tool_result import ToolResult
from peon.models.tool_spec import RiskLevel, ToolSpec
from peon.policy import PolicyEngine
from peon.reasoner import Reasoner
from peon.runtime import Runtime
from peon.storage import CorruptedEventLogError, FileStorage, InMemoryStorage, Storage
from peon.tool_registry import ToolRegistry
from peon.tools.base import Tool


class _StubTool(Tool):
    def __init__(self, name: str, risk_level: RiskLevel, output: Any = "ok") -> None:
        self._spec = ToolSpec(
            name=name,
            description=f"Outil {name}",
            parameters_schema={"type": "object", "properties": {}},
            risk_level=risk_level,
        )
        self._output = output

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        return ToolResult(success=True, output=self._output)


class _ScriptedReasoner(Reasoner):
    # Decisions fixees a l'avance, jamais devinees depuis le Context : reproduit
    # une sequence de reponses LLM deja connue, sans comportement non deterministe
    # (meme convention que le reste du projet).
    def __init__(self, decisions: list[Decision]) -> None:
        self._decisions = list(decisions)

    def decide(self, context: Context) -> Decision:
        return self._decisions.pop(0)


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(_StubTool("read_config", RiskLevel.LOW, output="config a"))
    registry.register(_StubTool("read_secrets", RiskLevel.LOW, output="config b"))
    registry.register(_StubTool("deploy", RiskLevel.HIGH))
    return registry


def _runtime(registry: ToolRegistry, reasoner: Reasoner, *, storage: Storage, event_log: EventLog | None = None) -> tuple[Runtime, EventLog]:
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


def _two_reads_then_deploy() -> _ScriptedReasoner:
    return _ScriptedReasoner(
        [
            ActionDecision(reasoning="lire config a", tool_name="read_config", arguments={}),
            ActionDecision(reasoning="lire config b", tool_name="read_secrets", arguments={}),
            ActionDecision(reasoning="deployer", tool_name="deploy", arguments={}),
        ]
    )


def _context_from_log(registry: ToolRegistry, mission, event_log: EventLog) -> Context:
    return ContextBuilder(registry).build_from_event_log(
        mission_goal=mission.goal,
        mission_status=mission.status,
        mission_iteration_count=mission.iteration_count,
        event_log=event_log,
    )


# --- Cas 1 : reprise apres plusieurs Actions reussies -------------------------


def test_cas1_resume_after_several_successful_actions_rebuilds_an_identical_context() -> None:
    storage = InMemoryStorage()
    registry = _registry()

    runtime_a, event_log_a = _runtime(registry, _two_reads_then_deploy(), storage=storage)
    mission = runtime_a.run("deployer apres verification")
    assert mission.status is MissionStatus.AWAITING_CONFIRMATION
    assert len(runtime_a.observations) == 2  # les deux lectures ont reussi

    context_a = _context_from_log(registry, mission, event_log_a)

    runtime_a.save_checkpoint(mission)
    runtime_a.persist_events()
    checkpoint = storage.load_checkpoint()
    del runtime_a, event_log_a  # simule l'arret du process

    # Runtime B : nouvel EventLog explicitement charge depuis Storage, comme le
    # fait cli.py::resume (Runtime.load_event_log puis build_runtime(event_log=...)).
    reloaded_event_log = Runtime.load_event_log(storage)
    runtime_b, _ = _runtime(
        registry,
        _ScriptedReasoner([FinishDecision(outcome="success", summary="fait", confidence=1.0)]),
        storage=storage,
        event_log=reloaded_event_log,
    )
    resumed_mission = runtime_b.resume_mission(checkpoint)

    context_b = _context_from_log(registry, resumed_mission, reloaded_event_log)

    # Contexte strictement identique a celui qu'avait Runtime A au meme point.
    assert context_b.observations == context_a.observations
    assert runtime_b.observations == context_a.observations

    # La mission continue normalement depuis ce point (confirmation puis fin).
    result = runtime_b.resume_confirmation(
        resumed_mission, ConfirmationResponse(request_id=runtime_b.pending_confirmation.id, granted=True)
    )
    assert result.status is MissionStatus.SUCCEEDED
    assert len(runtime_b.observations) == 3  # 2 lectures + le deploy


# --- Cas 2 : reprise en attente de confirmation, avec historique prealable ----


def test_cas2_resume_awaiting_confirmation_preserves_both_request_and_prior_history() -> None:
    storage = InMemoryStorage()
    registry = _registry()

    runtime_a, _ = _runtime(registry, _two_reads_then_deploy(), storage=storage)
    mission = runtime_a.run("deployer apres verification")
    assert mission.status is MissionStatus.AWAITING_CONFIRMATION
    request_id = runtime_a.pending_confirmation.id
    tool_name = runtime_a.pending_confirmation.action.tool_name

    runtime_a.save_checkpoint(mission)
    runtime_a.persist_events()
    checkpoint = storage.load_checkpoint()
    del runtime_a

    reloaded_event_log = Runtime.load_event_log(storage)
    runtime_b, _ = _runtime(
        registry,
        _ScriptedReasoner([FinishDecision(outcome="failure", summary="annule", confidence=1.0)]),
        storage=storage,
        event_log=reloaded_event_log,
    )
    resumed_mission = runtime_b.resume_mission(checkpoint)

    # La ConfirmationRequest est retrouvee a l'identique.
    assert runtime_b.pending_confirmation is not None
    assert runtime_b.pending_confirmation.id == request_id
    assert runtime_b.pending_confirmation.action.tool_name == tool_name
    # ... et l'historique qui la precede (2 lectures reussies) aussi.
    assert len(runtime_b.observations) == 2
    assert all(obs.kind is ObservationKind.EXECUTION_RESULT for obs in runtime_b.observations)

    result = runtime_b.resume_confirmation(
        resumed_mission, ConfirmationResponse(request_id=request_id, granted=False, note="pas maintenant")
    )
    assert result.status is MissionStatus.FAILED
    # Historique prealable + observation de refus, dans cet ordre.
    assert len(runtime_b.observations) == 3
    assert runtime_b.observations[-1].kind is ObservationKind.CONFIRMATION_DENIED


# --- Cas 3 : crash / nouveau process, via deux FileStorage distincts ----------


def test_cas3_two_distinct_file_storage_instances_simulate_a_real_restart(tmp_path: Path) -> None:
    checkpoint_path = tmp_path / "checkpoint.json"
    registry = _registry()

    storage_a = FileStorage(checkpoint_path)
    runtime_a, _ = _runtime(registry, _two_reads_then_deploy(), storage=storage_a)
    mission = runtime_a.run("deployer apres verification")
    assert mission.status is MissionStatus.AWAITING_CONFIRMATION

    runtime_a.save_checkpoint(mission)
    runtime_a.persist_events()
    # Aucune reference Python partagee au-dela de ce point : nouvelle instance
    # de FileStorage sur le meme chemin, comme le ferait un nouveau process CLI.
    del runtime_a, storage_a

    events_path = tmp_path / "checkpoint.json.events.jsonl"
    assert checkpoint_path.exists()
    assert events_path.exists()

    storage_b = FileStorage(checkpoint_path)
    checkpoint = storage_b.load_checkpoint()
    assert checkpoint is not None

    reloaded_event_log = Runtime.load_event_log(storage_b)
    assert len(reloaded_event_log.list_events()) > 0

    runtime_b, _ = _runtime(
        registry,
        _ScriptedReasoner([FinishDecision(outcome="success", summary="fait", confidence=1.0)]),
        storage=storage_b,
        event_log=reloaded_event_log,
    )
    resumed_mission = runtime_b.resume_mission(checkpoint)

    assert len(runtime_b.observations) == 2
    assert runtime_b.pending_confirmation is not None

    result = runtime_b.resume_confirmation(
        resumed_mission, ConfirmationResponse(request_id=runtime_b.pending_confirmation.id, granted=True)
    )
    assert result.status is MissionStatus.SUCCEEDED
    assert len(runtime_b.observations) == 3


# --- Cas 4 : RetryingReasoner + Runtime + historique, via build_runtime() -----
#
# Les cas 1-3 construisent Runtime directement avec un Reasoner deja
# deterministe (_ScriptedReasoner), jamais un RetryingReasoner : ils prouvent
# la reprise d'historique, mais pas que le retry continue de fonctionner apres
# une reprise. Ce test utilise `build_runtime()` (composition.py, câble
# toujours `RetryingReasoner` autour de `LLMReasoner`, voir composition.py)
# des deux cotes du "redemarrage", avec un vrai `LLM` qui echoue une fois de
# facon generique (jamais `OllamaRequestError`, pour rester agnostique du
# fournisseur) avant de repondre correctement.


class _TransientlyFailingLLM(LLM):
    def __init__(self, fail_times: int, responses: list[str]) -> None:
        self._fail_times = fail_times
        self._responses = list(responses)
        self.call_count = 0

    def generate(self, messages: list[dict[str, str]]) -> str:
        self.call_count += 1
        if self.call_count <= self._fail_times:
            raise RuntimeError("panne transitoire generique")
        return self._responses.pop(0)


def _action_json(tool_name: str) -> str:
    return json.dumps({"kind": "action", "reasoning": "x", "tool_name": tool_name, "arguments": {}})


def _finish_json(outcome: str = "success", summary: str = "fait") -> str:
    return json.dumps({"kind": "finished", "outcome": outcome, "summary": summary, "confidence": 1.0})


def test_cas4_retry_still_recovers_from_a_transient_llm_failure_after_a_resume(tmp_path: Path) -> None:
    checkpoint_path = tmp_path / "checkpoint.json"
    read_tool = _StubTool("read_config", RiskLevel.LOW, output="config a")
    deploy_tool = _StubTool("deploy", RiskLevel.HIGH)

    storage_a = FileStorage(checkpoint_path)
    llm_a = _TransientlyFailingLLM(fail_times=0, responses=[_action_json("read_config"), _action_json("deploy")])
    runtime_a = build_runtime(llm=llm_a, tools=[read_tool, deploy_tool], storage=storage_a)

    mission = runtime_a.run("deployer apres verification")
    assert mission.status is MissionStatus.AWAITING_CONFIRMATION
    assert len(runtime_a.observations) == 1

    runtime_a.save_checkpoint(mission)
    runtime_a.persist_events()
    checkpoint = storage_a.load_checkpoint()
    del runtime_a, storage_a  # simule l'arret du process, comme le cas 3

    storage_b = FileStorage(checkpoint_path)
    reloaded_event_log = Runtime.load_event_log(storage_b)
    # La panne transitoire survient apres la reprise, sur la Decision qui suit
    # la confirmation accordee (pas sur une Action deja executee/persistee).
    llm_b = _TransientlyFailingLLM(fail_times=1, responses=[_finish_json(summary="deploiement termine")])
    runtime_b = build_runtime(llm=llm_b, tools=[read_tool, deploy_tool], storage=storage_b, event_log=reloaded_event_log)
    resumed_mission = runtime_b.resume_mission(checkpoint)

    # Historique d'avant redemarrage bien present malgre le build_runtime() cote B.
    assert len(runtime_b.observations) == 1
    assert runtime_b.observations[0].details["output"] == "config a"
    assert runtime_b.pending_confirmation is not None

    result = runtime_b.resume_confirmation(
        resumed_mission, ConfirmationResponse(request_id=runtime_b.pending_confirmation.id, granted=True)
    )

    assert result.status is MissionStatus.SUCCEEDED
    # 1 panne generique + 1 reponse exploitable : RetryingReasoner a bien
    # repris apres la reprise d'historique. `deploy` lui-meme n'est pas
    # rejoue par cette panne : elle survient sur le tour de raisonnement
    # *suivant* resume_confirmation(), qui a deja execute l'Action une seule
    # fois avant que le Reasoner ne soit consulte a nouveau.
    assert llm_b.call_count == 2
    assert len(runtime_b.observations) == 2
    assert runtime_b.observations[1].kind is ObservationKind.EXECUTION_RESULT


# --- Round-trip EventLog / absence de duplication / robustesse ----------------


def test_round_trip_through_file_storage_preserves_every_event_type_and_payload(tmp_path: Path) -> None:
    storage = FileStorage(tmp_path / "checkpoint.json")
    registry = _registry()
    runtime, event_log = _runtime(registry, _two_reads_then_deploy(), storage=storage)

    runtime.run("deployer apres verification")
    runtime.persist_events()

    reloaded = Runtime.load_event_log(storage)

    assert [event.type for event in reloaded.list_events()] == [event.type for event in event_log.list_events()]
    assert [event.payload for event in reloaded.list_events()] == [
        event.payload for event in event_log.list_events()
    ]
    assert reloaded.list_events() == event_log.list_events()


def test_no_duplication_across_repeated_checkpoints_in_the_same_cli_style_session(tmp_path: Path) -> None:
    # Reproduit le pattern de cli.py::_drive_to_completion : save_checkpoint()
    # + persist_events() a chaque passage par AWAITING_CONFIRMATION, plusieurs
    # fois dans la meme session Runtime.
    storage = FileStorage(tmp_path / "checkpoint.json")
    registry = _registry()
    reasoner = _ScriptedReasoner(
        [
            ActionDecision(reasoning="premier deploy", tool_name="deploy", arguments={}),
            ActionDecision(reasoning="second deploy", tool_name="deploy", arguments={}),
            FinishDecision(outcome="success", summary="fini", confidence=1.0),
        ]
    )
    runtime, event_log = _runtime(registry, reasoner, storage=storage)

    mission = runtime.run("deployer deux fois")
    assert mission.status is MissionStatus.AWAITING_CONFIRMATION
    runtime.save_checkpoint(mission)
    runtime.persist_events()
    after_first_checkpoint = len(storage.load_events())
    assert after_first_checkpoint == len(event_log.list_events())

    mission = runtime.resume_confirmation(
        mission, ConfirmationResponse(request_id=runtime.pending_confirmation.id, granted=True)
    )
    assert mission.status is MissionStatus.AWAITING_CONFIRMATION
    runtime.save_checkpoint(mission)
    runtime.persist_events()

    all_events = storage.load_events()
    assert all_events == event_log.list_events()
    assert len(all_events) == len({event.id for event in all_events})  # aucun doublon


def test_resume_with_no_checkpoint_ever_saved_returns_none_and_empty_history(tmp_path: Path) -> None:
    storage = FileStorage(tmp_path / "checkpoint.json")

    assert storage.load_checkpoint() is None
    assert Runtime.load_event_log(storage).list_events() == []


def test_resume_with_corrupted_persisted_events_raises_before_any_reasoning(tmp_path: Path) -> None:
    checkpoint_path = tmp_path / "checkpoint.json"
    events_path = tmp_path / "checkpoint.json.events.jsonl"
    storage = FileStorage(checkpoint_path)
    registry = _registry()
    runtime, _ = _runtime(registry, _two_reads_then_deploy(), storage=storage)
    mission = runtime.run("deployer apres verification")
    runtime.save_checkpoint(mission)
    runtime.persist_events()

    # Corruption externe du fichier d'evenements (disque endommage, edition
    # manuelle, etc.) : une ligne n'est plus du JSON valide.
    with events_path.open("a", encoding="utf-8") as handle:
        handle.write("ceci n'est pas un evenement JSON valide\n")

    fresh_storage = FileStorage(checkpoint_path)
    try:
        Runtime.load_event_log(fresh_storage)
        raised = False
    except CorruptedEventLogError:
        raised = True
    assert raised
    # Le Checkpoint, lui, reste lisible independamment (fichiers separes).
    assert fresh_storage.load_checkpoint() is not None


# --- Bonus : deux process Python distincts (simple, best-effort) --------------


def test_cas3_bonus_two_distinct_python_processes_share_only_the_disk(tmp_path: Path) -> None:
    checkpoint_path = tmp_path / "checkpoint.json"
    tests_dir = str(Path(__file__).parent)

    producer = textwrap.dedent(
        f"""
        import sys
        sys.path.insert(0, {tests_dir!r})
        from pathlib import Path
        from peon.context_builder import ContextBuilder
        from peon.event_log import EventLog
        from peon.executor import Executor
        from peon.models.decision import ActionDecision
        from peon.models.tool_spec import RiskLevel
        from peon.policy import PolicyEngine
        from peon.runtime import Runtime
        from peon.storage import FileStorage
        from peon.tool_registry import ToolRegistry
        from test_resume_history import _StubTool, _ScriptedReasoner

        registry = ToolRegistry()
        registry.register(_StubTool("read_config", RiskLevel.LOW, output="config a"))
        registry.register(_StubTool("deploy", RiskLevel.HIGH))
        storage = FileStorage(Path({str(checkpoint_path)!r}))
        runtime = Runtime(
            context_builder=ContextBuilder(registry),
            reasoner=_ScriptedReasoner([
                ActionDecision(reasoning="lire", tool_name="read_config", arguments={{}}),
                ActionDecision(reasoning="deployer", tool_name="deploy", arguments={{}}),
            ]),
            policy_engine=PolicyEngine(registry),
            executor=Executor(registry),
            event_log=EventLog(),
            storage=storage,
        )
        mission = runtime.run("deployer depuis le process A")
        assert mission.status.value == "awaiting_confirmation"
        runtime.save_checkpoint(mission)
        runtime.persist_events()
        print("PRODUCER_OK")
        """
    )

    consumer = textwrap.dedent(
        f"""
        import sys
        sys.path.insert(0, {tests_dir!r})
        from pathlib import Path
        from peon.context_builder import ContextBuilder
        from peon.event_log import EventLog
        from peon.executor import Executor
        from peon.models.confirmation import ConfirmationResponse
        from peon.models.decision import FinishDecision
        from peon.models.tool_spec import RiskLevel
        from peon.policy import PolicyEngine
        from peon.runtime import Runtime
        from peon.storage import FileStorage
        from peon.tool_registry import ToolRegistry
        from test_resume_history import _StubTool, _ScriptedReasoner

        registry = ToolRegistry()
        registry.register(_StubTool("read_config", RiskLevel.LOW, output="config a"))
        registry.register(_StubTool("deploy", RiskLevel.HIGH))
        storage = FileStorage(Path({str(checkpoint_path)!r}))
        checkpoint = storage.load_checkpoint()
        assert checkpoint is not None
        event_log = Runtime.load_event_log(storage)
        assert len(event_log.list_events()) > 0
        runtime = Runtime(
            context_builder=ContextBuilder(registry),
            reasoner=_ScriptedReasoner([FinishDecision(outcome="success", summary="fait", confidence=1.0)]),
            policy_engine=PolicyEngine(registry),
            executor=Executor(registry),
            event_log=event_log,
            storage=storage,
        )
        mission = runtime.resume_mission(checkpoint)
        assert len(runtime.observations) == 1
        result = runtime.resume_confirmation(
            mission, ConfirmationResponse(request_id=runtime.pending_confirmation.id, granted=True)
        )
        assert result.status.value == "succeeded"
        print("CONSUMER_OK")
        """
    )

    env_python = sys.executable
    producer_result = subprocess.run(
        [env_python, "-c", producer], capture_output=True, text=True, cwd=tests_dir, timeout=30
    )
    assert producer_result.returncode == 0, producer_result.stderr
    assert "PRODUCER_OK" in producer_result.stdout

    consumer_result = subprocess.run(
        [env_python, "-c", consumer], capture_output=True, text=True, cwd=tests_dir, timeout=30
    )
    assert consumer_result.returncode == 0, consumer_result.stderr
    assert "CONSUMER_OK" in consumer_result.stdout

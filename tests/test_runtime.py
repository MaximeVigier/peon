from typing import Any, Literal

from peon.context_builder import ContextBuilder
from peon.event_log import EventLog
from peon.executor import Executor
from peon.llm import LLM
from peon.models.context import Context
from peon.models.decision import ActionDecision, Decision, FinishDecision
from peon.models.events import EventType
from peon.models.mission import MissionStatus
from peon.models.observation import ObservationKind
from peon.models.tool_result import ToolResult
from peon.models.tool_spec import RiskLevel, ToolSpec
from peon.policy import PolicyEngine
from peon.prompts import PromptBuilder
from peon.providers.ollama import OllamaRequestError
from peon.reasoner import InvalidLLMResponseError, LLMReasoner, Reasoner, RetryingReasoner
from peon.runtime import Runtime
from peon.tool_registry import ToolRegistry
from peon.tools.base import Tool


class _StubTool(Tool):
    def __init__(self, name: str, risk_level: RiskLevel, result: ToolResult | None = None) -> None:
        self._spec = ToolSpec(
            name=name,
            description=f"Outil {name}",
            parameters_schema={"type": "object", "properties": {}},
            risk_level=risk_level,
        )
        self._result = result if result is not None else ToolResult(success=True, output="ok")

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        return self._result


def _registry(*tools: Tool) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    return registry


def _action_decision(tool_name: str, **arguments: Any) -> ActionDecision:
    return ActionDecision(reasoning="x", tool_name=tool_name, arguments=arguments)


def _finish(outcome: Literal["success", "failure"], summary: str = "termine") -> FinishDecision:
    return FinishDecision(outcome=outcome, summary=summary, confidence=1.0)


class _ScriptedReasoner(Reasoner):
    def __init__(self, decisions: list[Decision]) -> None:
        self._decisions = list(decisions)
        self.received_contexts: list[Context] = []

    def decide(self, context: Context) -> Decision:
        self.received_contexts.append(context)
        return self._decisions.pop(0)


class _RepeatingReasoner(Reasoner):
    def __init__(self, decision: Decision) -> None:
        self._decision = decision
        self.call_count = 0

    def decide(self, context: Context) -> Decision:
        self.call_count += 1
        return self._decision


class _FailingReasoner(Reasoner):
    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self.call_count = 0

    def decide(self, context: Context) -> Decision:
        self.call_count += 1
        raise self._exc


class _ScriptedThenFailingReasoner(Reasoner):
    def __init__(self, decisions: list[Decision], exc: Exception) -> None:
        self._decisions = list(decisions)
        self._exc = exc
        self.call_count = 0

    def decide(self, context: Context) -> Decision:
        self.call_count += 1
        if self._decisions:
            return self._decisions.pop(0)
        raise self._exc


class _FailingLLM(LLM):
    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self.call_count = 0

    def generate(self, messages: list[dict[str, str]]) -> str:
        self.call_count += 1
        raise self._exc


def _runtime(registry: ToolRegistry, reasoner: Reasoner) -> tuple[Runtime, EventLog]:
    event_log = EventLog()
    runtime = Runtime(
        context_builder=ContextBuilder(registry),
        reasoner=reasoner,
        policy_engine=PolicyEngine(registry),
        executor=Executor(registry),
        event_log=event_log,
    )
    return runtime, event_log


def test_immediate_success_finishes_the_mission() -> None:
    reasoner = _ScriptedReasoner([_finish("success")])
    runtime, event_log = _runtime(_registry(), reasoner)

    mission = runtime.run("corriger le bug de parsing")

    assert mission.status is MissionStatus.SUCCEEDED
    assert mission.iteration_count == 1
    assert [event.type for event in event_log.list_events()] == [
        EventType.MISSION_CREATED,
        EventType.STATE_TRANSITIONED,
        EventType.CONTEXT_BUILT,
        EventType.DECISION_RECEIVED,
        EventType.MISSION_SUCCEEDED,
        EventType.STATE_TRANSITIONED,
    ]


def test_immediate_failure_fails_the_mission() -> None:
    reasoner = _ScriptedReasoner([_finish("failure")])
    runtime, _ = _runtime(_registry(), reasoner)

    mission = runtime.run("tache impossible")

    assert mission.status is MissionStatus.FAILED


def test_allowed_action_executes_then_mission_finishes() -> None:
    reasoner = _ScriptedReasoner([_action_decision("read_file", path="README.md"), _finish("success")])
    runtime, event_log = _runtime(_registry(_StubTool("read_file", RiskLevel.LOW)), reasoner)

    mission = runtime.run("lire le readme")

    assert mission.status is MissionStatus.SUCCEEDED
    assert mission.iteration_count == 2
    assert len(runtime.observations) == 1
    assert runtime.observations[0].kind is ObservationKind.EXECUTION_RESULT
    assert EventType.TOOL_EXECUTION_COMPLETED in [e.type for e in event_log.list_events()]


def test_second_context_includes_the_observation_from_the_first_cycle() -> None:
    reasoner = _ScriptedReasoner([_action_decision("read_file"), _finish("success")])
    runtime, _ = _runtime(_registry(_StubTool("read_file", RiskLevel.LOW)), reasoner)

    runtime.run("lire le readme")

    assert reasoner.received_contexts[0].observations == []
    assert len(reasoner.received_contexts[1].observations) == 1
    observation = reasoner.received_contexts[1].observations[0]
    assert observation.kind is ObservationKind.EXECUTION_RESULT
    # Round-trip par l'EventLog (build_from_event_log) : les details doivent
    # survivre, pas seulement kind/summary.
    assert observation.details == {"tool_name": "read_file", "output": "ok"}


def test_high_risk_action_pauses_on_confirmation() -> None:
    reasoner = _ScriptedReasoner([_action_decision("git_push")])
    runtime, event_log = _runtime(_registry(_StubTool("git_push", RiskLevel.HIGH)), reasoner)

    mission = runtime.run("publier les changements")

    assert mission.status is MissionStatus.AWAITING_CONFIRMATION
    assert runtime.pending_confirmation is not None
    assert runtime.pending_confirmation.action.tool_name == "git_push"
    assert EventType.CONFIRMATION_REQUESTED in [e.type for e in event_log.list_events()]


def test_denied_action_produces_an_observation_and_continues() -> None:
    reasoner = _ScriptedReasoner([_action_decision("run_command", command="rm -rf /"), _finish("failure")])
    runtime, _ = _runtime(_registry(_StubTool("run_command", RiskLevel.HIGH)), reasoner)

    mission = runtime.run("nettoyer le disque")

    assert mission.status is MissionStatus.FAILED
    assert mission.iteration_count == 2
    assert len(runtime.observations) == 1
    assert runtime.observations[0].kind is ObservationKind.POLICY_REJECTION


def test_rewrite_action_carries_the_suggestion_in_the_observation() -> None:
    reasoner = _ScriptedReasoner([_action_decision("run_command", command="rm -rf build"), _finish("success")])
    runtime, _ = _runtime(_registry(_StubTool("run_command", RiskLevel.HIGH)), reasoner)

    runtime.run("nettoyer le build")

    observation = runtime.observations[0]
    assert observation.kind is ObservationKind.POLICY_REJECTION
    assert observation.details is not None
    assert observation.details["suggested_arguments"]["command"] == "rm -rf -- ./build"


def test_unknown_tool_is_denied_without_crashing() -> None:
    reasoner = _ScriptedReasoner([_action_decision("does_not_exist"), _finish("failure")])
    runtime, _ = _runtime(_registry(), reasoner)

    mission = runtime.run("appeler un outil inexistant")

    assert mission.status is MissionStatus.FAILED
    assert runtime.observations[0].kind is ObservationKind.POLICY_REJECTION


def test_max_iterations_stops_the_loop() -> None:
    reasoner = _RepeatingReasoner(_action_decision("read_file"))
    runtime, _ = _runtime(_registry(_StubTool("read_file", RiskLevel.LOW)), reasoner)

    mission = runtime.run("boucle infinie", max_iterations=2)

    assert mission.status is MissionStatus.MAX_ITERATIONS
    assert mission.iteration_count == 3
    assert reasoner.call_count == 2


def test_tool_execution_error_produces_an_execution_error_observation() -> None:
    failing_result = ToolResult(success=False, error="fichier introuvable")
    reasoner = _ScriptedReasoner([_action_decision("read_file"), _finish("failure")])
    runtime, _ = _runtime(_registry(_StubTool("read_file", RiskLevel.LOW, result=failing_result)), reasoner)

    runtime.run("lire un fichier absent")

    assert runtime.observations[0].kind is ObservationKind.EXECUTION_ERROR


def test_invalid_llm_response_error_fails_the_mission_without_crashing() -> None:
    reasoner = _FailingReasoner(InvalidLLMResponseError("reponse LLM invalide (pas un JSON)"))
    runtime, event_log = _runtime(_registry(), reasoner)

    mission = runtime.run("tache quelconque")

    assert mission.status is MissionStatus.FAILED
    assert reasoner.call_count == 1
    assert runtime.observations == []
    event_types = [event.type for event in event_log.list_events()]
    assert EventType.MISSION_FAILED in event_types
    assert EventType.DECISION_RECEIVED not in event_types
    assert EventType.POLICY_EVALUATED not in event_types
    assert EventType.TOOL_EXECUTION_COMPLETED not in event_types


def test_ollama_request_error_fails_the_mission_without_crashing() -> None:
    # Bout en bout via un vrai LLMReasoner + un LLM qui echoue comme OllamaLLM
    # le ferait (OllamaRequestError). Le Runtime n'importe jamais
    # peon.providers.ollama : seul ce test le fait, pour prouver que la
    # traduction se fait au niveau du Reasoner (voir test_reasoner.py) et que
    # le Runtime reste robuste sans connaitre ce type concret.
    llm = _FailingLLM(OllamaRequestError("appel a Ollama impossible"))
    reasoner = LLMReasoner(llm=llm, prompt_builder=PromptBuilder())
    runtime, _ = _runtime(_registry(), reasoner)

    mission = runtime.run("tache quelconque")

    assert mission.status is MissionStatus.FAILED
    assert llm.call_count == 1
    assert runtime.observations == []


def test_history_before_a_late_llm_failure_is_preserved() -> None:
    reasoner = _ScriptedThenFailingReasoner(
        [_action_decision("read_file", path="README.md")],
        InvalidLLMResponseError("panne apres une action reussie"),
    )
    runtime, event_log = _runtime(_registry(_StubTool("read_file", RiskLevel.LOW)), reasoner)

    mission = runtime.run("lire puis echouer au tour suivant")

    assert mission.status is MissionStatus.FAILED
    assert reasoner.call_count == 2
    assert len(runtime.observations) == 1
    assert runtime.observations[0].kind is ObservationKind.EXECUTION_RESULT
    event_types = [event.type for event in event_log.list_events()]
    assert EventType.TOOL_EXECUTION_COMPLETED in event_types
    assert EventType.OBSERVATION_PRODUCED in event_types
    assert EventType.MISSION_FAILED in event_types


# --- RetryingReasoner, vu depuis Runtime ---------------------------------
#
# Runtime ne sait pas qu'un retry a eu lieu : il continue d'appeler
# reasoner.decide(context) une seule fois par cycle. Ces tests verifient que
# ce contrat tient bel et bien quand le Reasoner injecte est un
# RetryingReasoner - en particulier qu'aucune Action/Tool n'est jamais
# rejouee a cause d'une reprise interne au Reasoner.


class _SequencedReasoner(Reasoner):
    # Contrairement a _ScriptedReasoner (qui ne fait que retourner des
    # Decision), accepte une sequence melangeant Decision et Exception : une
    # entree Exception est levee, pas retournee. Necessaire pour scripter un
    # echec suivi d'un succes (RetryingReasoner), scenario qu'aucun double
    # existant dans ce fichier ne couvrait.
    def __init__(self, outcomes: list[Decision | Exception]) -> None:
        self._outcomes = list(outcomes)
        self.received_contexts: list[Context] = []
        self.call_count = 0

    def decide(self, context: Context) -> Decision:
        self.received_contexts.append(context)
        self.call_count += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _CountingTool(Tool):
    def __init__(self, name: str, risk_level: RiskLevel) -> None:
        self._spec = ToolSpec(
            name=name,
            description=f"Outil {name}",
            parameters_schema={"type": "object", "properties": {}},
            risk_level=risk_level,
        )
        self.call_count = 0

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        self.call_count += 1
        return ToolResult(success=True, output="ok")


def test_runtime_recovers_when_the_retrying_reasoner_succeeds_on_a_later_attempt() -> None:
    inner = _SequencedReasoner([InvalidLLMResponseError("panne transitoire"), _finish("success")])
    reasoner = RetryingReasoner(inner, max_attempts=2)
    runtime, event_log = _runtime(_registry(), reasoner)

    mission = runtime.run("tache quelconque")

    assert mission.status is MissionStatus.SUCCEEDED
    # Un seul cycle de raisonnement du point de vue de Runtime, malgre deux
    # appels internes au Reasoner enveloppe : la reprise reste invisible pour
    # le compteur d'iterations de la Mission.
    assert mission.iteration_count == 1
    assert inner.call_count == 2
    event_types = [event.type for event in event_log.list_events()]
    assert event_types.count(EventType.MISSION_FAILED) == 0
    assert event_types.count(EventType.MISSION_SUCCEEDED) == 1


def test_runtime_fails_only_after_the_retrying_reasoner_exhausts_its_attempts() -> None:
    inner = _FailingReasoner(InvalidLLMResponseError("panne persistante"))
    reasoner = RetryingReasoner(inner, max_attempts=3)
    runtime, event_log = _runtime(_registry(), reasoner)

    mission = runtime.run("tache quelconque")

    assert mission.status is MissionStatus.FAILED
    assert inner.call_count == 3
    event_types = [event.type for event in event_log.list_events()]
    assert event_types.count(EventType.MISSION_FAILED) == 1
    assert EventType.DECISION_RECEIVED not in event_types
    assert EventType.POLICY_EVALUATED not in event_types
    assert EventType.TOOL_EXECUTION_COMPLETED not in event_types


def test_retry_inside_one_cycle_never_executes_a_tool_twice() -> None:
    tool = _CountingTool("read_file", RiskLevel.LOW)
    inner = _SequencedReasoner(
        [
            _action_decision("read_file"),
            InvalidLLMResponseError("panne transitoire apres l'action"),
            _finish("success"),
        ]
    )
    reasoner = RetryingReasoner(inner, max_attempts=2)
    runtime, _ = _runtime(_registry(tool), reasoner)

    mission = runtime.run("lire puis subir une panne transitoire")

    assert mission.status is MissionStatus.SUCCEEDED
    assert tool.call_count == 1
    assert inner.call_count == 3


def test_history_before_a_late_failure_is_preserved_even_when_retries_are_exhausted() -> None:
    # Variante securite du scenario "Action reussie -> nouveau tour -> panne
    # LLM" avec un RetryingReasoner qui epuise ses tentatives plutot qu'un
    # Reasoner qui echoue au premier coup : memes garanties attendues.
    tool = _CountingTool("read_file", RiskLevel.LOW)
    inner = _ScriptedThenFailingReasoner(
        [_action_decision("read_file", path="README.md")],
        InvalidLLMResponseError("panne persistante apres une action reussie"),
    )
    reasoner = RetryingReasoner(inner, max_attempts=2)
    runtime, event_log = _runtime(_registry(tool), reasoner)

    mission = runtime.run("lire puis echouer durablement au tour suivant")

    assert mission.status is MissionStatus.FAILED
    assert tool.call_count == 1
    assert len(runtime.observations) == 1
    assert runtime.observations[0].kind is ObservationKind.EXECUTION_RESULT
    event_types = [event.type for event in event_log.list_events()]
    assert event_types.count(EventType.TOOL_EXECUTION_COMPLETED) == 1
    assert event_types.count(EventType.OBSERVATION_PRODUCED) == 1
    assert event_types.count(EventType.MISSION_FAILED) == 1


class _StubReasonerRaisingGenericException(Reasoner):
    # Preuve d'agnosticisme fournisseur au niveau Runtime : ni Ollama ni
    # OllamaRequestError ne sont importes ici, seule InvalidLLMResponseError
    # (le contrat deja normalise par reasoner.py) est utilisee.
    def decide(self, context: Context) -> Decision:
        raise InvalidLLMResponseError("panne generique, aucun fournisseur concret implique")


def test_runtime_stays_provider_agnostic_through_a_retrying_reasoner() -> None:
    reasoner = RetryingReasoner(_StubReasonerRaisingGenericException(), max_attempts=2)
    runtime, _ = _runtime(_registry(), reasoner)

    mission = runtime.run("tache quelconque")

    assert mission.status is MissionStatus.FAILED

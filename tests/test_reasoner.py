from typing import Any

import pytest

from peon.llm import LLM
from peon.models.context import Context
from peon.models.decision import ActionDecision, Decision, FinishDecision
from peon.models.mission import MissionStatus
from peon.prompts import PromptBuilder
from peon.providers.ollama import OllamaRequestError
from peon.reasoner import InvalidLLMResponseError, LLMReasoner, Reasoner, RetryingReasoner
from peon.tracing import Span, Tracer


def _context() -> Context:
    return Context(
        mission_goal="corriger le bug de parsing",
        mission_status=MissionStatus.REASONING,
        mission_iteration_count=0,
    )


class _StubReasoner(Reasoner):
    def decide(self, context: Context) -> Decision:
        return FinishDecision(outcome="success", summary="mission terminee", confidence=1.0)


def test_reasoner_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        Reasoner()  # type: ignore[abstract]


def test_subclass_missing_decide_cannot_be_instantiated() -> None:
    class _MissingDecide(Reasoner):
        pass

    with pytest.raises(TypeError):
        _MissingDecide()  # type: ignore[abstract]


def test_conforming_subclass_returns_a_finish_decision() -> None:
    decision = _StubReasoner().decide(_context())

    assert isinstance(decision, FinishDecision)
    assert decision.outcome == "success"


def test_conforming_subclass_can_return_an_action_decision() -> None:
    class _ActionReasoner(Reasoner):
        def decide(self, context: Context) -> Decision:
            return ActionDecision(reasoning="lire le fichier", tool_name="read_file", arguments={"path": "README.md"})

    decision = _ActionReasoner().decide(_context())

    assert isinstance(decision, ActionDecision)
    assert decision.tool_name == "read_file"


def test_decide_receives_the_given_context() -> None:
    received: list[Context] = []

    class _RecordingReasoner(Reasoner):
        def decide(self, context: Context) -> Decision:
            received.append(context)
            return FinishDecision(outcome="success", summary="ok", confidence=1.0)

    context = _context()
    _RecordingReasoner().decide(context)

    assert received == [context]


class _StubLLM(LLM):
    def __init__(self, response: str) -> None:
        self._response = response
        self.received_messages: list[list[dict[str, str]]] | None = None

    def generate(self, messages: list[dict[str, str]]) -> str:
        self.received_messages = messages
        return self._response


def test_llm_reasoner_uses_only_context_and_injected_llm() -> None:
    llm = _StubLLM('{"kind": "finished", "outcome": "success", "summary": "ok", "confidence": 1.0}')
    context = _context()

    LLMReasoner(llm=llm, prompt_builder=PromptBuilder()).decide(context)

    assert llm.received_messages == PromptBuilder().build(context)


def test_llm_reasoner_parses_a_valid_action_decision() -> None:
    llm = _StubLLM(
        '{"kind": "action", "reasoning": "lire le fichier", "tool_name": "read_file", '
        '"arguments": {"path": "README.md"}}'
    )

    decision = LLMReasoner(llm=llm, prompt_builder=PromptBuilder()).decide(_context())

    assert isinstance(decision, ActionDecision)
    assert decision.tool_name == "read_file"
    assert decision.arguments == {"path": "README.md"}


def test_llm_reasoner_parses_a_valid_finish_decision() -> None:
    llm = _StubLLM('{"kind": "finished", "outcome": "failure", "summary": "bloque", "confidence": 0.2}')

    decision = LLMReasoner(llm=llm, prompt_builder=PromptBuilder()).decide(_context())

    assert isinstance(decision, FinishDecision)
    assert decision.outcome == "failure"


def test_llm_reasoner_raises_on_invalid_json() -> None:
    llm = _StubLLM("ceci n'est pas du JSON")

    with pytest.raises(InvalidLLMResponseError):
        LLMReasoner(llm=llm, prompt_builder=PromptBuilder()).decide(_context())


def test_llm_reasoner_raises_when_kind_is_missing() -> None:
    llm = _StubLLM('{"reasoning": "lire le fichier", "tool_name": "read_file"}')

    with pytest.raises(InvalidLLMResponseError):
        LLMReasoner(llm=llm, prompt_builder=PromptBuilder()).decide(_context())


def test_llm_reasoner_raises_on_unknown_kind() -> None:
    llm = _StubLLM('{"kind": "unknown"}')

    with pytest.raises(InvalidLLMResponseError):
        LLMReasoner(llm=llm, prompt_builder=PromptBuilder()).decide(_context())


def test_llm_reasoner_raises_on_incorrect_arguments() -> None:
    llm = _StubLLM('{"kind": "finished", "outcome": "maybe", "summary": "ok", "confidence": 1.0}')

    with pytest.raises(InvalidLLMResponseError):
        LLMReasoner(llm=llm, prompt_builder=PromptBuilder()).decide(_context())


class _FailingLLM(LLM):
    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self.call_count = 0

    def generate(self, messages: list[dict[str, str]]) -> str:
        self.call_count += 1
        raise self._exc


def test_llm_reasoner_wraps_an_ollama_request_error_into_invalid_llm_response_error() -> None:
    # OllamaRequestError est le type concret leve par le premier fournisseur
    # (providers/ollama.py), mais LLMReasoner ne doit connaitre que le contrat
    # abstrait LLM.generate() -- ce test verifie que le fournisseur concret
    # importe ici (dans le test, pas dans reasoner.py) est bien traduit en
    # l'exception dediee du Reasoner, sans retry.
    llm = _FailingLLM(OllamaRequestError("ollama injoignable"))

    with pytest.raises(InvalidLLMResponseError):
        LLMReasoner(llm=llm, prompt_builder=PromptBuilder()).decide(_context())

    assert llm.call_count == 1


def test_llm_reasoner_wraps_any_generate_failure_regardless_of_provider() -> None:
    # Aucune hypothese sur le fournisseur : une exception generique levee par
    # generate() (reseau, timeout, provider futur...) doit aussi etre traduite,
    # pas seulement OllamaRequestError.
    llm = _FailingLLM(RuntimeError("panne reseau generique"))

    with pytest.raises(InvalidLLMResponseError):
        LLMReasoner(llm=llm, prompt_builder=PromptBuilder()).decide(_context())

    assert llm.call_count == 1


# --- RetryingReasoner ---------------------------------------------------
#
# Doubles definis localement (meme convention que les autres modules de
# tests) : _RecordingReasoner n'importe rien d'Ollama, seulement le contrat
# Reasoner/InvalidLLMResponseError deja normalise - preuve directe que la
# reprise reste fournisseur-agnostique.


class _RecordingReasoner(Reasoner):
    def __init__(self, outcomes: list[Decision | Exception]) -> None:
        self._outcomes = list(outcomes)
        self.call_count = 0

    def decide(self, context: Context) -> Decision:
        self.call_count += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _RecordedSpan(Span):
    def __init__(self, name: str, attributes: dict[str, Any]) -> None:
        self.name = name
        self.attributes = dict(attributes)

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def record_exception(self, exc: BaseException) -> None:
        pass


class _RecordingTracer(Tracer):
    def __init__(self) -> None:
        self.spans: list[_RecordedSpan] = []

    def _start_span(self, name: str) -> Span:
        span = _RecordedSpan(name, {})
        self.spans.append(span)
        return span

    def _end_span(self, span: Span) -> None:
        pass


def test_retrying_reasoner_returns_result_from_first_successful_attempt() -> None:
    inner = _RecordingReasoner([_finish_decision()])

    decision = RetryingReasoner(inner).decide(_context())

    assert isinstance(decision, FinishDecision)
    assert inner.call_count == 1


def test_retrying_reasoner_recovers_after_one_transient_failure() -> None:
    inner = _RecordingReasoner([InvalidLLMResponseError("panne transitoire"), _finish_decision()])

    decision = RetryingReasoner(inner, max_attempts=2).decide(_context())

    assert isinstance(decision, FinishDecision)
    assert inner.call_count == 2


def test_retrying_reasoner_raises_after_exhausting_all_attempts() -> None:
    inner = _RecordingReasoner(
        [InvalidLLMResponseError("1"), InvalidLLMResponseError("2"), InvalidLLMResponseError("3")]
    )

    with pytest.raises(InvalidLLMResponseError):
        RetryingReasoner(inner, max_attempts=3).decide(_context())

    assert inner.call_count == 3


def test_retrying_reasoner_never_exceeds_max_attempts() -> None:
    # Meme si l'echec pourrait continuer indefiniment, la boucle reste bornee :
    # ne fournir que exactement max_attempts echecs suffit (pas un de plus).
    inner = _RecordingReasoner([InvalidLLMResponseError("1"), InvalidLLMResponseError("2")])

    with pytest.raises(InvalidLLMResponseError):
        RetryingReasoner(inner, max_attempts=2).decide(_context())

    assert inner.call_count == 2


def test_retrying_reasoner_default_max_attempts_is_small() -> None:
    inner = _RecordingReasoner([InvalidLLMResponseError("1"), InvalidLLMResponseError("2")])

    with pytest.raises(InvalidLLMResponseError):
        RetryingReasoner(inner).decide(_context())

    assert inner.call_count == 2


def test_retrying_reasoner_does_not_retry_an_unexpected_exception_type() -> None:
    # Seule InvalidLLMResponseError (le contrat deja normalise du Reasoner)
    # declenche une reprise : une exception inattendue (bug d'implementation)
    # doit remonter immediatement, jamais etre masquee par un retry.
    inner = _RecordingReasoner([RuntimeError("bug d'implementation"), _finish_decision()])

    with pytest.raises(RuntimeError):
        RetryingReasoner(inner, max_attempts=3).decide(_context())

    assert inner.call_count == 1


def test_retrying_reasoner_rejects_max_attempts_below_one() -> None:
    with pytest.raises(ValueError):
        RetryingReasoner(_RecordingReasoner([]), max_attempts=0)


def test_retrying_reasoner_traces_one_span_per_attempt() -> None:
    tracer = _RecordingTracer()
    inner = _RecordingReasoner([InvalidLLMResponseError("panne transitoire"), _finish_decision()])

    RetryingReasoner(inner, max_attempts=2, tracer=tracer).decide(_context())

    assert [span.name for span in tracer.spans] == ["reasoner.decide.attempt", "reasoner.decide.attempt"]
    assert [span.attributes["attempt"] for span in tracer.spans] == [1, 2]
    assert all(span.attributes["max_attempts"] == 2 for span in tracer.spans)


def test_retrying_reasoner_without_tracer_behaves_like_noop() -> None:
    inner = _RecordingReasoner([_finish_decision()])

    decision = RetryingReasoner(inner).decide(_context())

    assert isinstance(decision, FinishDecision)


def _finish_decision() -> FinishDecision:
    return FinishDecision(outcome="success", summary="ok", confidence=1.0)

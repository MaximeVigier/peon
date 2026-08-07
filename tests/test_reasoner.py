import pytest

from peon.llm import LLM
from peon.models.context import Context
from peon.models.decision import ActionDecision, Decision, FinishDecision
from peon.models.mission import MissionStatus
from peon.prompts import PromptBuilder
from peon.reasoner import InvalidLLMResponseError, LLMReasoner, Reasoner


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

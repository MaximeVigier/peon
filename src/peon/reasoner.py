import json
from abc import ABC, abstractmethod

from pydantic import TypeAdapter, ValidationError

from peon.llm import LLM
from peon.models.context import Context
from peon.models.decision import Decision
from peon.prompts import PromptBuilder

_decision_adapter: TypeAdapter[Decision] = TypeAdapter(Decision)


class Reasoner(ABC):
    @abstractmethod
    def decide(self, context: Context) -> Decision:
        # Ne recoit que le Context : aucun acces au ToolRegistry, a un Tool
        # concret, au Policy Engine ou a l'Event Log n'est possible par ce
        # contrat. N'execute jamais rien lui-meme, ne retourne qu'une Decision.
        ...


class InvalidLLMResponseError(Exception):
    pass


class LLMReasoner(Reasoner):
    def __init__(self, llm: LLM, prompt_builder: PromptBuilder) -> None:
        self._llm = llm
        self._prompt_builder = prompt_builder

    def decide(self, context: Context) -> Decision:
        messages = self._prompt_builder.build(context)
        raw_response = self._llm.generate(messages)
        return self._parse(raw_response)

    @staticmethod
    def _parse(raw_response: str) -> Decision:
        try:
            payload = json.loads(raw_response)
        except json.JSONDecodeError as exc:
            raise InvalidLLMResponseError(f"reponse LLM invalide (pas un JSON) : {exc}") from exc

        try:
            return _decision_adapter.validate_python(payload)
        except ValidationError as exc:
            raise InvalidLLMResponseError(
                f"reponse LLM invalide (ne correspond a aucune Decision connue) : {exc}"
            ) from exc

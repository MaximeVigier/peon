import json
from abc import ABC, abstractmethod

from pydantic import TypeAdapter, ValidationError

from peon.llm import LLM
from peon.models.context import Context
from peon.models.decision import Decision
from peon.prompts import PromptBuilder
from peon.tracing import NoOpTracer, Tracer

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
        try:
            raw_response = self._llm.generate(messages)
        except Exception as exc:
            # LLM.generate() ne declare aucune exception dans son contrat abstrait
            # (llm.py) : quel que soit le fournisseur concret (Ollama, futurs
            # OpenAI/Anthropic...), un echec ici est traduit dans l'exception
            # dediee existante du Reasoner plutot que de laisser fuir un type
            # specifique au fournisseur jusqu'au Runtime.
            raise InvalidLLMResponseError(f"appel LLM en echec : {exc}") from exc
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


class RetryingReasoner(Reasoner):
    # Decorateur de Reasoner, pas une variante de LLMReasoner : la seule
    # information necessaire pour decider si une nouvelle tentative a un sens
    # est le contrat deja normalise par Reasoner.decide() (leve au plus
    # InvalidLLMResponseError), jamais un detail de fournisseur. En
    # consequence, enveloppe n'importe quel Reasoner (y compris un stub de
    # test) et reste totalement invisible pour Runtime : celui-ci continue
    # d'appeler decide(context) une seule fois par cycle et de traiter le
    # resultat (Decision ou InvalidLLMResponseError) exactement comme avant.
    #
    # Les tentatives supplementaires ne rejouent jamais qu'un appel LLM : ce
    # point du cycle precede toujours la construction d'une Action (voir
    # Runtime._run_reasoning_cycle), donc aucune Action ni aucun Tool n'est
    # jamais concerne par ce retry.
    def __init__(self, reasoner: Reasoner, *, max_attempts: int = 2, tracer: Tracer | None = None) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts doit etre superieur ou egal a 1")
        self._reasoner = reasoner
        self._max_attempts = max_attempts
        self._tracer = tracer if tracer is not None else NoOpTracer()

    def decide(self, context: Context) -> Decision:
        last_error: InvalidLLMResponseError | None = None
        for attempt in range(1, self._max_attempts + 1):
            with self._tracer.start_span("reasoner.decide.attempt", attempt=attempt, max_attempts=self._max_attempts):
                try:
                    return self._reasoner.decide(context)
                except InvalidLLMResponseError as exc:
                    last_error = exc
        assert last_error is not None
        raise last_error

"""Point de composition unique : assemble un `Runtime` pret a l'emploi a
partir d'un `LLM` concret et d'une liste de `Tool`, sans que `Runtime` n'ait
jamais a connaitre le fournisseur LLM utilise (Ollama ou un autre).

Ne contient aucune logique metier, uniquement du cablage : construction et
injection des collaborateurs deja existants (`ToolRegistry`,
`ContextBuilder`, `PolicyEngine`, `Executor`, `EventLog`, `LLMReasoner`,
`PromptBuilder`). Le contrat `LLM` (`llm.py`) et `LLMReasoner`
(`reasoner.py`) restent inchanges : `build_runtime` ne fait que les invoquer
tels quels. Remplacer un fournisseur par un autre (ex. `OllamaLLM` par un
futur `OpenAILLM`) ne demande que de passer une autre instance de `LLM`,
jamais de modifier ce module ni `Runtime`.
"""

from peon.context_builder import ContextBuilder
from peon.event_log import EventLog
from peon.executor import Executor
from peon.llm import LLM
from peon.policy import PolicyEngine
from peon.prompts import PromptBuilder
from peon.reasoner import LLMReasoner
from peon.runtime import Runtime
from peon.storage import Storage
from peon.tool_registry import ToolRegistry
from peon.tools.base import Tool


def build_runtime(*, llm: LLM, tools: list[Tool], storage: Storage | None = None) -> Runtime:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)

    return Runtime(
        context_builder=ContextBuilder(registry),
        reasoner=LLMReasoner(llm=llm, prompt_builder=PromptBuilder()),
        policy_engine=PolicyEngine(registry),
        executor=Executor(registry),
        event_log=EventLog(),
        storage=storage,
    )

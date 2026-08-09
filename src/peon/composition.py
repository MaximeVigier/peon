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

`workspace_root` est un pur pass-through vers `PolicyEngine(registry,
workspace_root=...)` (`policy.py`) : `build_runtime` ne resout, ne valide ni
n'interprete jamais ce chemin lui-meme, il se contente de le transmettre.
`None` (valeur par defaut) reproduit exactement le comportement historique -
aucune restriction de chemin, `PathRestrictionRule` absente de la chaine.

Le `Reasoner` injecte dans `Runtime` est toujours un `LLMReasoner` enveloppe
dans un `RetryingReasoner` (`reasoner.py`) : reprise controlee apres erreur
LLM transitoire (HTTP/reseau, JSON invalide, decision non conforme), bornee
et sans backoff. `reasoner_max_attempts` (defaut : `_DEFAULT_REASONER_MAX_ATTEMPTS`,
tres faible) et `tracer` (defaut `None`, transmis tel quel a `Runtime` comme
au `RetryingReasoner` pour que les spans d'une meme execution partagent le
meme Tracer) sont de purs parametres de cablage - aucune logique de retry
n'est ajoutee ici, elle vit entierement dans `RetryingReasoner`.

`event_log` (defaut `None`) reproduit le comportement historique quand il
n'est pas fourni : un `EventLog()` vierge, comme pour une nouvelle Mission.
Le passer explicitement permet d'injecter un EventLog deja peuple depuis
`Storage` (`Runtime.load_event_log(storage)`, deja existant) avant d'appeler
`resume_mission()` sur le `Runtime` obtenu, pour qu'une reprise apres
redemarrage retrouve l'historique complet plutot qu'un EventLog vide - voir
`cli.py::resume`.
"""

from pathlib import Path

from peon.context_builder import ContextBuilder
from peon.event_log import EventLog
from peon.executor import Executor
from peon.llm import LLM
from peon.policy import PolicyEngine
from peon.prompts import PromptBuilder
from peon.reasoner import LLMReasoner, RetryingReasoner
from peon.runtime import Runtime
from peon.storage import Storage
from peon.tool_registry import ToolRegistry
from peon.tools.base import Tool
from peon.tracing import Tracer

_DEFAULT_REASONER_MAX_ATTEMPTS = 2


def build_runtime(
    *,
    llm: LLM,
    tools: list[Tool],
    storage: Storage | None = None,
    workspace_root: Path | str | None = None,
    reasoner_max_attempts: int = _DEFAULT_REASONER_MAX_ATTEMPTS,
    tracer: Tracer | None = None,
    event_log: EventLog | None = None,
) -> Runtime:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)

    reasoner = RetryingReasoner(
        LLMReasoner(llm=llm, prompt_builder=PromptBuilder()),
        max_attempts=reasoner_max_attempts,
        tracer=tracer,
    )

    return Runtime(
        context_builder=ContextBuilder(registry),
        reasoner=reasoner,
        policy_engine=PolicyEngine(registry, workspace_root=workspace_root),
        executor=Executor(registry),
        event_log=event_log if event_log is not None else EventLog(),
        storage=storage,
        tracer=tracer,
    )

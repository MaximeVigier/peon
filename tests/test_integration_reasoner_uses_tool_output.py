"""Test de bout en bout du bug decouvert lors du test reel avec Ollama :

un premier appel a `run_command` produit une donnee precise dans stdout, et
le tour suivant doit pouvoir s'en servir pour prendre une decision correcte.
Avant le correctif de `PromptBuilder._render_observations`, seul le summary
generique ("outil 'run_command' execute avec succes") atteignait le LLM : le
contenu reel de stdout restait invisible.

Ce test n'utilise pas un LLM reel (Ollama) ni le transport HTTP, mais reste
fidele au contrat `LLM`/`Reasoner`/`PromptBuilder` reellement traverse par
`LLMReasoner` : le Reasoner ci-dessous decide en lisant exclusivement le texte
du prompt construit par `PromptBuilder.build()`, exactement ce qu'un vrai LLM
recevrait. C'est le "faux backend LLM" le plus direct pour verifier cette
boucle sans dependre d'un serveur Ollama.
"""

from peon.context_builder import ContextBuilder
from peon.event_log import EventLog
from peon.executor import Executor
from peon.models.context import Context
from peon.models.decision import ActionDecision, Decision, FinishDecision
from peon.models.events import EventType
from peon.models.mission import MissionStatus
from peon.policy import PolicyEngine
from peon.prompts import PromptBuilder
from peon.reasoner import Reasoner
from peon.runtime import Runtime
from peon.tool_registry import ToolRegistry
from peon.tools.shell import ShellTool
from peon.workspace import LocalWorkspace

_SECRET = "PEON_SECRET=42"


class _PromptReadingReasoner(Reasoner):
    # Simule un LLM minimal : ne recoit jamais rien d'autre que le texte de
    # prompt reellement produit par PromptBuilder (le meme texte qu'un LLM
    # recevrait via LLMReasoner), et decide en fonction de ce qu'il y lit.
    def __init__(self, command: str) -> None:
        self._command = command
        self._call_count = 0
        self._prompt_builder = PromptBuilder()

    def decide(self, context: Context) -> Decision:
        self._call_count += 1
        prompt_text = self._prompt_builder.build(context)[1]["content"]

        if self._call_count == 1:
            return ActionDecision(
                reasoning="chercher le secret dans les fichiers de la mission",
                tool_name="run_command",
                arguments={"command": self._command},
            )

        if _SECRET in prompt_text:
            return FinishDecision(outcome="success", summary=f"secret trouve : {_SECRET}", confidence=1.0)
        return FinishDecision(outcome="failure", summary="secret introuvable dans le contexte", confidence=1.0)


def test_second_turn_uses_the_real_output_of_the_first_tool_call() -> None:
    registry = ToolRegistry()
    registry.register(ShellTool(LocalWorkspace()))
    event_log = EventLog()
    runtime = Runtime(
        context_builder=ContextBuilder(registry),
        reasoner=_PromptReadingReasoner(command=f"echo {_SECRET}"),
        policy_engine=PolicyEngine(registry),
        executor=Executor(registry),
        event_log=event_log,
    )

    mission = runtime.run("trouver le secret PEON_SECRET")

    assert mission.status is MissionStatus.SUCCEEDED
    assert mission.iteration_count == 2

    succeeded_events = event_log.list_events_by_type(EventType.MISSION_SUCCEEDED)
    assert len(succeeded_events) == 1
    assert _SECRET in succeeded_events[0].payload["summary"]

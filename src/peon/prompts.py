"""Construction de prompts : `Context` -> messages LLM.

Aucune logique metier, aucun appel LLM, aucune connaissance de Policy/
Executor/Tools concrets : `PromptBuilder` ne lit que les informations deja
presentes dans `Context` (voir `models/context.py`), assemblees par le
Context Builder en amont.

Format des messages produits : liste de deux dicts `{"role": str, "content":
str}`, conforme au contrat fixe par `llm.py` :

- un message `"system"` : instructions fixes, independantes du Context,
  qui decrivent le contrat de sortie JSON attendu (`ActionDecision` /
  `FinishDecision`, voir `models/decision.py`) ;
- un message `"user"` : etat courant de la mission (objectif, statut,
  iteration), outils disponibles et observations recentes, tous tires
  exclusivement du `Context` recu.

`build()` est deterministe : a Context egal, les messages produits sont
identiques (les outils/observations sont rendus dans l'ordre recu, et le
schema JSON de chaque outil est serialise avec des cles triees).
"""

import json
from typing import Any

from peon.models.context import Context
from peon.models.observation import Observation, ObservationKind

# Limite minimale pour eviter qu'un stdout/stderr ou un fichier volumineux ne
# fasse exploser le prompt envoye au LLM. Pas de convention de troncature
# preexistante ailleurs dans le code : valeur choisie arbitrairement, assez
# large pour couvrir un usage normal (extrait de fichier, sortie de commande).
_MAX_OUTPUT_CHARS = 2000

_SYSTEM_PROMPT = """Tu es le Reasoner d'un runtime d'agent IA nomme Peon.

A chaque tour, tu recois l'etat de la mission en cours, les outils \
disponibles et l'historique des observations recentes. Tu dois repondre \
avec un unique objet JSON, sans texte avant ou apres et sans bloc markdown, \
correspondant a l'une de ces deux formes exactement :

1. Demander l'execution d'un outil :
{"kind": "action", "reasoning": "<pourquoi>", "tool_name": "<nom d'outil parmi ceux listes>", "arguments": {"<nom>": "<valeur>"}}

2. Signaler la fin de la mission :
{"kind": "finished", "outcome": "success ou failure", "summary": "<resume>", "confidence": <nombre entre 0.0 et 1.0>}

Tu ne peux utiliser que les outils listes dans le message utilisateur. Tu ne \
planifies jamais plusieurs etapes a l'avance : une seule decision par \
reponse, informee par l'observation la plus recente."""


class PromptBuilder:
    def build(self, context: Context) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": self._render_user_message(context)},
        ]

    @staticmethod
    def _render_user_message(context: Context) -> str:
        lines = [
            f"Objectif de la mission : {context.mission_goal}",
            f"Statut : {context.mission_status.value}",
            f"Iteration : {context.mission_iteration_count}",
            "",
            "Outils disponibles :",
            *PromptBuilder._render_tools(context),
            "",
            "Observations recentes :",
            *PromptBuilder._render_observations(context),
        ]
        return "\n".join(lines)

    @staticmethod
    def _render_tools(context: Context) -> list[str]:
        if not context.available_tools:
            return ["(aucun)"]
        return [
            f"- {tool.name} ({tool.risk_level.value}) : {tool.description} "
            f"| parametres attendus : {json.dumps(tool.parameters_schema, sort_keys=True)}"
            for tool in context.available_tools
        ]

    @staticmethod
    def _render_observations(context: Context) -> list[str]:
        if not context.observations:
            return ["(aucune)"]
        return [PromptBuilder._render_observation(observation) for observation in context.observations]

    @staticmethod
    def _render_observation(observation: Observation) -> str:
        line = f"- [{observation.kind.value}] {observation.summary}"
        rendered_output = PromptBuilder._render_output(observation)
        if rendered_output is not None:
            line += f"\n  resultat : {rendered_output}"
        return line

    @staticmethod
    def _render_output(observation: Observation) -> str | None:
        # Seul EXECUTION_RESULT porte un resultat d'outil reussi exploitable
        # (details["output"], pose par Runtime._execute_action). Les autres
        # kinds (erreur, rejet policy, confirmation refusee) restent inchanges :
        # leur summary est deja le contenu informatif complet.
        if observation.kind is not ObservationKind.EXECUTION_RESULT or observation.details is None:
            return None
        if "output" not in observation.details:
            return None
        output = observation.details["output"]
        if output is None:
            return None
        return PromptBuilder._truncate(PromptBuilder._stringify(output))

    @staticmethod
    def _stringify(output: Any) -> str:
        if isinstance(output, str):
            return output
        return json.dumps(output, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _truncate(text: str) -> str:
        if len(text) <= _MAX_OUTPUT_CHARS:
            return text
        return f"{text[:_MAX_OUTPUT_CHARS]}... [tronque, {len(text)} caracteres au total]"

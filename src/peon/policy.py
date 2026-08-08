"""Point d'entree unique de toute decision de securite : `PolicyEngine.evaluate()`.

`PolicyEngine` ne porte plus lui-meme de logique de regle : il enchaine une
liste ordonnee de `PolicyRule` (guardrails.py). La premiere regle qui rend un
`Verdict` gagne ; une regle qui rend `None` laisse la main a la suivante. Cet
ordre est un invariant de securite, pas un detail d'implementation - voir
ARCHITECTURE.md pour la justification de chaque position.
"""

from pathlib import Path

from peon.guardrails import (
    ArgumentsSchemaRule,
    DangerousCommandRule,
    PathRestrictionRule,
    PolicyRule,
    RiskLevelRule,
    ToolAuthorizationRule,
)
from peon.models.action import Action
from peon.models.verdict import Verdict
from peon.tool_registry import ToolRegistry


class PolicyEngine:
    def __init__(
        self,
        registry: ToolRegistry,
        *,
        rules: list[PolicyRule] | None = None,
        workspace_root: Path | str | None = None,
    ) -> None:
        self._rules = rules if rules is not None else self._default_rules(registry, workspace_root)

    @staticmethod
    def _default_rules(registry: ToolRegistry, workspace_root: Path | str | None) -> list[PolicyRule]:
        # Ordre invariant : autorisation du Tool d'abord (rien d'autre n'a de
        # sens sur un Tool inconnu), puis la regle specifique (commande
        # dangereuse) avant toute regle generique, puis validation
        # structurelle des arguments, puis restriction de chemin (si une
        # racine est configuree), et enfin le risk_level generique en
        # dernier recours - c'est lui qui produit l'ALLOWED par defaut.
        rules: list[PolicyRule] = [
            ToolAuthorizationRule(registry),
            DangerousCommandRule(),
            ArgumentsSchemaRule(registry),
        ]
        if workspace_root is not None:
            rules.append(PathRestrictionRule(registry, Path(workspace_root)))
        rules.append(RiskLevelRule(registry))
        return rules

    def evaluate(self, action: Action) -> Verdict:
        for rule in self._rules:
            verdict = rule.evaluate(action)
            if verdict is not None:
                return verdict
        raise AssertionError(
            f"no PolicyRule produced a Verdict for action '{action.tool_name}' - "
            "a custom `rules=` composition is missing a catch-all rule"
        )

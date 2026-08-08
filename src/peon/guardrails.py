"""Regles composables consommees par `PolicyEngine.evaluate()` (policy.py).

Chaque regle est une fonction pure `Action -> Verdict | None` :

- un `Verdict` signifie que la regle tranche, l'evaluation s'arrete la ;
- `None` signifie qu'elle ne s'applique pas a cette Action, la regle suivante
  de la chaine est consultee.

`PolicyEngine` reste l'unique appelant de ces regles et l'unique point
d'entree de toute decision de securite : aucune des classes ci-dessous n'est
appelee depuis un Tool, Workspace, Executor ou StateMachine.
"""

import re
from pathlib import Path
from typing import Any, Protocol

from peon.models.action import Action
from peon.models.tool_spec import RiskLevel, ToolSpec
from peon.models.verdict import Verdict, VerdictType
from peon.tool_registry import ToolNotFoundError, ToolRegistry


class PolicyRule(Protocol):
    def evaluate(self, action: Action) -> Verdict | None: ...


def _resolve_spec(registry: ToolRegistry, action: Action) -> ToolSpec | None:
    try:
        return registry.get(action.tool_name).spec
    except ToolNotFoundError:
        return None


class ToolAuthorizationRule:
    """Refuse toute Action visant un Tool absent du ToolRegistry.

    `ToolSpec` ne porte aujourd'hui aucun champ d'autorisation distinct de
    son enregistrement dans le `ToolRegistry` (pas de `enabled`, pas de liste
    de roles autorises) : le registre injecte au `PolicyEngine` a la
    construction est la seule "configuration disponible" au sens de cette
    regle. Inventer un second mecanisme ferait doublon avec lui - voir
    CONTEXT.md pour ce point ouvert si une autorisation plus fine (au-dela de
    "connu ou pas") s'avere necessaire plus tard.
    """

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def evaluate(self, action: Action) -> Verdict | None:
        if self._registry.exists(action.tool_name):
            return None
        return Verdict(type=VerdictType.DENIED, reason=f"tool '{action.tool_name}' is not registered")


class DangerousCommandRule:
    """Detecte un motif de commande shell dangereuse (`rm -rf` non cible).

    Motif volontairement minimal et illustratif, repris tel quel de
    l'ancien `PolicyEngine._check_dangerous_command` : ni allowlist ni
    blocklist exhaustive de commandes dans cette phase (voir CONTEXT.md).
    """

    _RM_RF_PATTERN = re.compile(r"^rm -rf (\S+)$")
    _UNSAFE_TARGETS = {".", "..", "/", "~"}
    _UNSAFE_TARGET_PREFIXES = ("/", "~")

    def evaluate(self, action: Action) -> Verdict | None:
        command = action.arguments.get("command")
        if not isinstance(command, str):
            return None

        match = self._RM_RF_PATTERN.match(command.strip())
        if match is None:
            return None

        target = match.group(1)
        if target in self._UNSAFE_TARGETS or target.startswith(self._UNSAFE_TARGET_PREFIXES):
            return Verdict(
                type=VerdictType.DENIED,
                reason=f"'{command}' targets an unscoped or absolute path, no safe rewrite exists",
            )

        safe_command = f"rm -rf -- ./{target}"
        return Verdict(
            type=VerdictType.REWRITE,
            reason=f"'{command}' is an untargeted recursive delete, a scoped alternative is available",
            suggested_action=Action(
                reasoning=f"Policy Engine suggestion: prefer '{safe_command}' over '{command}'.",
                tool_name=action.tool_name,
                arguments={**action.arguments, "command": safe_command},
                risk_level=RiskLevel.LOW,
            ),
        )


_JSON_SCHEMA_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
    "null": (type(None),),
}


class ArgumentsSchemaRule:
    """Valide `Action.arguments` contre `ToolSpec.parameters_schema`.

    Sous-ensemble minimal de JSON Schema : `type: "object"`, `properties`
    (avec `type` par propriete) et `required` - exactement ce que declarent
    les `ToolSpec` existants (`tools/filesystem.py`, `tools/shell.py`).
    Pas de `$ref`, pas de sous-schemas imbriques. `additionalProperties` non
    specifie se comporte comme la semantique JSON Schema par defaut (des
    champs supplementaires non declares restent tolerés) ; seul
    `additionalProperties: false` explicite les rejette.
    """

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def evaluate(self, action: Action) -> Verdict | None:
        spec = _resolve_spec(self._registry, action)
        if spec is None:
            return None  # ToolAuthorizationRule tranche deja ce cas

        violation = self._validate(action.arguments, spec.parameters_schema)
        if violation is None:
            return None

        return Verdict(type=VerdictType.DENIED, reason=f"invalid arguments for tool '{spec.name}': {violation}")

    @staticmethod
    def _validate(arguments: dict[str, Any], schema: dict[str, Any]) -> str | None:
        if schema.get("type") != "object":
            return None  # hors perimetre de ce validateur minimal

        for field in schema.get("required", []):
            if field not in arguments:
                return f"missing required field '{field}'"

        properties: dict[str, Any] = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for key in arguments:
                if key not in properties:
                    return f"unexpected field '{key}'"

        for key, value in arguments.items():
            expected_type = properties.get(key, {}).get("type")
            python_types = _JSON_SCHEMA_TYPES.get(expected_type)
            if python_types is None:
                continue
            if expected_type != "boolean" and isinstance(value, bool):
                return f"field '{key}' must be of type '{expected_type}', got bool"
            if not isinstance(value, python_types):
                return f"field '{key}' must be of type '{expected_type}', got {type(value).__name__}"

        return None


class PathRestrictionRule:
    """Refuse un argument `path` qui echappe a la racine autorisee.

    La racine autorisee est une donnee de configuration du `PolicyEngine`
    lui-meme (voir `policy.py`), jamais lue depuis `Workspace` : `Workspace`
    reste un port d'acces technique, pas un arbitre de securite. S'applique
    uniquement aux Tools dont le `ToolSpec.parameters_schema` declare une
    propriete nommee `path` - determine depuis le schema, jamais depuis une
    liste de noms de Tools codee en dur.
    """

    def __init__(self, registry: ToolRegistry, root: Path) -> None:
        self._registry = registry
        self._root = root.resolve()

    def evaluate(self, action: Action) -> Verdict | None:
        spec = _resolve_spec(self._registry, action)
        if spec is None:
            return None  # ToolAuthorizationRule tranche deja ce cas

        if "path" not in spec.parameters_schema.get("properties", {}):
            return None  # ce Tool ne declare pas de parametre 'path'

        path = action.arguments.get("path")
        if not isinstance(path, str) or not path.strip():
            return None  # ArgumentsSchemaRule couvre deja ce cas

        candidate = Path(path)
        resolved = candidate.resolve() if candidate.is_absolute() else (self._root / candidate).resolve()

        if resolved != self._root and self._root not in resolved.parents:
            return Verdict(
                type=VerdictType.DENIED,
                reason=f"path '{path}' escapes the allowed workspace root '{self._root}'",
            )

        return None


class RiskLevelRule:
    """Regle generique de secours : `HIGH` -> confirmation, sinon `ALLOWED`.

    Doit toujours venir en dernier dans la chaine : c'est elle qui produit le
    verdict par defaut quand aucune regle plus specifique n'a tranche.
    Ignore volontairement `Action.risk_level` et re-derive le risque depuis
    le `ToolRegistry` (comportement inchange depuis avant cette phase - voir
    CONTEXT.md, point ouvert correspondant).
    """

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def evaluate(self, action: Action) -> Verdict | None:
        spec = _resolve_spec(self._registry, action)
        if spec is None:
            return None  # ToolAuthorizationRule tranche deja ce cas

        if spec.risk_level is RiskLevel.HIGH:
            return Verdict(type=VerdictType.REQUIRES_CONFIRMATION, reason=f"tool '{spec.name}' is high risk")

        return Verdict(
            type=VerdictType.ALLOWED,
            reason=f"tool '{spec.name}' is risk level '{spec.risk_level.value}', no blocking rule triggered",
        )

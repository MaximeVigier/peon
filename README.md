# Péon

Un agent orchestré par événements, local et indépendant du modèle utilisé
(aucun fournisseur LLM concret n'est encore branché). Inspiré de Claude Code
sans en être un clone.

## Philosophie

Le LLM n'est pas l'application : c'est uniquement un moteur de décision qui
répond avec une `Decision` structurée (JSON) : soit une action (`tool_name` +
`arguments`), soit une fin de mission (`outcome` + `summary`). Toute la
logique métier — état, outils, permissions, contrôle, historique — appartient
au runtime Python. Le LLM ne lit, n'écrit et n'exécute jamais rien lui-même ;
il ne fait que demander l'usage d'un outil, et le runtime valide
systématiquement sa réponse avant d'agir. Voir
[`ARCHITECTURE.md`](ARCHITECTURE.md) pour le détail de chaque composant et
[`CONTEXT.md`](CONTEXT.md) pour l'état d'avancement complet.

## Composants

- **Runtime** — seul composant impur (orchestrateur) : appelle `ContextBuilder`,
  `Reasoner`, `PolicyEngine`, `Executor`, écrit dans l'`EventLog` et,
  optionnellement, persiste vers `Storage`.
- **State Machine** — fonction pure `(état, événement) -> état`.
- **Policy Engine** — fonction pure `Action -> Verdict`, détection minimale de
  commandes dangereuses, déclenchement de confirmation.
- **Executor** — exécute une `Action` déjà validée via le `Tool` résolu par le
  `ToolRegistry`.
- **Tools** — `read_file`, `list_directory` (risque `LOW`), `run_command`
  (risque `MEDIUM`).
- **EventLog** — journal append-only en mémoire ; source du `Context`
  reconstruit pour le Reasoner.
- **Context reconstruction** — `ContextBuilder` reconstruit un `Context` soit
  à partir d'observations déjà en main, soit directement depuis un `EventLog`.
- **Storage abstraction** — interface `save_events`/`load_events` ;
  implémentation en mémoire fournie (`InMemoryStorage`).

## Lancer en local

```bash
python -m venv .venv
.venv\Scripts\activate       # Windows
pip install -e ".[dev]"

peon --version
python -m pytest
```

## Structure

```
src/peon/
  cli.py               # point d'entree Typer (peon --version)
  runtime.py            # orchestrateur : run(), resume_confirmation(),
                         # persist_events(), load_event_log()
  state_machine.py       # transition pure (etat, evenement) -> etat
  context_builder.py     # Context depuis observations ou depuis un EventLog
  reasoner.py             # ABC Reasoner + LLMReasoner (Context -> Decision)
  llm.py                  # abstraction fournisseur LLM (aucun client concret)
  prompts.py              # Context -> messages LLM (PromptBuilder)
  policy.py               # Action (+ ToolRegistry) -> Verdict
  executor.py             # Action validee -> ToolResult
  tool_registry.py         # registre des Tools disponibles
  event_log.py             # journal append-only en memoire
  storage.py                # abstraction Storage + InMemoryStorage
  tools/
    base.py                 # contrat Tool (ABC)
    filesystem.py             # ReadFileTool (read_file), ListDirectoryTool (list_directory)
    shell.py                   # ShellTool (run_command)
  models/                       # schemas Pydantic partages entre composants
tests/                           # miroir de src/peon/ (+ tests d'integration bout-en-bout)
```

## Fonctionnalités terminées

- Cycle complet `Mission → Context → Reasoner → Decision → Policy Engine →
  Executor → Tool → ToolResult → Observation → EventLog`, testé de bout en
  bout sans mock interne.
- Boucle de confirmation humaine complète : une action à risque `HIGH`
  déclenche une `ConfirmationRequest` ; `Runtime.resume_confirmation()`
  consomme une `ConfirmationResponse` externe et reprend le cycle (exécution
  si acceptée, retour au raisonnement avec une `Observation` si refusée).
- `Context` reconstructible directement depuis un `EventLog`
  (`ContextBuilder.build_from_event_log`), pas seulement depuis une liste
  d'observations tenue en mémoire par le Runtime.
- Persistance minimale : `Runtime.persist_events()` sauvegarde l'`EventLog`
  courant vers un `Storage` injecté ; `Runtime.load_event_log()` reconstruit
  un `EventLog` à partir d'un `Storage`.
- Trois Tools concrets : `read_file`, `list_directory` (lecture seule, `LOW`),
  `run_command` (exécution shell arbitraire, `MEDIUM` — la sécurité reste
  entièrement au Policy Engine, jamais filtrée par le Tool lui-même).
- 263 tests passants (unitaires + intégration bout en bout).

## Limitations actuelles

- **Aucun fournisseur LLM concret** branché derrière l'abstraction `llm.py`
  (`LLMReasoner`/`PromptBuilder` sont prêts, mais un Reasoner déterministe
  scripté est utilisé partout dans les tests).
- **`Storage` n'a qu'une implémentation en mémoire** (`InMemoryStorage`) : une
  mission ne survit pas encore réellement à un arrêt du process. Un backend
  disque (SQLite ou autre) reste une extension future derrière la même
  abstraction.
- **Pas de reprise complète de mission** : recharger un `EventLog` reconstruit
  l'historique des événements, mais ne restaure ni la `Mission` ni la
  `StateMachine` ni une `ConfirmationRequest` en attente.
- **CLI minimale** : seulement `peon --version` ; aucune commande d'exécution
  de mission.
- **Détection de commandes dangereuses volontairement minimale** dans le
  Policy Engine (un seul motif, `rm -rf <cible>` non ciblé) — illustratif, pas
  un moteur de règles complet.
- Pas de Critic (système de hooks) ni de Budget Manager : points d'extension
  identifiés dans `ARCHITECTURE.md`, non implémentés.
- Pas d'outils `git`/`search`.

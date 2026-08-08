# Péon

[![Tests](https://github.com/MaximeVigier/peon/actions/workflows/tests.yml/badge.svg)](https://github.com/MaximeVigier/peon/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](pyproject.toml)

Un agent orchestré par événements, local et indépendant du modèle utilisé
(`OllamaLLM` est le premier fournisseur concret branché derrière l'abstraction
`llm.py`, d'autres restent envisageables sans changement d'architecture).
Inspiré de Claude Code sans en être un clone.

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
  optionnellement, persiste vers `Storage` (événements et checkpoints).
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
- **Tracer** — port d'observabilité technique optionnel (`tracer=None` par
  défaut) : spans de durée autour de `run()`, `resume_confirmation()`, l'appel
  au Reasoner et l'appel à l'Executor. Séparé du vocabulaire métier, aucun
  couplage à l'`EventLog`.
- **CLI** — `peon run "<goal>"` / `peon resume` (Typer). Pilote uniquement
  l'API publique du Runtime, ne réimplémente jamais la boucle ReAct.

## Lancer en local

```bash
python -m venv .venv
.venv\Scripts\activate       # Windows
pip install -e ".[dev]"

peon --version
python -m pytest

# necessite un serveur Ollama local (http://localhost:11434) avec le modele
# choisi deja disponible (`ollama pull <model>`) :
peon run "lister les fichiers du dossier courant" --model llama3.1
peon resume   # reprend un Checkpoint sauvegarde plus tot DANS LE MEME PROCESS
```

## Structure

```
src/peon/
  cli.py               # Typer : peon --version, peon run, peon resume (Phase 5)
  composition.py         # build_runtime() : assemble un Runtime pret a l'emploi
                          # a partir d'un LLM concret et d'une liste de Tool
  runtime.py            # orchestrateur : run(), resume_confirmation(),
                         # persist_events(), load_event_log(),
                         # save_checkpoint(), resume_mission()
  state_machine.py       # transition pure (etat, evenement) -> etat
  context_builder.py     # Context depuis observations ou depuis un EventLog
  reasoner.py             # ABC Reasoner + LLMReasoner (Context -> Decision)
  llm.py                  # abstraction fournisseur LLM (ABC)
  providers/
    ollama.py              # OllamaLLM : premier fournisseur concret (API chat Ollama)
  prompts.py              # Context -> messages LLM (PromptBuilder)
  policy.py               # PolicyEngine.evaluate() : compose la chaine de guardrails.py
  guardrails.py           # PolicyRule (Protocol) + regles composables (Phase 3)
  executor.py             # Action validee -> ToolResult
  tool_registry.py         # registre des Tools disponibles
  event_log.py             # journal append-only en memoire
  storage.py                # abstraction Storage + InMemoryStorage
                             # (evenements + checkpoints)
  tracing.py                 # Tracer/Span (ABC) + NoOpTracer : port d'observabilite optionnel
                              # (Runtime -> Tracer, separe de l'EventLog)
  tools/
    base.py                 # contrat Tool (ABC)
    filesystem.py             # ReadFileTool (read_file), ListDirectoryTool (list_directory)
    shell.py                   # ShellTool (run_command)
  workspace.py                # Workspace (ABC) + LocalWorkspace : port technique
                               # filesystem/subprocess injecte dans les Tools ci-dessus
  models/                       # schemas Pydantic partages entre composants
                                 # (dont checkpoint.py : Mission + ConfirmationRequest)
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
- Checkpoint / reprise durable (Phase 1) : `Runtime.save_checkpoint(mission)`
  sauvegarde un `Checkpoint` (`Mission` + `ConfirmationRequest` en attente)
  vers `Storage` ; un **nouveau** `Runtime`, après arrêt/crash du process,
  peut recharger ce `Checkpoint` et appeler `resume_mission()` pour restaurer
  l'état nécessaire à `resume_confirmation()` — cas cible : une mission
  suspendue en `AWAITING_CONFIRMATION` reprend normalement après redémarrage.
- `OllamaLLM` (`providers/ollama.py`) : premier fournisseur `LLM` concret,
  appelle l'API chat d'Ollama en HTTP ; assemblé avec le reste du pipeline via
  `composition.py` (`build_runtime()`).
- Trois Tools concrets : `read_file`, `list_directory` (lecture seule, `LOW`),
  `run_command` (exécution shell arbitraire, `MEDIUM` — la sécurité reste
  entièrement au Policy Engine, jamais filtrée par le Tool lui-même). Chacun
  délègue l'accès technique au filesystem/`subprocess` à un `Workspace`
  injecté (`LocalWorkspace` aujourd'hui).
- Policy Engine composable (Phase 3) : `PolicyEngine.evaluate()` enchaîne une
  liste ordonnée de règles (`guardrails.py`) — autorisation par Tool,
  détection de commande dangereuse, validation des arguments contre le
  schéma du Tool, restriction de chemin optionnelle, `risk_level` générique
  en dernier recours. Toujours la seule autorité de sécurité : ni les Tools,
  ni `Workspace`, ni l'Executor ne décident jamais si une Action est permise.
- Tracer optionnel (`tracing.py`) : `Runtime(tracer=...)` mesure la durée de
  `run()`, `resume_confirmation()`, de l'appel LLM (`reasoner.decide`) et de
  l'exécution d'un Tool (`executor.run`) via des spans techniques ; `NoOpTracer`
  par défaut, comportement strictement inchangé sans tracer, aucun couplage à
  l'`EventLog`.
- CLI minimale réellement utilisable (Phase 5) : `peon run "<goal>"` lance une
  mission jusqu'à son état final en gérant les confirmations en direct
  (affichage `Tool`/`Arguments`/`Reason`, réponse `y`/`N`, vrai
  `ConfirmationResponse`) ; `peon resume` s'appuie sur le vrai mécanisme de
  `Checkpoint` de la Phase 1. Construite exclusivement via `build_runtime()`,
  ne réimplémente jamais la boucle ReAct.
- 344 tests passants (unitaires + intégration bout en bout).

## Limitations actuelles

- **Un seul fournisseur LLM concret** (`OllamaLLM`) derrière l'abstraction
  `llm.py` ; d'autres (OpenAI, Anthropic, Gemini...) restent à écrire derrière
  la même interface. Un Reasoner déterministe scripté reste utilisé dans la
  majorité des tests.
- **`Storage` n'a qu'une implémentation en mémoire** (`InMemoryStorage`) : une
  mission ne survit pas encore à un arrêt du process **tant qu'aucun backend
  disque n'est branché** — l'abstraction et le mécanisme de reprise sont en
  place (voir ci-dessus), mais `InMemoryStorage` ne persiste que pour la durée
  du process. Un backend disque (SQLite ou autre) reste une extension future
  derrière la même abstraction `Storage`. **Conséquence concrète sur `peon
  resume`** : la CLI garde un `InMemoryStorage` unique en mémoire du process
  (partagé entre `run` et `resume`), donc `peon resume` ne retrouve un
  Checkpoint que s'il a été sauvegardé **dans le même process** — pas après un
  vrai redémarrage de `peon` depuis un terminal (chaque invocation est un
  nouveau process). Aucune persistance disque n'a été ajoutée pour masquer
  cette limite.
- **Reprise de mission encore partielle** : le `Checkpoint` restaure la
  `Mission` et une éventuelle `ConfirmationRequest` en attente (le cas
  `AWAITING_CONFIRMATION`), mais pas l'historique complet de l'`EventLog` ni
  les `Observation` passées — un nouveau `Runtime` reprend avec un `EventLog`
  vierge, pas un replay complet. Pas de multi-mission : un `Runtime` ne
  retient qu'un `Checkpoint`/une confirmation en attente à la fois.
- **CLI minimale** : `peon --version`, `peon run "<goal>"`, `peon resume` —
  pas de sous-commandes de configuration, pas d'option `--workspace-root`
  (donc `PathRestrictionRule` reste inutilisée en pratique), pas de gestion
  dédiée des erreurs réseau Ollama (elles remontent brutes).
- **Le chemin de confirmation de la CLI n'est pas encore déclenché en usage
  réel** : les trois `Tool` livrés (`read_file`, `list_directory` en `LOW`,
  `run_command` en `MEDIUM`) ne sont jamais `RiskLevel.HIGH`, seul niveau qui
  déclenche `REQUIRES_CONFIRMATION`. Le code de confirmation de `peon run`/
  `peon resume` est implémenté et testé (via des `Tool` de test `HIGH`), prêt
  à s'activer sans changement dès qu'un `Tool` `HIGH` réel (ex. futur
  `tools/git.py`) sera enregistré.
- **Détection de commandes dangereuses volontairement minimale** : le Policy
  Engine est un moteur de règles composables (voir ci-dessus), mais la règle
  de détection elle-même ne reconnaît toujours qu'un seul motif (`rm -rf
  <cible>` non ciblé) — illustrative, pas une allowlist/blocklist exhaustive.
- Pas de Critic (système de hooks) ni de Budget Manager : points d'extension
  identifiés dans `ARCHITECTURE.md`, non implémentés.
- Pas d'outils `git`/`search`.
- **Tracer sans adaptateur concret** : seul `NoOpTracer` existe ; pas de
  branchement OpenTelemetry, pas de métriques de tokens (aucune source fiable
  n'existe dans `LLM`/`Reasoner` aujourd'hui).

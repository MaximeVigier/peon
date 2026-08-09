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
  (risque `MEDIUM`), `delete_file` (risque `HIGH`).
- **EventLog** — journal append-only en mémoire ; source du `Context`
  reconstruit pour le Reasoner.
- **Context reconstruction** — `ContextBuilder` reconstruit un `Context` soit
  à partir d'observations déjà en main, soit directement depuis un `EventLog`.
- **Storage abstraction** — interface `save_events`/`load_events` +
  `save_checkpoint`/`load_checkpoint` ; `InMemoryStorage` (événements et
  checkpoints, tout en mémoire) et `FileStorage` (checkpoint en JSON +
  événements en JSON Lines, tous deux persistés sur disque) fournies.
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
peon resume   # reprend le dernier Checkpoint sauvegarde sur disque
              # (~/.peon/checkpoint.json), meme apres un redemarrage complet

# --workspace-root restreint les arguments 'path' des Tools a cette racine
# (PathRestrictionRule) ; a refournir sur `resume` pour rester actif sur la
# suite du raisonnement repris (non persiste dans le Checkpoint) :
peon run "lire les fichiers du projet" --workspace-root .
peon resume --workspace-root .
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
  storage.py                # abstraction Storage + InMemoryStorage (evenements
                             # + checkpoints, memoire) + FileStorage (checkpoint
                             # JSON + evenements JSON Lines, tous deux sur disque)
  tracing.py                 # Tracer/Span (ABC) + NoOpTracer : port d'observabilite optionnel
                              # (Runtime -> Tracer, separe de l'EventLog)
  tools/
    base.py                 # contrat Tool (ABC)
    filesystem.py             # ReadFileTool (read_file, LOW), ListDirectoryTool (list_directory, LOW),
                               # DeleteFileTool (delete_file, HIGH)
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
- Persistance disque du Checkpoint : `FileStorage` (`storage.py`) persiste le
  dernier `Checkpoint` en JSON sur disque (`~/.peon/checkpoint.json` par
  défaut côté CLI, voir ci-dessous), écriture atomique (fichier temporaire +
  remplacement), fichier absent → `None`, JSON invalide/incompatible →
  `CorruptedCheckpointError` explicite plutôt qu'une corruption silencieuse.
  Mono-mission comme `InMemoryStorage` : chaque sauvegarde remplace la
  précédente. `peon resume` (Phase 5) utilise désormais ce `FileStorage` par
  défaut : un Checkpoint survit à un vrai redémarrage du process `peon`, pas
  seulement à l'intérieur d'un même process.
- Reprise durable avec historique (chantier ultérieur) : `FileStorage`
  persiste aussi désormais l'`EventLog` (fichier JSON Lines séparé, nommé
  d'après le chemin du Checkpoint, réécrit atomiquement à chaque
  `save_events()`), `CorruptedEventLogError` explicite en cas de contenu
  corrompu, et `Runtime.persist_events()` ne renvoie plus que le delta depuis
  le dernier appel (évite la duplication dans `Storage` en cas d'appels
  répétés). `Runtime.resume_mission()` reconstruit `self._observations`
  depuis l'`EventLog` du Runtime (via `ContextBuilder.build_from_event_log`) :
  si cet `EventLog` a été chargé depuis `Storage` au préalable
  (`Runtime.load_event_log()`, câblé dans `peon resume`), le Context reconstruit
  après reprise est identique à celui qu'aurait eu le process d'origine au
  même point.
- `OllamaLLM` (`providers/ollama.py`) : premier fournisseur `LLM` concret,
  appelle l'API chat d'Ollama en HTTP ; assemblé avec le reste du pipeline via
  `composition.py` (`build_runtime()`).
- Reprise contrôlée après erreur LLM (`reasoner.py`) : toute panne du
  `Reasoner` (réseau/HTTP, JSON invalide, décision non conforme au schéma)
  converge vers `InvalidLLMResponseError` ; `RetryingReasoner` (décorateur de
  `Reasoner`, jamais spécifique à Ollama) retente `decide()` un nombre borné
  de fois (`reasoner_max_attempts`, défaut faible, aucune boucle infinie,
  aucune Action/Tool rejouée) avant de laisser la Mission échouer
  proprement. `build_runtime()` enveloppe toujours `LLMReasoner` dans un
  `RetryingReasoner` ; `Runtime` n'importe jamais `providers.ollama` et ne
  connaît que le contrat `InvalidLLMResponseError`.
- Quatre Tools concrets : `read_file`, `list_directory` (lecture seule,
  `LOW`), `run_command` (exécution shell arbitraire, `MEDIUM`), `delete_file`
  (suppression d'un fichier, `HIGH` — premier Tool de production dont le
  risque déclenche réellement `REQUIRES_CONFIRMATION`, voir ci-dessous). La
  sécurité reste entièrement au Policy Engine, jamais filtrée par le Tool
  lui-même. Chacun délègue l'accès technique au filesystem/`subprocess` à un
  `Workspace` injecté (`LocalWorkspace` aujourd'hui).
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
- Restriction de chemin réellement activable : `peon run "<goal>"
  --workspace-root <path>` (idem `peon resume`) branche `PathRestrictionRule`
  via `build_runtime(..., workspace_root=...)` — tout argument `path` en
  dehors de cette racine est refusé par le Policy Engine. Opt-in : sans
  l'option, comportement historique inchangé (aucune restriction).
- 441 tests passants (unitaires + intégration bout en bout).

## Limitations actuelles

- **Un seul fournisseur LLM concret** (`OllamaLLM`) derrière l'abstraction
  `llm.py` ; d'autres (OpenAI, Anthropic, Gemini...) restent à écrire derrière
  la même interface. Un Reasoner déterministe scripté reste utilisé dans la
  majorité des tests.
- **Le Checkpoint et l'EventLog sont tous les deux persistés sur disque** :
  `FileStorage` (utilisé par défaut par `cli.py`, `~/.peon/checkpoint.json` +
  un fichier `.events.jsonl` associé) persiste le dernier `Checkpoint` et
  l'historique complet des événements. `peon resume` retrouve donc un
  Checkpoint *et* les `Observation` produites avant l'arrêt après un vrai
  redémarrage du process `peon` (voir la limite suivante pour ce que ça
  restaure concrètement). `InMemoryStorage` reste disponible séparément
  (tests, usages qui ne veulent aucune I/O disque) ; un backend disque plus
  robuste (SQLite ou autre, transactionnel) reste une extension future
  possible, mais n'est plus un prérequis pour la reprise.
- **Reprise de mission** : le `Checkpoint` restaure la `Mission` et une
  éventuelle `ConfirmationRequest` en attente (le cas
  `AWAITING_CONFIRMATION`) ; l'historique des `Observation` passées est
  restauré si l'appelant a chargé l'`EventLog` depuis `Storage` avant
  d'appeler `resume_mission()` (`Runtime.load_event_log(storage)`, déjà fait
  par `peon resume`) — un nouveau `Runtime` reconstruit alors un `Context`
  identique à celui qu'aurait eu le process d'origine au même point, sans
  replay artificiel : `ContextBuilder.build_from_event_log()` est la même
  méthode que la boucle de raisonnement utilise déjà à chaque tour. Pas de
  multi-mission : un `Runtime` ne retient qu'un `Checkpoint`/une confirmation
  en attente à la fois.
- **CLI minimale** : `peon --version`, `peon run "<goal>"`, `peon resume` —
  pas de sous-commandes de configuration. Une erreur réseau/HTTP Ollama ne
  remonte plus brute : `LLMReasoner` la traduit en `InvalidLLMResponseError`,
  `RetryingReasoner` retente un nombre borné de fois
  (`reasoner_max_attempts`), puis la Mission échoue proprement (`FAILED`,
  `MISSION_FAILED` persisté dans l'`EventLog` avec le message d'erreur) — la
  CLI affiche `Mission failed after N iteration(s).`, sans détailler encore
  la raison précise dans ce message (elle reste consultable dans
  l'`EventLog` persisté). Une erreur de `Storage` (`Checkpoint`/`EventLog`
  corrompu sur disque) produit un message dédié côté `peon resume`
  (`Checkpoint file is corrupted.` / `Event log file is corrupted.`) plutôt
  qu'une trace Python brute. `--workspace-root` existe sur les deux
  commandes mais n'est pas persistée dans le `Checkpoint` : `peon resume`
  doit la refournir pour qu'elle s'applique aux Actions de la boucle de
  raisonnement reprise.
- **Le chemin de confirmation de la CLI est désormais déclenché en usage
  réel** : `delete_file` (`RiskLevel.HIGH`) est enregistré par
  `_build_tools()` — `peon run "<goal>"`/`peon resume` demandent une vraie
  confirmation (`Tool`/`Arguments`/`Reason`, `y`/`N`) dès qu'une mission
  demande la suppression d'un fichier, sans changement au code de
  confirmation lui-même (déjà implémenté et testé via des `Tool` de test
  `HIGH` depuis la Phase 5). `delete_file` reste borné à un seul fichier
  (jamais un dossier) ; d'autres opérations destructrices (`git push`,
  suppression massive) restent hors périmètre tant que `tools/git.py` n'est
  pas écrit.
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

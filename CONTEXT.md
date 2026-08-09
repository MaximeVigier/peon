# Péon — Contexte de reprise

Ce document résume tout ce qui a été validé pour le projet Péon, afin qu'une
nouvelle session Claude Code puisse reprendre le travail sans relire tout
l'historique de conversation. Il ne contient que des décisions déjà prises —
aucune proposition nouvelle.

Voir aussi [`ARCHITECTURE.md`](ARCHITECTURE.md) (référence détaillée : diagrammes
Mermaid, table de transitions complète, vocabulaire d'événements) et
[`README.md`](README.md) (installation, lancement local, statut par phase —
réaligné sur l'état réel dans cette même mise à jour, voir section 6).

## 1. Vision de Péon

Péon est un agent orchestré par événements, local, inspiré de Claude Code sans en être un
clone, conçu dès le départ pour être modulaire, extensible et indépendant du
modèle utilisé (Ollama aujourd'hui, d'autres fournisseurs — OpenAI, Anthropic,
Gemini, OpenRouter — envisageables plus tard sans changement d'architecture).

Philosophie fondatrice : **le LLM n'est pas l'application**. Il n'est qu'un
moteur de décision. Toute la logique métier — état, outils, mémoire,
permissions, contrôle — appartient au runtime Python, qui ne fait jamais
confiance au LLM et valide systématiquement tout ce qu'il produit. Le LLM ne
lit, n'écrit et n'exécute jamais rien lui-même ; il ne fait que demander
l'usage d'un outil ou signaler la fin d'une mission.

Objectif du MVP (document de vision initial, affiné depuis par l'architecture
event-driven décrite en section 2) : recevoir une mission utilisateur, agir
via des outils, modifier un projet, lancer des commandes, analyser les
résultats, corriger automatiquement les erreurs, s'arrêter en succès, échec ou
limite d'itérations atteinte. Sécurité : toute action dangereuse (`rm -rf`,
`git push`, suppression massive, commandes destructrices) doit être bloquée ou
nécessiter une confirmation utilisateur explicite — jamais exécutée
silencieusement.

Stack : Python 3.12+, Typer, Pydantic v2, Rich, SQLite, Ollama, pytest.
Architecture fortement typée, code clair, peu de dépendances.

**Méthode de travail (règle non négociable, à respecter dans toute session
future) :** développement par petites étapes. Pour chaque étape : expliquer
les choix d'architecture, implémenter uniquement cette étape, ajouter les
tests associés, attendre validation avant de continuer. Ne jamais générer
plusieurs composants d'un coup ni anticiper une phase non demandée.

## 2. Principes d'architecture validés

- **Pipeline** : Mission → Context Builder → Reasoner (LLM) → Decision →
  State Machine (consulte Policy Engine via un Verdict déjà calculé, ne
  l'appelle jamais elle-même) → Executor → Tool → ToolResult → Observation →
  Event Log → nouveau cycle (retour au Context Builder).
- **Séparation stricte pur/impur** : le **Runtime** est le seul composant
  impur (I/O réel — LLM, disque, process). La **State Machine** et le
  **Policy Engine** sont des fonctions pures, testables sans mock d'I/O.
- **Le LLM ne pilote jamais le runtime.** Il produit uniquement une `Decision`
  (donnée), jamais un ordre. Toute Decision doit franchir une transition
  validée par la State Machine avant tout effet de bord.
- **Boucle réactive, type ReAct** : une Decision à la fois, informée par
  l'Observation la plus récente — pas de plan global amont obligatoire (écart
  assumé par rapport à la mention "créer un plan" du document de vision
  initial, tranché explicitement lors de la conception).
- **État explicite**, tenu par la State Machine, + Event Log comme journal
  d'audit séparé — pas d'event-sourcing complet (état dérivé par fold des
  événements) pour le MVP.
- **Une responsabilité unique par composant**, communication uniquement via
  contrats d'entrée/sortie typés (Pydantic). Pas d'appel direct entre pairs en
  dehors de l'orchestration du Runtime (seule exception : State Machine →
  Policy Engine, une consultation pure-à-pure sans I/O).
- **Extensibilité posée sans être construite** : Critic (système de hooks) et
  Budget Manager (agrégateur de budget) sont des points d'extension
  identifiés, explicitement non implémentés dans le MVP.

## 3. Responsabilités de chaque composant

| Composant | Rôle | Statut |
|---|---|---|
| **Mission** | Objectif utilisateur, statut de cycle de vie, compteur d'itérations, limite max. Donnée pure. | ✅ implémenté (`models/mission.py`) |
| **Checkpoint** | Instantané composable d'une `Mission` et d'une éventuelle `ConfirmationRequest` en attente, suffisant pour reconstruire l'état nécessaire à `resume_confirmation()` après arrêt/crash du process. Ne porte ni l'`EventLog` ni les `Observation`. | ✅ implémenté (`models/checkpoint.py`, Phase 1 — checkpoint/reprise) |
| **Context** | Donnée immuable remise au Reasoner : objectif, statut, itération, observations, outils disponibles. | ✅ implémenté (`models/context.py`) |
| **Context Builder** | Seul point de passage entre Event Log, Tool Registry et Reasoner : sélectionne les événements pertinents, décrit les outils disponibles. Ne raisonne jamais, n'assemble aucun prompt (voir PromptBuilder). Deux points d'entrée : `build(observations=...)` et `build_from_event_log(event_log=...)` (reconstruit les `Observation` depuis les événements `OBSERVATION_PRODUCED`, utilisé par le Runtime). | ✅ implémenté (`context_builder.py`) |
| **Reasoner** (ex-Planner) | Reçoit un `Context`, appelle le LLM, retourne une `Decision`. Ne construit jamais son propre contexte, ne devine jamais les outils disponibles. | ✅ implémenté : ABC (`reasoner.py`) + implémentation concrète `LLMReasoner` |
| **LLM** | Abstraction pure d'un fournisseur de modèle : `generate(messages) -> str`. | ✅ contrat implémenté (`llm.py`) — **`OllamaLLM` branché** (`providers/ollama.py`, premier fournisseur concret) ; OpenAI/Anthropic/... restent à écrire derrière la même abstraction |
| **PromptBuilder** | Transforme un `Context` en messages LLM (`Context -> list[dict[str, str]]`), déterministe, sans logique métier. | ✅ implémenté (`prompts.py`) |
| **State Machine** | Autorité unique de transition, fonction pure `(état, événement) -> état`. Ne consulte jamais elle-même le Policy Engine — le Verdict lui est fourni comme donnée de l'événement. | ✅ implémenté (`state_machine.py`) |
| **Policy Engine** | Fonction pure `(Action) -> Verdict`. Consulte le Tool Registry (métadonnées uniquement). Depuis la Phase 3, compose une chaîne ordonnée de `PolicyRule` (`guardrails.py`) : autorisation par Tool, détection de commandes dangereuses, validation d'arguments contre `ToolSpec.parameters_schema`, restriction de chemin optionnelle, `risk_level` générique en dernier recours. | ✅ implémenté (`policy.py` + `guardrails.py`, Phase 3) |
| **Executor** | Exécute une Action déjà validée via le Tool résolu par le Tool Registry ; convertit les échecs en `ExecutionError`. Ne parle jamais au LLM ni au Policy Engine. | ✅ implémenté (`executor.py`) |
| **Tool Registry** | Source de vérité unique sur les outils disponibles (instances `Tool` exécutables, pas seulement leur description). | ✅ implémenté (`tool_registry.py`) |
| **Tool** | Contrat d'une capacité atomique : `spec` (ToolSpec) + `execute(arguments) -> ToolResult`. | ✅ contrat implémenté (`tools/base.py`) — implémentations concrètes : `ReadFileTool` (`read_file`, `LOW`), `ListDirectoryTool` (`list_directory`, `LOW`), `DeleteFileTool` (`delete_file`, `HIGH`, chantier « Tool HIGH — delete_file »), toutes trois dans `tools/filesystem.py` ; `ShellTool` (`run_command`, `MEDIUM`), dans `tools/shell.py` ; les quatre injectées avec un `Workspace` depuis la Phase 2 (délèguent l'I/O, ne touchent plus `pathlib`/`subprocess` directement) ; **reste à faire** `git.py`/`search.py` |
| **Workspace** | Port technique entre les Tools et le filesystem/processus réels : `read_file`, `list_directory`, `run_command`, `delete_file`. Interface réduite aux seules opérations dont les Tools actuels ont besoin. | ✅ implémenté (`workspace.py`, Phase 2 ; `delete_file` ajouté par le chantier « Tool HIGH — delete_file ») — une seule implémentation concrète, `LocalWorkspace` (aucun sandboxing, aucune restriction de chemin) |
| **Observation** | Modèle plat (`kind` + `summary` + `details`), sans dépendance vers ToolResult/ExecutionError/Verdict ni aucun composant — la traduction elle-même reste une responsabilité du Runtime. Le Reasoner ne voit jamais un ToolResult brut. | ✅ implémenté (`models/observation.py`), produite en conditions réelles par le `Runtime` |
| **Event Log** | Journal append-only en mémoire pendant l'exécution (`append`, `list_events`, `list_events_by_type`), zéro dépendance vers Storage. | ✅ implémenté (`event_log.py`) |
| **Storage** | Abstraction `save_events`/`load_events` + `save_checkpoint`/`load_checkpoint` (ABC), zéro dépendance vers Event Log ni logique métier au-delà de `Event`/`Checkpoint`. | ✅ abstraction + `InMemoryStorage` + `FileStorage` implémentées (`storage.py`) — `FileStorage` persiste le `Checkpoint` sur disque (JSON) ; les événements restent en mémoire pour les deux implémentations, **aucun backend disque pour l'`EventLog`** pour l'instant |
| **Runtime** | Seul composant impur : orchestre tous les appels (Context Builder, Reasoner, Policy Engine, Executor), écrit dans l'Event Log, consomme un `ConfirmationResponse` (`resume_confirmation`). | ✅ implémenté (`runtime.py`) — persistance vers `Storage` optionnelle et explicite (`persist_events()`/`load_event_log()`, `save_checkpoint()`/`resume_mission()`), jamais automatique |
| **Critic** *(extension, hors MVP)* | Système de hooks (`BeforeToolExecution`, `AfterToolExecution`, `BeforeMissionCompletion`, `BeforeCommit`), non bloquant par défaut, jamais une dépendance obligatoire. | ❌ non implémenté, non prévu au MVP |
| **Budget Manager** *(extension, hors MVP)* | Centraliserait itérations/tokens/temps/coût, consulté par State Machine et Context Builder. | ❌ non implémenté, non prévu au MVP |

Modèles de support déjà implémentés et désormais produits/consommés en
conditions réelles par le pipeline complet (`Runtime` + `Reasoner` +
`Policy Engine` + `Executor` + `Tool`), confirmé par les tests d'intégration
bout-en-bout (`tests/test_integration_read_file.py`,
`tests/test_integration_list_directory.py`) : `Decision`
(`ActionDecision`/`FinishDecision`), `Action`, `ToolSpec`, `ToolResult`,
`ExecutionError`, `Verdict`, `Observation`. `Event` (`models/events.py`) est
consommé par l'`EventLog` depuis le début. `ConfirmationRequest` est émis par
le Runtime (`_request_confirmation`) et `ConfirmationResponse` est désormais
consommée par `Runtime.resume_confirmation(mission, response)`, qui reprend le
cycle en attente (exécution si acceptée, retour au raisonnement si refusée) —
cf. section 4 pour la décision et section 5 pour les limites encore ouvertes
de ce mécanisme. `Checkpoint` (`models/checkpoint.py`) compose une `Mission`
et une éventuelle `ConfirmationRequest` en attente ; produit par
`Runtime.save_checkpoint(mission)`, consommé par `Runtime.resume_mission(checkpoint)`
sur un nouveau `Runtime` pour restaurer l'état nécessaire à
`resume_confirmation()` après un arrêt/crash du process.

## 4. Décisions déjà prises

**Architecture (issues de la conception, voir `ARCHITECTURE.md`) :**
- State Machine = autorité unique de transition, fonction pure.
- Runtime = unique composant impur / orchestrateur.
- Event Log append-only, en mémoire, sans dépendance vers Storage ; Storage =
  persistance SQLite séparée, alimentée par le Runtime en parallèle de l'Event
  Log — pas un troisième mécanisme d'historique.
- État explicite (pas d'event-sourcing complet pour le MVP).
- Boucle réactive (ReAct), pas de plan global obligatoire.
- Observation distincte de ToolResult.
- Context Builder séparé du Reasoner ; Reasoner ne devine jamais les outils.
- Planner renommé Reasoner.
- Policy Engine séparé de la State Machine, fonction pure, verdict à 4
  valeurs (`ALLOWED`/`DENIED`/`REQUIRES_CONFIRMATION`/`REWRITE`).
- Tool Registry = source de vérité unique sur les outils disponibles.
- Critic = système de hooks non bloquants, pas un état de la State Machine.
- Budget Manager = point d'extension identifié, non implémenté.
- `agent.py` supprimé du découpage — rôle absorbé par Runtime + State Machine
  + Context Builder + Reasoner + Executor.
- Terme "mémoire" réservé à une future mémoire sémantique (non conçue) — Event
  Log et Storage n'en font pas partie.

**Décisions prises pendant l'implémentation (non encore répercutées dans le
texte d'`ARCHITECTURE.md`, valables telles quelles) :**
- `Mission.status` réutilise directement les 8 valeurs de la State Machine
  (`CREATED`, `REASONING`, `EXECUTING`, `AWAITING_CONFIRMATION`, `SUCCEEDED`,
  `FAILED`, `MAX_ITERATIONS`, `ABORTED`) — pas d'enum simplifié séparé.
- Le champ JSON `action` du document de vision initial est devenu `tool_name`
  dans `ActionDecision`, pour ne pas entrer en collision avec le modèle
  `Action`. `FinishDecision` porte un `outcome: "success"|"failure"` ajouté
  (absent du sketch initial), nécessaire pour distinguer `SUCCEEDED`/`FAILED`.
- `ToolSpec.risk_level` est obligatoire, sans valeur par défaut.
- Contrat `Tool.execute()` : ne lève jamais pour un échec attendu (retourne
  `ToolResult(success=False)`) ; seul un bug d'implémentation remonte comme
  exception non gérée — c'est Executor qui la capture et la convertit en
  `ExecutionError(SYSTEM_ERROR)`.
- `ToolRegistry` enregistre des instances `Tool` exécutables, pas seulement
  des `ToolSpec` (décision validée explicitement par l'utilisateur après
  clarification). `list_tools()` continue de n'exposer que des `ToolSpec`
  (dérivés via `tool.spec`), par principe de moindre privilège.
- `ErrorCategory` : "erreur utilisateur" renommée `INVALID_REQUEST` (aucun
  humain n'est à l'origine d'une Decision défaillante).
  `ExecutionError.tool_name` est une chaîne libre, pas le type contraint
  `ToolName` — doit pouvoir enregistrer un nom d'outil invalide à des fins de
  diagnostic. `ExecutionError.is_expected` est une propriété dérivée de
  `category`, jamais un champ stocké séparément.
- Policy Engine : règles appliquées dans un ordre précis (motif dangereux
  spécifique avant règle générale par `risk_level`). Un seul motif dangereux
  implémenté (`rm -rf <cible>`) — illustratif, pas exhaustif. Le Policy Engine
  ignore délibérément `Action.risk_level` et re-dérive le risque depuis le
  Tool Registry (voir point ouvert correspondant).
- `ConfirmationResponse` gardé tel quel (pas renommé `ConfirmationDecision`)
  pour ne pas entrer en collision avec le concept `Decision` (sortie du
  Reasoner). `ConfirmationRequest`/`ConfirmationResponse` découplés par
  référence (`request_id`), jamais par embarquement.
- State Machine : les événements `ToolExecutionCompleted`/`ToolExecutionFailed`
  du vocabulaire `ARCHITECTURE.md` sont fusionnés en un seul
  `ToolExecutionFinished` côté State Machine (transition-équivalents — la
  distinction ne sert qu'à l'Event Log, pas à la transition). Un événement
  `AbortRequested`, absent du vocabulaire documenté, a été introduit pour le
  déclencheur externe d'abandon.
- **State Machine ne consulte pas le Policy Engine** — corrige une note
  obsolète d'`ARCHITECTURE.md` ("consulte policy.py"), écrite avant que cette
  décision ne soit tranchée explicitement. Le Verdict est fourni comme donnée
  de l'événement `PolicyEvaluated` par l'appelant (le Runtime).
- Convention de validation cohérente sur tous les modèles : chaînes
  significatives strippées, rejetées si vides quand obligatoires (`Mission.goal`,
  `ExecutionError.message`, `Verdict.reason`, `ConfirmationRequest.reason`) ;
  normalisées à `None` si optionnelles (`ConfirmationResponse.note`).
- `Observation` (`models/observation.py`) : modèle plat non discriminé
  (contrairement à `Decision`/`MissionEvent`), pour ne pas être tenté de faire
  porter aux différentes variantes des bouts de `ToolResult`/`ExecutionError`/
  `Verdict`. `kind: ObservationKind` (`EXECUTION_RESULT`/`EXECUTION_ERROR`/
  `POLICY_REJECTION`/`CONFIRMATION_DENIED`/`SYSTEM_INFO`), `summary: str`
  (non vide, strippé, même convention que les autres modèles), `details:
  dict[str, Any] | None` libre. `frozen=True` : même rationale qu'`Event`,
  c'est un fait déjà survenu, jamais recalculé après coup. N'importe aucun
  module `peon.*` — la traduction depuis `ToolResult`/`ExecutionError`/
  `Verdict`/refus reste la responsabilité du Runtime, seul composant à
  voir à la fois la source et la destination.
- **Abstraction LLM** (`llm.py`) : ABC `LLM` avec une unique méthode
  `generate(messages: list[dict[str, str]]) -> str`, sans dépendance externe
  ni client concret branché (Ollama/OpenAI/... reste pour une phase
  ultérieure). Format de message calqué sur la convention "chat" popularisée
  par OpenAI (`{"role": ..., "content": ...}`), choisi comme plus petit
  dénominateur commun entre fournisseurs.
- **PromptBuilder** (`prompts.py`) : classe (pas des fonctions pures), pour
  rester cohérent avec les autres composants injectés (`ContextBuilder`,
  `PolicyEngine`, `Executor`). `build(context) -> list[dict[str, str]]`
  déterministe, ne connaît que `Context` — aucune logique métier, aucun appel
  LLM. Produit un message `system` (contrat de sortie JSON fixe) et un
  message `user` (état de la mission, outils, observations).
- **LLMReasoner** (`reasoner.py`) : implémentation concrète de `Reasoner`,
  dépendances injectées (`LLM`, `PromptBuilder`). Une réponse LLM invalide
  (JSON malformé, `kind` absent/inconnu, arguments incorrects au regard du
  modèle `Decision` existant) lève `InvalidLLMResponseError`, exception dédiée
  définie dans `reasoner.py` (même pattern que `ToolNotFoundError`/
  `InvalidTransitionError`), plutôt qu'un nouveau mécanisme d'erreur global.
  `decide()` capture aussi toute exception levée par `self._llm.generate()`
  (réseau, timeout, réponse fournisseur invalide — `OllamaRequestError` pour
  Ollama, ou n'importe quel futur fournisseur) et la relève en
  `InvalidLLMResponseError` — seule exception que le Runtime a besoin de
  connaître, quel que soit le fournisseur `LLM` concret. Connectée au
  Runtime depuis la phase Robustesse LLM/Ollama (voir section 6) :
  `Runtime._run_reasoning_cycle()` la capture et fait échouer proprement la
  Mission (`FAILED`), sans exécuter d'Action ni fabriquer de résultat.
- **Tools concrets filesystem** (`tools/filesystem.py`) : `ReadFileTool`
  (`read_file`) et `ListDirectoryTool` (`list_directory`), toutes deux
  classées `RiskLevel.LOW` (lecture seule, aucun effet de bord ; la
  restriction de chemins reste explicitement hors périmètre de ces Tools, à
  traiter par un futur Policy Engine — cf. section 5). Échecs attendus
  (fichier/dossier absent, permission refusée, mauvais type/valeur
  d'argument, décodage invalide) capturés via `OSError`/`UnicodeDecodeError`
  et convertis en `ToolResult(success=False)` ; aucune exception ne sort de
  `execute()`, conformément au contrat `Tool`.
- **`ShellTool`** (`tools/shell.py`, outil `run_command`) : classé
  `RiskLevel.MEDIUM`, pas `HIGH`. Un `HIGH` déclencherait `REQUIRES_CONFIRMATION`
  pour *toute* invocation (règle générale de `policy.py`), ce qui bloquerait le
  Reasoner indéfiniment tant que rien ne consomme de `ConfirmationResponse` —
  et même une fois ce mécanisme branché, rendrait la boucle réactive
  inutilisable pour des commandes anodines. `MEDIUM` laisse `ALLOWED` par
  défaut et s'appuie sur la détection de motif dangereux de `policy.py`
  (`_check_dangerous_command`, déjà lue depuis `arguments["command"]` avant
  même que `ShellTool` existe) comme mécanisme de sécurité principal — cohérent
  avec « la sécurité appartient au Policy Engine », pas au Tool. Exécute via
  `subprocess.run(..., shell=True)`, sans filtrage ni whitelist.
- **Boucle de confirmation humaine** (`runtime.py`) : `Runtime.resume_confirmation(mission, response)`
  consomme un `ConfirmationResponse` externe — signature avec `mission`
  explicite plutôt que `response` seul, pour que « mauvais `request_id` » et
  « mauvaise mission » restent deux échecs distincts et testables (sans ce
  paramètre, `ConfirmationResponse` ne porte pas de `mission_id` pour les
  distinguer). Réutilise telles quelles les transitions `AWAITING_CONFIRMATION`
  déjà présentes dans `state_machine.py` et `_execute_action`/`_record_observation`
  déjà présents dans `runtime.py` — aucune de ces briques n'a été modifiée pour
  cette phase.
- **`ContextBuilder.build_from_event_log`** (`context_builder.py`) : reconstruit
  les `Observation` à partir des événements `OBSERVATION_PRODUCED` uniquement
  (les autres types restent des faits d'audit, jamais réinterprétés). Payload
  d'événement invalide → `MalformedObservationEventError` (échec explicite, pas
  d'absorption silencieuse : le Runtime est l'unique producteur normal de ces
  événements). A nécessité un correctif dans `Runtime._record_observation` :
  le payload loggé est passé de `{kind, summary}` à `observation.model_dump(mode="json")`
  (donc `details` inclus) — sans ce correctif, tout `Observation` reconstruite
  depuis l'Event Log aurait silencieusement perdu `details`.
- **`Storage`** (`storage.py`) : ABC minimale (`save_events`/`load_events`) +
  `InMemoryStorage`, sans dépendance vers `Runtime`/`Reasoner`/`PolicyEngine`/
  `Executor`/`Tool`/`Mission`. Décision délibérée : **`event_log.py` n'importe
  pas `storage.py`** (et réciproquement), pour préserver la décision déjà
  validée « aucune dépendance entre les deux » (cf. `ARCHITECTURE.md`) — le
  pont Event Log ↔ Storage vit dans `Runtime` (`persist_events()`,
  `load_event_log()`), qui dépend déjà légitimement de tout. `persist_events()`
  fait un instantané complet à la demande, sans suivi incrémental : l'appeler
  deux fois duplique (cf. section 5).
- **Checkpoint / reprise durable** (Phase 1, `models/checkpoint.py`,
  `storage.py`, `runtime.py`) : `Checkpoint` compose une `Mission` et une
  éventuelle `ConfirmationRequest` (`pending_confirmation: ConfirmationRequest
  | None`) plutôt que de dupliquer leurs champs — cohérent avec la même
  décision déjà prise pour `ConfirmationRequest`/`ConfirmationResponse`
  (découplage par référence, jamais par recopie de champs). Ne porte
  délibérément pas l'`EventLog` ni les `Observation` : le périmètre de cette
  phase est de restaurer juste assez d'état pour que `resume_confirmation()`
  fonctionne sur un nouveau `Runtime`, pas de rejouer l'historique complet
  (event-sourcing/replay reste hors périmètre, cf. `ARCHITECTURE.md`). `Storage`
  étendu de façon additive (`save_checkpoint`/`load_checkpoint`), signatures et
  sémantique de `save_events`/`load_events` inchangées. `InMemoryStorage` ne
  retient qu'un seul `Checkpoint` à la fois (remplacé, pas accumulé — cohérent
  avec le périmètre mono-Mission de cette phase) et retourne des copies
  profondes à la sauvegarde comme au chargement (une `Mission` restant mutable,
  contrairement à `Event`). Côté `Runtime` : `save_checkpoint(mission)` prend
  la `Mission` en paramètre explicite plutôt que de la garder comme attribut
  d'instance — même choix que `resume_confirmation(mission, response)`, pour
  ne pas introduire un `self._mission` que rien d'autre dans `Runtime`
  n'a besoin de porter. `resume_mission(checkpoint)` ne fait que restaurer
  `self._pending_confirmation` et retourner la `Mission` : aucune duplication
  de la boucle ReAct, aucune I/O, réutilise entièrement `resume_confirmation()`
  déjà existant pour la suite du cycle.
- **Persistance disque du Checkpoint** (`storage.py`, `cli.py`) : nouvelle
  implémentation `FileStorage(Storage)`, à côté d'`InMemoryStorage` (pas de
  transformation en stockage hybride) — ne persiste que le `Checkpoint`
  (JSON, via `Checkpoint.model_dump_json()`/`model_validate_json()`, déjà
  prévu par le modèle) ; `save_events`/`load_events` délèguent à un
  `InMemoryStorage` interne (les événements restent en mémoire, la
  persistance de l'`EventLog` restant hors périmètre de cette phase — voir
  section 7). Écriture atomique volontairement simple (`tempfile.mkstemp()`
  dans le même répertoire que la cible, puis `os.replace()`), répertoire
  parent créé si besoin. `load_checkpoint()` retourne `None` si le fichier
  est absent ; un fichier présent mais invalide (JSON malformé ou
  incompatible avec `Checkpoint`) lève `CorruptedCheckpointError` (nouvelle
  exception de `storage.py`) plutôt que d'échouer silencieusement — `pydantic`
  v2 lève déjà `ValidationError` aussi bien pour du JSON malformé que pour un
  schéma incompatible (`model_validate_json`), donc un seul bloc
  `except ValidationError` suffit à couvrir les deux cas. Mono-mission comme
  `InMemoryStorage` : chaque `save_checkpoint()` remplace le fichier
  précédent. Emplacement par défaut choisi par `cli.py`, pas par `storage.py`
  (même logique que les autres constantes CLI comme `_DEFAULT_MODEL`) :
  `_DEFAULT_CHECKPOINT_PATH = Path.home() / ".peon" / "checkpoint.json"` —
  convention simple, déterministe, sans dépendance externe
  (`platformdirs` etc.), pas de `.env` ni de nouvelle couche de
  configuration. `cli._storage` passe d'`InMemoryStorage()` à
  `FileStorage(_DEFAULT_CHECKPOINT_PATH)` : `peon resume` retrouve désormais
  un Checkpoint après un vrai redémarrage du process `peon`, plus seulement
  dans le même process (l'ancienne limite documentée dans `ARCHITECTURE.md`/
  `README.md`). Aucun changement à `save_events()`/`load_events()`
  eux-mêmes, à `EventLog`, ni au contrat abstrait `Storage`.
- **Workspace / `LocalWorkspace`** (Phase 2, `workspace.py`,
  `tools/filesystem.py`, `tools/shell.py`) : indirection technique pure
  (`Runtime -> Executor -> Tool -> Workspace -> filesystem/subprocess`), pas
  encore un système de sandboxing — délibérément hors périmètre de cette
  phase (voir point ouvert correspondant). Interface `Workspace` (ABC)
  déduite du code existant plutôt que dessinée a priori : trois méthodes
  seulement (`read_file`, `list_directory`, `run_command`), une par
  opération technique réellement utilisée par `ReadFileTool`,
  `ListDirectoryTool`, `ShellTool`. `run_command` retourne un `CommandResult`
  (`NamedTuple` : `stdout`/`stderr`/`return_code`), pas un
  `subprocess.CompletedProcess` réexposé tel quel, pour ne pas fuiter un type
  de la stdlib à travers le port. Chaque méthode laisse remonter les mêmes
  exceptions que l'implémentation d'origine (`OSError`,
  `UnicodeDecodeError`) : c'est toujours le Tool appelant qui les capture et
  les traduit en `ToolResult`, comportement strictement inchangé.
  `LocalWorkspace` reproduit exactement l'ancien code (`Path.read_text`,
  `Path.iterdir`, `subprocess.run(..., shell=True, capture_output=True,
  text=True)`), y compris son absence de tri : le tri des entrées de
  répertoire reste une responsabilité de `ListDirectoryTool` (logique de
  présentation), pas du Workspace (accès technique brut) — c'est le seul
  point où le découpage aurait pu se faire autrement. Les trois Tools
  reçoivent désormais un `Workspace` obligatoire au constructeur
  (`ReadFileTool(workspace)`, etc.), sans valeur par défaut : décision
  délibérée pour que l'injection reste explicite à chaque site de
  construction (tests, `build_runtime`), plutôt qu'un `LocalWorkspace()`
  implicite qui masquerait la dépendance. `build_runtime()`
  (`composition.py`) n'a pas eu besoin d'évoluer — il ne construit jamais les
  `Tool` lui-même, seulement le `ToolRegistry` à partir d'instances déjà
  construites par l'appelant. `PolicyEngine`, `Executor`, `StateMachine`,
  `EventLog`, `Storage`/`Checkpoint` : aucun n'a été touché par cette phase.
- **Policy / Guardrails** (Phase 3, `guardrails.py`, `policy.py`) :
  `PolicyEngine.evaluate()` ne porte plus lui-même la logique de règle — il
  compose une chaîne ordonnée de `PolicyRule` (`Action -> Verdict | None`,
  `Protocol` plutôt qu'une hiérarchie de classes, aucune abstraction
  supplémentaire n'étant justifiée). Ordre par défaut, invariant de sécurité :
  `ToolAuthorizationRule` → `DangerousCommandRule` → `ArgumentsSchemaRule` →
  (`PathRestrictionRule`, seulement si `workspace_root` est fourni) →
  `RiskLevelRule`. La règle spécifique (`DangerousCommandRule`) garde la
  priorité sur la règle générique (`RiskLevelRule`), exactement comme avant
  cette phase — invariant explicitement demandé et vérifié par
  `test_dangerous_command_rule_takes_priority_over_the_generic_risk_level_rule`.
  `PolicyEngine(registry)` (constructeur par défaut, seule forme utilisée par
  tous les appelants existant avant cette phase) reproduit exactement le
  comportement observable pré-Phase 3 : `ArgumentsSchemaRule`, bien que
  nouvelle, ne rejette jamais une Action qui passait avant (tous les
  `ToolSpec` réels déclarent des schémas conformes aux arguments réellement
  envoyés dans les tests/l'usage existant), et `PathRestrictionRule` est
  absente tant que `workspace_root` n'est pas explicitement fourni.
  **Autorisation par Tool** (`ToolAuthorizationRule`) : reste équivalente à
  « connu du `ToolRegistry` » — `ToolSpec` ne porte aujourd'hui aucun champ
  d'autorisation distinct (pas de `enabled`, pas de rôles) ; en inventer un
  aurait fait doublon avec le registre déjà injecté au `PolicyEngine`, donc
  pas fait dans cette phase (voir section 5, nouveau point ouvert).
  **Validation des arguments** (`ArgumentsSchemaRule`) : résout le point
  ouvert « qui valide `Action.arguments` contre
  `ToolSpec.parameters_schema` ? » — c'est le Policy Engine, jamais le Tool
  ni l'Executor. Sous-ensemble minimal de JSON Schema implémenté à la main
  (`type: object`, `properties`, `required`) plutôt qu'une dépendance externe
  (`jsonschema`), suffisant pour tous les `ToolSpec` existants et cohérent
  avec le peu de dépendances du projet ; `additionalProperties` non spécifié
  reste permissif (sémantique JSON Schema standard), seul
  `additionalProperties: false` explicite le restreindrait. Réutilise
  `DENIED` (pas de cinquième `Verdict`) pour une Action structurellement
  invalide.
  **Restriction de chemin** (`PathRestrictionRule`) : la racine autorisée
  (`workspace_root`) est une donnée de configuration passée directement au
  `PolicyEngine` (`Path`), jamais lue depuis `Workspace` — `Workspace`
  n'avait et n'a toujours aucune notion de racine (`LocalWorkspace()` sans
  argument, Phase 2 inchangée), et lui en ajouter une l'aurait transformé en
  arbitre de sécurité, contraire à son rôle de port technique pur. La règle
  ne s'applique qu'aux Tools dont `ToolSpec.parameters_schema` déclare une
  propriété nommée `path` (déterminé depuis le schéma, jamais depuis une
  liste de noms de Tools codée en dur) ; résout la traversée (`..`) et les
  chemins absolus via `Path.resolve()` + containment check
  (`root in resolved.parents`). Opt-in : `PolicyEngine(registry)` sans
  `workspace_root` ne restreint toujours aucun chemin. Réellement branchée
  depuis `build_runtime()` (`composition.py`, pass-through
  `workspace_root=...` vers `PolicyEngine`) et depuis `cli.py`
  (`peon run "<goal>" --workspace-root <path>`, idem `peon resume`,
  normalisé par `Path.resolve()` à la frontière CLI) — `None` par défaut des
  deux côtés, donc aucune régression pour un appelant qui ne fournit pas
  cette option.
  **Fail-closed de composition** : si une chaîne de `PolicyRule` personnalisée
  (paramètre `rules=`) ne produit aucun `Verdict` pour une Action donnée,
  `evaluate()` lève `AssertionError` plutôt que d'autoriser silencieusement
  par défaut — choix délibéré pour un moteur de sécurité, la composition par
  défaut (toujours terminée par `RiskLevelRule`, inconditionnelle pour tout
  Tool enregistré) n'atteint jamais ce chemin.

## 5. Points volontairement laissés ouverts

1. Contrat d'intervention du Critic — un hook peut-il bloquer une action, ou
   est-il purement observationnel ?
2. Adoption d'un `REWRITE` toujours via le Reasoner (jamais auto-appliqué) —
   à confirmer si un futur mode "autonome" doit court-circuiter cette étape.
3. Budget Manager : centralise-t-il les compteurs existants (State Machine,
   Context Builder), ou reste-t-il un agrégateur en lecture seule ?
4. Synchronicité de la persistance Storage (synchrone à chaque événement, ou
   différée/batchée) — à trancher avant la phase Storage.
5. Tool Registry statique (liste fixe au démarrage) ou dynamique
   (enregistrement à chaud, plugins) ?
6. Rôle réel d'`Action.risk_level` — ignoré par le Policy Engine aujourd'hui :
   doit-il disparaître, ou être réécrit *par* le Policy Engine comme sortie
   plutôt que lu comme entrée ?
7. `ToolRegistry` n'expose pas d'accesseur métadonnées-seule distinct de
   `get(name) -> Tool` — le Policy Engine respecte "ne jamais exécuter" par
   discipline, pas par un type qui l'en empêcherait structurellement.
8. ~~Qui valide `Action.arguments` contre `ToolSpec.parameters_schema` ? Le
   Tool lui-même, l'Executor en amont, les deux ?~~ — résolu par la Phase 3
   (Policy/Guardrails) : c'est le Policy Engine (`ArgumentsSchemaRule`,
   `guardrails.py`), jamais le Tool ni l'Executor. Sous-ensemble minimal de
   JSON Schema (`type`, `properties`, `required`), voir section 4.
9. Que fait le Runtime face à un `ExecutionError(SYSTEM_ERROR)` ? Échec
   automatique de la Mission, ou nouvelle tentative laissée au Reasoner ?
10. `ExecutionError.details` reste un `dict[str, Any]` libre, non structuré
    par catégorie.
11. Détection de commandes dangereuses par le Policy Engine volontairement
    minimale (une seule regex `rm -rf`) — pas de vrai moteur de règles.
12. Aucun timeout modélisé pour une `ConfirmationRequest` en attente ; rien
    n'empêche formellement plusieurs confirmations simultanées pour une même
    Mission (improbable vu la boucle réactive, mais pas garanti par les
    modèles).
13. Deux vocabulaires d'événements coexistent : celui, riche, d'
    `ARCHITECTURE.md` (pour l'Event Log) et celui, réduit, de
    `state_machine.py` (pour les transitions). Aucune traduction entre les
    deux n'existe encore.
14. ~~Qui compare `iteration_count`/`max_iterations` et décide d'émettre
    `MaxIterationsReached` ?~~ — résolu par l'implémentation : c'est le
    `Runtime` (`_run_reasoning_cycle`, dans `runtime.py`) qui incrémente
    `iteration_count` et le compare à `mission.max_iterations` avant chaque
    cycle de raisonnement, confirmé par `test_max_iterations_stops_the_loop`.
15. Comment le Runtime doit-il traiter une `InvalidTransitionError` (crash,
    log, absorption silencieuse) ?
16. ~~`ARCHITECTURE.md` contient des sections obsolètes~~ — corrigé dans cette
    même mise à jour de documentation (voir section 6).
17. ~~`Observation` n'a pas d'`id`/`timestamp` propres (contrairement à
    `Event`)~~ — confirmé par l'usage : c'est le Runtime qui les porte en
    empaquetant l'Observation dans un `Event(OBSERVATION_PRODUCED,
    payload=observation.model_dump(mode="json"))`, et `ContextBuilder.build_from_event_log`
    reconstruit l'`Observation` depuis ce payload sans jamais avoir besoin d'un
    `id`/`timestamp` propre à `Observation` elle-même.
18. `ObservationKind.POLICY_REJECTION` couvre à la fois `DENIED` et `REWRITE`
    sans les distinguer : la suggestion `REWRITE` (l'`Action` alternative)
    devra être portée dans `details` par le Runtime. À séparer en un kind
    dédié plus tard si besoin, ou rester ainsi ?
19. ~~Aucune restriction de chemin dans `ReadFileTool`/`ListDirectoryTool`~~
    — résolu par la Phase 3 (mécanisme, `PathRestrictionRule` dans
    `guardrails.py`, consultée par le `PolicyEngine`, pas par les Tools ni
    par `Workspace`) puis par le chantier « wiring » qui a suivi : le
    mécanisme est maintenant réellement activable, `build_runtime()`
    (`composition.py`) expose `workspace_root=...` en pass-through vers
    `PolicyEngine`, et `cli.py` expose `--workspace-root` sur `peon run`
    et `peon resume`. Reste opt-in (`None` par défaut partout, aucune
    restriction si l'option n'est pas fournie) — décision assumée, pas un
    oubli. Reste ouvert : la racine n'est toujours pas dérivée du
    `Workspace` lui-même (les deux restent indépendants par choix, voir
    section 4) ; `--workspace-root` sur `peon resume` doit être refourni
    explicitement à chaque reprise (n'est pas persisté dans le `Checkpoint`,
    voir section 7).
20. Format d'`output` des Tools filesystem non uniformisé : `ReadFileTool`
    retourne une chaîne (contenu brut), `ListDirectoryTool` une liste triée de
    noms (`list[str]`), sans distinguer fichier/dossier ni exposer de
    métadonnées (taille, type, date de modification). Suffisant pour valider
    le pipeline ; une évolution vers une structure plus riche (ex.
    `{"name": ..., "is_dir": ...}`) n'est pas tranchée.
21. `Observation.details` reste un `dict[str, Any]` libre, sans schéma par
    Tool ou par catégorie d'erreur (même remarque que le point 10 pour
    `ExecutionError.details`, jamais formalisée pour `Observation.details`) :
    à mesure que le nombre de Tools augmente (filesystem aujourd'hui, puis
    shell/git/search), sa forme risque de diverger d'un Tool à l'autre sans
    contrat commun. Non structuré pour l'instant.
22. ~~Rien ne consomme encore un `ConfirmationResponse` pour reprendre un cycle
    en `AWAITING_CONFIRMATION`~~ — résolu par l'implémentation :
    `Runtime.resume_confirmation(mission, response)` consomme la réponse et
    reprend le cycle (voir section 4). Reste ouvert : pas de CLI interactive
    branchée pour produire cette réponse dans un usage réel (cf. point 26).
23. Détection de commandes dangereuses toujours minimale (point 11) : `ShellTool`
    (`run_command`) est désormais un vrai consommateur de
    `_check_dangerous_command`, mais celui-ci ne reconnaît toujours qu'un seul
    motif (`rm -rf <cible>`). D'autres commandes destructrices citées dans la
    vision initiale (`git push`, suppression massive) ne sont détectées que
    via le `risk_level` déclaré par le Tool, pas par inspection de la commande
    elle-même.
24. ~~`Runtime.persist_events()` fait un instantané complet de l'Event Log à
    chaque appel, sans suivi de ce qui a déjà été persisté : l'appeler deux
    fois duplique dans `Storage`.~~ — résolu (chantier « reprise durable avec
    historique ») : `Runtime` suit désormais le nombre d'événements déjà
    envoyés (`self._persisted_event_count`) et n'envoie que le delta à
    chaque appel, cohérent avec le contrat append-only de `Storage.
    save_events()`. Toujours pas de branchement automatique dans la boucle
    de raisonnement (délibérément hors périmètre) : `persist_events()` reste
    à l'appelant, comme `cli.py::_drive_to_completion` le fait désormais à
    chaque `save_checkpoint()`.
25. ~~Recharger un `EventLog` depuis `Storage` (`Runtime.load_event_log`) ne
    restaure ni la `Mission` (statut, compteur d'itérations) ni la
    `StateMachine` ni une `ConfirmationRequest` en attente — seul l'historique
    brut des événements est reconstructible. Une vraie « reprise de mission
    après arrêt du process » reste à concevoir.~~ — résolu par l'implémentation
    (Phase 1, checkpoint/reprise, puis chantier « reprise durable avec
    historique ») : `Checkpoint` (`models/checkpoint.py`) +
    `Runtime.save_checkpoint()`/`resume_mission()` restaurent la `Mission` et
    une `ConfirmationRequest` en attente sur un nouveau `Runtime`, et
    `resume_mission()` reconstruit désormais aussi `self._observations`
    depuis `self._event_log` (via `ContextBuilder.build_from_event_log`,
    déjà utilisé par la boucle de raisonnement) — si l'appelant a préalablement
    injecté un `EventLog` peuplé via `Runtime.load_event_log(storage)`
    (voir `build_runtime(event_log=...)`), le `Context` reconstruit après
    reprise est identique à celui qu'aurait eu le Runtime d'origine au même
    point. `resume_mission()` lui-même ne charge toujours rien depuis
    `Storage` — c'est l'appelant (`cli.py::resume`, ou tout code de
    composition) qui reste responsable de ce câblage, pas de replay
    automatique caché dans `Runtime`.
26. ~~`Storage` n'a qu'une implémentation en mémoire (`InMemoryStorage`), pour
    les événements comme pour les checkpoints : malgré l'abstraction et le
    mécanisme de reprise en place (point 25), une Mission ne survit pas
    encore à un arrêt réel du process.~~ — résolu : `FileStorage`
    (`storage.py`) persiste désormais le `Checkpoint` **et** l'`EventLog` sur
    disque (fichier JSON Lines séparé, nommé d'après le chemin du
    Checkpoint), utilisé par défaut par `cli.py` (`peon resume` survit à un
    vrai redémarrage du process, historique compris). Un contenu corrompu
    lève `CorruptedEventLogError` (même famille que `CorruptedCheckpointError`)
    plutôt que d'échouer silencieusement. Un backend disque plus robuste pour
    l'`EventLog` (SQLite ou autre, transactionnel, sans réécriture complète du
    fichier à chaque `save_events()`) reste une évolution possible derrière la
    même interface `Storage`, mais n'est plus un prérequis pour la reprise —
    `FileStorage` (JSON Lines) suffit déjà.
27. `Runtime` ne retient qu'une seule `ConfirmationRequest` en attente à la
    fois (`self._pending_confirmation`, réinitialisée à chaque `run()`) :
    suffisant pour une Mission à la fois, pas conçu pour plusieurs Missions
    concurrentes sur un même `Runtime`. `Checkpoint` hérite de la même
    limite : `Storage.save_checkpoint()`/`load_checkpoint()` ne retiennent
    qu'un seul `Checkpoint` à la fois, pas une reprise multi-mission
    (délibérément hors périmètre de la Phase 1 — voir `ARCHITECTURE.md`).
28. `ToolAuthorizationRule` (Phase 3, `guardrails.py`) traite « autorisé »
    comme strictement équivalent à « enregistré dans le `ToolRegistry` » :
    `ToolSpec` ne porte aucun champ d'autorisation plus fin (pas de rôles,
    pas de `enabled: bool` désactivable sans désenregistrer le Tool). Suffit
    au périmètre demandé par la Phase 3 (refuser un Tool inconnu), mais une
    autorisation par utilisateur/mode d'exécution/environnement, si elle
    devient nécessaire, demandera un champ dédié sur `ToolSpec` (ou un
    mécanisme séparé consulté par cette même règle) — non conçu, non
    implémenté.
29. ~~`PathRestrictionRule` (Phase 3) détecte l'échappement de racine via
    `Path.resolve()` + containment check, sans traiter explicitement les
    liens symboliques [...] ni les cas Windows spécifiques [...] — non
    approfondi dans cette phase.~~ — vérifié explicitement par le chantier
    « wiring » qui a suivi (`tests/test_guardrails.py`) : une jonction NTFS
    créée à l'intérieur de la racine mais pointant vers une cible extérieure
    est bien refusée (`Path.resolve()` suit la jonction jusqu'à sa cible
    réelle avant le containment check, comportement déjà correct, maintenant
    testé plutôt que seulement supposé) ; la comparaison de chemins est
    insensible à la casse et aux séparateurs `/`/`\` sur Windows
    (`WindowsPath`, natif à `pathlib`, pas une logique ajoutée par cette
    règle). Chemins UNC (`\\serveur\partage\...`) toujours non testés
    explicitement — reste un point ouvert mineur si un jour pertinent.
30. ~~Entre la confirmation d'une Action `HIGH` et la persistance normale
    suivante (`cli.py::_drive_to_completion`), un crash pouvait laisser sur
    disque un `Checkpoint` `AWAITING_CONFIRMATION` déjà résolu en mémoire :
    un `peon resume` ultérieur retrouvait la même `ConfirmationRequest`
    "en attente" et réexécutait l'Action `HIGH` (ex. `delete_file`) une
    deuxième fois.~~ — résolu (chantier « Idempotence durable d'une Action
    HIGH après confirmation ») : `Runtime.resume_confirmation()` persiste
    désormais l'état (Checkpoint + delta d'EventLog) juste avant *et* juste
    après `_execute_action()`, quand un `Storage` est injecté — voir
    `ARCHITECTURE.md`, section **Runtime**. Reste ouvert, assumé et documenté
    plutôt que masqué : si le crash survient exactement pendant
    `Executor.run()` ou pendant l'écriture du second point de persistance
    (fenêtre résiduelle inévitable sans coupler transactionnellement le Tool
    à l'écriture disque — hors périmètre de ce chantier, et non demandé), la
    Mission reste durablement "garée" en `EXECUTING`/`REASONING` : plus
    aucune double exécution possible (`pending_confirmation` déjà `None` sur
    disque), mais `peon resume` ne relance pas non plus automatiquement la
    boucle de raisonnement dans ce cas précis — il n'existe aujourd'hui
    aucune API publique pour reprendre le raisonnement sans
    `ConfirmationRequest` en attente (limite déjà présente de
    `cli.py::resume`, inchangée par ce chantier, pas une régression). Une
    vraie continuation automatique de la Mission depuis cet état "garé"
    resterait à concevoir si elle devient nécessaire.
31. Risque distinct du point 27 (`Runtime`/`Checkpoint` mono-mission, non
    modifié par ce qui suit et toujours vrai) : `cli._storage` (un
    `FileStorage`) est partagé entre toutes les invocations `peon run`
    successives, et `Storage.save_events()` est append-only *pour toujours*,
    sans aucune notion de Mission. Une `EventLog` rechargée via
    `Runtime.load_event_log()` pouvait donc porter l'historique de plusieurs
    Missions bout à bout, et `ContextBuilder.build_from_event_log()` ne
    filtrait jusque-là aucun `OBSERVATION_PRODUCED` par Mission — les
    Observations d'une Mission antérieure, déjà terminée et sans rapport,
    fuyaient dans le `Context` reconstruit pour la reprise de la Mission
    courante (audit d'isolation multi-Mission). Résolu par
    `ContextBuilder._current_mission_events(event_log)` : ne retient que les
    événements survenus depuis le plus récent `MISSION_CREATED` — voir
    `ARCHITECTURE.md`, section **Context Builder**, et
    `tests/test_resume_history.py::test_cas5_*`/
    `tests/test_context_builder_event_log.py`. N'introduit aucune reprise
    multi-mission (toujours hors périmètre, voir point 27) : corrige
    uniquement la reconstruction du `Context`, pas la capacité du `Runtime`/
    `Checkpoint` à suivre plusieurs Missions à la fois. Audit conclu : aucun
    autre changement de code jugé nécessaire.
32. `peon run "<goal>"` avec un `goal` vide/blanc ou un `--max-iterations`
    invalide (`< 1`) laissait fuir une `pydantic.ValidationError` brute
    (levée par `Mission.__init__`, voir `models/mission.py`) au lieu d'un
    message et d'un `exit_code=1` propres — même classe de défaut que celui
    déjà corrigé pour les erreurs `Storage` sur `peon resume` (voir plus
    haut, `CorruptedCheckpointError`/`CorruptedEventLogError`). Résolu :
    `cli.py::run` capture désormais `ValidationError` autour de
    `runtime.run()` et affiche `Invalid mission parameters.` avant de sortir
    en erreur, même convention que `resume` — voir `ARCHITECTURE.md`,
    section **CLI**.

## 6. État actuel d'implémentation

**Implémenté et testé (344 tests, tous verts) :**

```
src/peon/
  __init__.py, cli.py                    # peon --version, peon run, peon resume (Phase 5)
  composition.py                         # build_runtime() : assemble un Runtime depuis un LLM concret
  models/
    mission.py, checkpoint.py, context.py, decision.py, action.py,
    tool_spec.py, tool_result.py, execution_error.py, verdict.py,
    confirmation.py, events.py, observation.py
  tools/
    base.py                              # contrat Tool (ABC)
    filesystem.py                        # ReadFileTool (read_file), ListDirectoryTool (list_directory)
                                          # -- injectees avec un Workspace (Phase 2)
    shell.py                             # ShellTool (run_command, RiskLevel.MEDIUM)
                                          # -- injecte avec un Workspace (Phase 2)
  workspace.py                           # Workspace (ABC) + LocalWorkspace (Phase 2) :
                                          # port technique filesystem/subprocess pour les Tools
  providers/
    ollama.py                            # OllamaLLM : premier fournisseur LLM concret
  tool_registry.py                       # enregistre des Tool, pas seulement des ToolSpec
  executor.py
  policy.py                              # PolicyEngine.evaluate() : compose la chaine de guardrails.py
  guardrails.py                          # PolicyRule (Protocol) + regles composables (Phase 3)
  state_machine.py                       # fonction pure transition(), evenements dedies
  event_log.py                           # journal append-only en memoire (Event/EventType)
  context_builder.py                     # build() et build_from_event_log() -> Context
  reasoner.py                            # ABC Reasoner + LLMReasoner (implementation concrete)
  llm.py                                 # ABC LLM
  prompts.py                             # PromptBuilder : Context -> messages LLM
  storage.py                             # ABC Storage + InMemoryStorage
                                          # (save_events/load_events, save_checkpoint/load_checkpoint)
  tracing.py                             # Tracer/Span (ABC) + NoOpTracer (Phase 4 - observabilite) :
                                          # port minimal, Runtime -> Tracer, aucun couplage a EventLog
  runtime.py                             # orchestrateur : assemble tous les composants ci-dessus,
                                          # + resume_confirmation, persist_events, load_event_log,
                                          # save_checkpoint, resume_mission ; accepte un Tracer optionnel
```

Le cycle complet `Mission -> Context -> Reasoner -> Decision -> Policy Engine
-> Executor -> Tool -> ToolResult -> Observation -> Event Log` est exercé de
bout en bout par des tests d'intégration sans mock interne
(`tests/test_integration_read_file.py`,
`tests/test_integration_list_directory.py`,
`tests/test_integration_run_command.py`), avec de vrais `Tool` (I/O disque et
sous-processus réels) et un Reasoner stub déterministe pour la plupart des
tests (un vrai `OllamaLLM` est exercé via `tests/providers/test_ollama.py` et
`tests/test_integration_llm_provider.py`, contre un faux serveur HTTP local).
La boucle de confirmation humaine complète (`tests/test_confirmation_flow.py`)
et le round-trip `EventLog -> Storage -> reload -> ContextBuilder`
(`tests/test_runtime_storage.py`) sont également exercés de bout en bout.
Le checkpoint/reprise après crash simulé (deux instances `Runtime` distinctes,
confirmation restaurée puis acceptée/refusée) est couvert par
`tests/test_checkpoint.py` (Phase 1).

**Non implémenté :** un backend `Storage` persistant sur disque (SQLite ou
autre) derrière l'abstraction déjà en place (y compris pour les checkpoints),
`tools/git.py`, `tools/search.py`, un second fournisseur `LLM` concret
au-delà d'`OllamaLLM` (OpenAI/Anthropic/...), toute CLI interactive capable de
produire un `ConfirmationResponse` réel (le mécanisme de reprise lui-même,
`Runtime.resume_confirmation`, est implémenté et testé — voir section 4), et
la restauration complète de l'`EventLog`/des `Observation` lors d'une reprise
(le `Checkpoint` restaure la `Mission` et la confirmation en attente, pas
l'historique de raisonnement — voir point 25, section 5).

**Git** : dépôt local initialisé, dossier de travail local renommé
`projects/arne/` -> `projects/peon/` pour correspondre au nom du futur dépôt
publié, toujours aucun commit.

**Documentation** : cette mise à jour (`README.md`, `ARCHITECTURE.md`,
`CONTEXT.md`, `CHANGELOG.md`, `LICENSE`) prépare la première publication du
dépôt sous le nom **Péon** : renommage du package (`src/arne` -> `src/peon`,
tous les imports), réalignement des statuts de composants et du compteur de
tests, correction des mentions `arne`/`Arne` restantes et des sections
`ARCHITECTURE.md` devenues fausses (Storage décrit comme SQLite alors que seule
une abstraction + `InMemoryStorage` existent, "futur Runtime" alors que
`Runtime` est implémenté depuis plusieurs phases, diagramme de séquence
montrant une persistance automatique par événement qui n'a jamais été
implémentée ainsi).

Mise à jour ultérieure (Phase 1 — checkpoint/reprise) : correction du
compteur de tests (263 -> 279 au moment de cette phase, puis 289 avec les
tests de checkpoint ajoutés), documentation d'`OllamaLLM`/`composition.py`
(déjà présents dans le code mais jamais reflétés ici), et documentation du
nouveau `Checkpoint`/mécanisme de reprise.

Mise à jour ultérieure (Phase 2 — Workspace) : introduction du port
`Workspace`/`LocalWorkspace` (`workspace.py`) entre les Tools et le
filesystem/`subprocess` réels, migration de `ReadFileTool`,
`ListDirectoryTool` et `ShellTool` pour déléguer à un `Workspace` injecté au
constructeur plutôt que d'appeler `pathlib`/`subprocess` directement,
comportement fonctionnel strictement inchangé. Compteur de tests : 289 ->
295 (6 nouveaux tests dans `tests/test_workspace.py`).

Mise à jour ultérieure (Phase 3 — Policy / Guardrails) : `PolicyEngine`
refactoré pour composer une chaîne ordonnée de `PolicyRule` (`guardrails.py`,
nouveau module) au lieu de porter lui-même la logique de règle. Ordre
préservé exactement (motif de commande dangereuse avant `risk_level`
générique) ; deux règles nouvelles ajoutées (`ArgumentsSchemaRule`,
`PathRestrictionRule`, cette dernière opt-in via `workspace_root`) ; aucune
logique de sécurité déplacée vers un Tool, `Workspace`, l'`Executor` ou la
`StateMachine` — voir section 4 pour le détail des décisions. Compteur de
tests : 295 -> 328 (20 nouveaux tests dans `tests/test_guardrails.py`, 13
nouveaux dans `tests/test_policy.py`), zéro régression sur les 295 tests
préexistants (aucun modifié).

Mise à jour ultérieure (Phase 4 — Observability / tracing technique) :
nouveau module `tracing.py` (`Tracer`/`Span` ABC + `NoOpTracer`), port
d'observabilité minimal et volontairement séparé du vocabulaire métier
(`Runtime -> Tracer`, jamais via l'`EventLog` — voir **Tracer** dans
`ARCHITECTURE.md`). `Runtime` reçoit un `tracer` optionnel
(`tracer=None` par défaut, comportement observable strictement inchangé) et
instrumente quatre points déjà existants dans sa boucle : `run()`,
`resume_confirmation()`, l'appel au Reasoner (`reasoner.decide`, span autour
de l'appel LLM) et l'appel à l'Executor (`executor.run`, span avec l'attribut
`tool_name`). Aucun `EventType` ajouté, aucun événement de tracing dans
l'`EventLog`, aucun changement à `Storage`/`Checkpoint`/`StateMachine`/
`PolicyEngine`/`Executor`/Tools. Pas de données de tokens : aucune source
fiable n'existe aujourd'hui dans `LLM`/`Reasoner` (`LLM.generate()` retourne
une chaîne brute, sans métadonnée d'usage), donc rien n'est fabriqué. Pas de
dépendance OpenTelemetry à ce stade — abstraction pensée pour qu'un futur
adaptateur OTel puisse s'y brancher sans réécriture. Compteur de tests : 328
-> 337 (9 nouveaux dans `tests/test_tracing.py` : comportement inchangé sans
tracer, séquence de spans cohérente sur une mission complète, spans imbriqués
pour `resume_confirmation()`, fermeture de span garantie même sur exception,
`EventLog`/`Observation` strictement identiques avec ou sans tracer,
contrat `NoOpTracer` testé isolément), zéro régression sur les 328 tests
préexistants (aucun modifié).

Mise à jour ultérieure (Phase 5 — CLI minimale) : `cli.py` passe de
`peon --version` seul à deux commandes réellement utilisables, `peon run
"<goal>"` et `peon resume`, sans jamais réimplémenter la boucle ReAct — la
CLI ne pilote que l'API publique du `Runtime` (`run`, `resume_confirmation`,
`resume_mission`, `save_checkpoint`, `pending_confirmation`), construit son
`Runtime` via `build_runtime()` (`composition.py`, inchangé) et n'importe ni
`Reasoner`, ni `PolicyEngine`, ni `Executor`, ni `StateMachine` (vérifié par
un test dédié qui inspecte l'AST du module). `peon run` gère les
confirmations de façon synchrone et interactive (affichage `Tool`/
`Arguments`/`Reason`, lecture `y`/`N` via `typer.confirm`, construction d'un
vrai `ConfirmationResponse`, boucle jusqu'à l'état final pour couvrir
plusieurs confirmations successives) et appelle `save_checkpoint(mission)`
juste avant chaque demande — le point que `Runtime.save_checkpoint()`
documentait déjà comme pertinent. `peon resume` s'appuie réellement sur le
Checkpoint de la Phase 1 (`Storage.load_checkpoint()` ->
`Runtime.resume_mission()` -> même boucle de confirmation que `run` si une
confirmation est en attente) ; absence de checkpoint -> message clair et
`exit_code=1`, pas d'exception brute. Configuration LLM volontairement
minimale : options `--model`/`--base-url`/`--timeout-seconds` sur
`OllamaLLM` (seul fournisseur existant), valeurs par défaut explicites en
constantes de module (`llama3.1`, `http://localhost:11434`, `60s`) — pas de
`.env`, pas de nouvelle couche de configuration. Deux limites assumées et
documentées plutôt que masquées (voir **CLI** dans `ARCHITECTURE.md`) : (1)
`InMemoryStorage` reste partagé en mémoire du process CLI (`cli._storage`),
donc `peon resume` ne retrouve un Checkpoint que dans le même process, pas
après un vrai redémarrage de `peon` ; (2) avec les trois `Tool` réels actuels
(`LOW`/`LOW`/`MEDIUM`), aucun n'est `HIGH`, donc le chemin de confirmation
n'est aujourd'hui jamais déclenché en usage réel — implémenté et testé via
des `Tool` de test `HIGH`, prêt sans changement dès qu'un `Tool` `HIGH` réel
existera. Compteur de tests : 337 -> 344 (7 nouveaux dans `tests/test_cli.py`
: objectif accepté et mission résolue avec un LLM stub, chemin nominal avec
action puis fin, confirmation acceptée avec reprise réelle de l'action,
confirmation refusée avec comportement `Runtime` inchangé, `peon resume`
sans checkpoint échoue proprement, `peon resume` retrouve et reprend un
Checkpoint réel, absence de boucle ReAct dupliquée dans `cli.py`), zéro
régression sur les 337 tests préexistants (aucun modifié).

Mise à jour ultérieure (Phase 6 — correctif du chemin Tool -> Observation ->
Context -> Prompt -> LLM) : bug découvert lors d'un test réel avec Ollama
(`qwen3:14b`, `gpt-oss:20b`). `Runtime._execute_action` construisait déjà
correctement l'`Observation` de succès avec `details = {"tool_name": ...,
"output": result.output}` (le contenu réel de `ToolResult.output` survivait
bien jusqu'au `Context`, via l'`EventLog` puis `ContextBuilder` — rien à
corriger de ce côté), mais `PromptBuilder._render_observations()`
(`prompts.py`) ne rendait que `observation.summary`, un texte générique
("outil 'x' exécuté avec succès") pour toute `Observation` de kind
`EXECUTION_RESULT`. Le LLM ne voyait donc jamais le contenu réel produit par
un outil réussi (ex. stdout de `run_command`, contenu lu par `read_file`) et
répétait l'action ou hallucinait un résultat jusqu'à `max_iterations`.
Correctif contenu entièrement dans `prompts.py` : `_render_observations()`
ajoute une ligne `resultat : ...` sous le résumé, uniquement pour les
`Observation` de kind `EXECUTION_RESULT` dont `details["output"]` n'est ni
absent ni `None` (chaîne affichée telle quelle, dict/liste sérialisés en JSON
via `json.dumps(..., sort_keys=True)`), tronquée à `_MAX_OUTPUT_CHARS = 2000`
caractères — aucune convention de troncature n'existait avant dans le code,
valeur choisie arbitrairement plutôt que d'introduire une abstraction dédiée.
Rendu des autres `ObservationKind` (`EXECUTION_ERROR`, `POLICY_REJECTION`,
`CONFIRMATION_DENIED`) strictement inchangé : leur `summary` était déjà le
contenu informatif complet (ex. `result.message` pour une erreur), donc
inutile et risqué de dupliquer `details` dessus. Aucun changement à
`Observation`/`Context`/`EventLog`/`Runtime`/`ContextBuilder`/`PolicyEngine`/
`Executor`/`StateMachine`/`Storage`/`Checkpoint`/`Workspace`/`Tracer`.
Compteur de tests : 344 -> 350 (5 nouveaux dans `tests/test_prompts.py` :
sortie texte et sortie dict/JSON d'un résultat réussi visibles dans le
prompt, troncature au-delà de la limite, rendu d'erreur inchangé, sortie
`None`/absente ignorée ; 1 nouveau dans
`tests/test_integration_reasoner_uses_tool_output.py`, scénario bout en bout
où un `Reasoner` qui ne lit que le texte de prompt réellement construit par
`PromptBuilder` — même contrat que `LLMReasoner`, sans dépendre d'un serveur
Ollama — décide correctement au second tour grâce à la sortie du premier
appel d'outil), zéro régression sur les 344 tests préexistants (aucun
modifié).

Mise à jour ultérieure (Robustesse LLM/Ollama — erreurs traitées comme
défaillance de mission propre) : deux exceptions préexistantes remontaient
jusqu'au `Runtime` sans traitement, confirmé lors de tests réels avec Ollama
sur des prompts longs (`gpt-oss:20b`) — `OllamaRequestError` (HTTP 500,
problème réseau, réponse JSON invalide ou champ manquant) et
`InvalidLLMResponseError` (réponse LLM qui ne respecte pas le contrat JSON
`Decision`). `LLMReasoner.decide()` (`reasoner.py`) capture désormais toute
exception levée par `self._llm.generate()` et la relève en
`InvalidLLMResponseError` (`raise ... from exc`, cause d'origine préservée) —
traduction générique au niveau du contrat abstrait `LLM`, valable pour
n'importe quel fournisseur futur, pas seulement Ollama. `Runtime.
_run_reasoning_cycle()` (`runtime.py`) capture cette `InvalidLLMResponseError`
autour de l'appel à `self._reasoner.decide(context)` et fait transiter la
Mission vers `FAILED` via le même événement `MissionFailed` que
`_finish_mission(..., outcome="failure")` (nouvelle méthode privée
`_fail_mission_on_reasoning_error()`) : aucune Action exécutée, aucune
Observation/Decision fabriquée pour ce tour, historique (`EventLog`,
observations précédentes) intact, aucun retry. `Runtime` n'importe toujours
pas `providers.ollama` : seule `InvalidLLMResponseError` (`reasoner.py`,
contrat `Reasoner` déjà existant) lui est connue. Aucun changement à
`PolicyEngine`/`Executor`/`StateMachine`/`EventLog`/`Storage`/`Checkpoint`/
`Workspace`/`Tracer`/`CLI`, ni au contrat `LLM.generate()`, ni au format JSON
des décisions. Compteur de tests : 350 -> 355 (2 nouveaux dans
`tests/test_reasoner.py` : `LLMReasoner.decide()` traduit `OllamaRequestError`
et une exception générique en `InvalidLLMResponseError`, un seul appel à
`generate()` ; 3 nouveaux dans `tests/test_runtime.py` :
`InvalidLLMResponseError` directe échoue la Mission sans crash ni faux
`DECISION_RECEIVED`/`POLICY_EVALUATED`, bout en bout `LLMReasoner` + `LLM` qui
échoue comme `OllamaLLM` (`OllamaRequestError`) échoue la Mission sans crash,
historique préservé (action réussie puis échec LLM au tour suivant)), zéro
régression sur les 350 tests préexistants (aucun modifié).

Mise à jour ultérieure (Persistance disque du Checkpoint) : `storage.py`
gagne `FileStorage(Storage)`, nouvelle implémentation concrète à côté
d'`InMemoryStorage` (non modifiée) — persiste uniquement le `Checkpoint` sur
disque en JSON (`Checkpoint.model_dump_json()`/`model_validate_json()`, déjà
prévu par le modèle depuis la Phase 1), à un chemin fourni explicitement au
constructeur. `save_events`/`load_events` délèguent à un `InMemoryStorage`
interne : les événements restent en mémoire, la persistance de l'`EventLog`
restant hors périmètre de cette phase (voir section 7). Écriture atomique
volontairement simple (`tempfile.mkstemp()` dans le même répertoire que la
cible, puis `os.replace()`), répertoire parent créé si besoin (`mkdir(parents=True,
exist_ok=True)`). `load_checkpoint()` retourne `None` si le fichier est
absent ; un fichier présent mais invalide (JSON malformé ou incompatible
avec le modèle `Checkpoint`) lève `CorruptedCheckpointError` (nouvelle
exception dédiée de `storage.py`, même convention que
`StorageNotConfiguredError`/`UnknownConfirmationRequestError` ailleurs dans
le projet) plutôt que d'échouer silencieusement ou de renvoyer `None` — un
seul bloc `except pydantic.ValidationError` suffit à couvrir les deux cas
(JSON malformé et schéma incompatible), `model_validate_json()` de Pydantic
v2 les levant tous deux comme `ValidationError`. Mono-mission comme
`InMemoryStorage` : chaque `save_checkpoint()` remplace le fichier
précédent, jamais d'accumulation. `cli.py` : `cli._storage` passe
d'`InMemoryStorage()` à `FileStorage(_DEFAULT_CHECKPOINT_PATH)`, avec
`_DEFAULT_CHECKPOINT_PATH = Path.home() / ".peon" / "checkpoint.json"` —
convention simple et déterministe, choisie côté `cli.py` (comme les autres
constantes `_DEFAULT_*` existantes), pas de `.env` ni de nouvelle couche de
configuration, aucune dépendance externe (stdlib + `pydantic`, déjà présent).
`peon resume` retrouve donc désormais un Checkpoint après un vrai
redémarrage du process `peon`, pas seulement dans le même process (ancienne
limite documentée, corrigée dans `ARCHITECTURE.md`/`README.md` par cette même
mise à jour). Aucun changement à `save_events()`/`load_events()` eux-mêmes,
à `EventLog`, ni au contrat abstrait `Storage` (`save_checkpoint`/
`load_checkpoint` déjà déclarés depuis la Phase 1). Compteur de tests :
355 -> 364 (9 nouveaux : 7 dans `tests/test_storage.py` couvrant
`FileStorage` — round-trip via une nouvelle instance sur le même fichier,
fichier absent -> `None`, JSON invalide -> `CorruptedCheckpointError`,
contenu écrit JSON valide et fidèle, deuxième `save_checkpoint()` qui
remplace le premier sans laisser de fichier temporaire, création du
répertoire parent manquant, événements qui restent isolés entre deux
`FileStorage` sur le même fichier ; 1 dans `tests/test_checkpoint.py` —
scénario `Runtime` A/B avec deux instances `FileStorage` distinctes pointant
vers le même fichier, sans état Python partagé, jusqu'à la reprise de
confirmation ; 1 dans `tests/test_cli.py` — `peon resume` retrouve réellement
un Checkpoint écrit par une première instance de `FileStorage` puis relu par
une seconde, via `CliRunner`/`tmp_path`, sans dépendre d'un serveur Ollama),
zéro régression sur les 355 tests préexistants (aucun modifié).

Mise à jour ultérieure (wiring de la restriction de Workspace/chemins) :
`PathRestrictionRule` (Phase 3) existait mais n'était configurée par aucun
appelant réel (voir section 5, point 19, et section 7 avant cette mise à
jour) — désormais réellement branchable de bout en bout. `build_runtime()`
(`composition.py`) gagne `workspace_root: Path | str | None = None`, simple
pass-through vers `PolicyEngine(registry, workspace_root=...)` (déjà
existant, non modifié) : `build_runtime` ne résout ni n'interprète ce chemin
lui-même. `cli.py` gagne `--workspace-root` sur `peon run` et `peon resume`
(`Path | None`, `None` par défaut), normalisé par `Path.resolve()` à la
frontière CLI (`_normalize_workspace_root()`, nouvelle fonction privée)
avant d'atteindre `build_runtime()` — `PathRestrictionRule.__init__()`
résout de nouveau ce chemin en interne, la normalisation côté CLI est donc
idempotente, jamais une deuxième source de vérité sur la racine réellement
appliquée. `--workspace-root` n'est pas persistée dans le `Checkpoint` :
`peon resume` doit la refournir explicitement pour qu'elle s'applique aux
Actions de la boucle de raisonnement reprise (l'Action déjà confirmée au
moment du `save_checkpoint()` s'exécute sans repasser par le Policy Engine,
comportement de `Runtime.resume_confirmation()` inchangé, non spécifique à
`workspace_root`). Comportement de sécurité vérifié explicitement par test à
cette occasion (`tests/test_guardrails.py`) plutôt que seulement supposé
correct : chemin inexistant (`Path.resolve()` ne requiert pas l'existence),
répertoire frère partageant un préfixe de nom (`root-evil` à côté de
`root` — pas de confusion, la règle compare via `Path.parents`, jamais un
`str.startswith()`), casse et séparateurs `/`/`\` sur Windows
(`WindowsPath`, comportement natif à `pathlib`, pas ajouté par cette règle),
jonction NTFS créée à l'intérieur de la racine mais pointant vers une cible
extérieure (`Path.resolve()` suit la jonction jusqu'à sa cible réelle avant
le containment check — déjà correct, maintenant testé). Aucun changement à
`policy.py`/`guardrails.py` eux-mêmes : `PolicyEngine` acceptait déjà
`workspace_root` en paramètre de construction, seul le chemin depuis
`composition.py`/`cli.py` jusqu'à lui manquait. Aucun changement à
`Workspace`/`LocalWorkspace`, `Runtime`, `EventLog`, `Storage`/`Checkpoint`,
`StateMachine`, `Tracer`. Compteur de tests : 364 -> 377 (13 nouveaux : 3
dans `tests/test_composition.py` — `build_runtime()` sans `workspace_root`
ne restreint toujours rien, avec `workspace_root` autorise un chemin dans la
racine et refuse un chemin hors racine, bout en bout via `runtime.run()`
avec un `ReadFileTool`/`LocalWorkspace` réel ; 4 dans `tests/test_cli.py` —
`peon run` sans/avec `--workspace-root` (chemin autorisé, chemin refusé), et
`peon resume --workspace-root` qui refuse une nouvelle Action hors racine
pendant la boucle reprise tout en laissant s'exécuter l'Action déjà
confirmée du `Checkpoint` ; 5 dans `tests/test_guardrails.py` — chemin
inexistant dans/hors racine, répertoire frère à préfixe partagé, casse et
séparateurs Windows, jonction NTFS qui échappe à la racine (ces deux
derniers `skipif` hors Windows) ; 1 dans `tests/test_policy.py` —
`PathRestrictionRule` garde la priorité sur `RiskLevelRule` générique dans
la chaîne par défaut), zéro régression sur les 364 tests préexistants (aucun
modifié).

Mise à jour ultérieure (Robustesse exécution Shell / encodage) : audit ciblé
de la chaîne `ShellTool -> Workspace -> LocalWorkspace -> subprocess`,
déclenché par un problème latent identifié lors de tests réels avec Ollama
sur Windows — `subprocess.run(..., text=True)` décode `stdout`/`stderr` avec
l'encodage préféré du système (`locale.getpreferredencoding`, `errors`
implicitement `"strict"`) : une sortie shell produisant des octets invalides
pour cet encodage aurait fait lever `UnicodeDecodeError` par
`LocalWorkspace.run_command()`, non capturée par `ShellTool.execute()` (qui
ne capture que `OSError`) et donc remontée brute jusqu'au `Runtime` — pas
reproduit dans cet environnement au moment de l'audit (l'encodage préféré
constaté était `cp1252`, qui accepte la quasi-totalité des octets), mais
identifié comme d'autant plus probable avec le mode UTF-8 par défaut des
versions récentes de Python (PEP 686), où `errors="strict"` rejette beaucoup
plus d'octets. Correctif contenu entièrement dans
`LocalWorkspace.run_command()` (`workspace.py`) : `subprocess.run(...,
encoding="utf-8", errors="replace")` explicite plutôt qu'implicite —
`encoding="utf-8"` aligne le décodage de la sortie shell sur la convention
déjà utilisée ailleurs dans le projet (`read_file`, `storage.py`), et
`errors="replace"` remplace les octets invalides par `U+FFFD` au lieu de
lever `UnicodeDecodeError`. Décision architecturale : la responsabilité du
décodage reste dans `Workspace` (l'I/O technique), pas remontée dans
`ShellTool` (qui garde uniquement `OSError` à capturer, comme avant) — une
sortie shell mal encodée devient un cas normal plutôt qu'une exception,
cohérent avec le principe déjà en place que `Workspace` ne doit jamais faire
crasher un Tool pour un aléa de bas niveau qu'il peut absorber lui-même sans
perdre d'information utile (`return_code` inchangé, `stdout`/`stderr`
toujours des `str`). Contrats `Workspace.run_command() -> CommandResult` et
`Tool.execute() -> ToolResult` strictement inchangés ; `read_file` continue
de lever `UnicodeDecodeError` (comportement volontairement différent,
`ReadFileTool` la capture déjà explicitement — voir `ARCHITECTURE.md`).
Aucun changement à `Runtime`/`PolicyEngine`/`Executor`/`Reasoner`/`LLM`.
Compteur de tests : 377 -> 382 (4 nouveaux dans `tests/test_workspace.py` —
sortie UTF-8 normale sur `stdout`/`stderr`, octets invalides remplacés sur
`stdout` seul, sur `stderr` seul, sur les deux simultanément avec
`return_code` non nul préservé, tous via un script Python minimal écrit dans
`tmp_path` pour produire des octets précis de façon portable plutôt que de
dépendre d'une commande Windows/Unix particulière ; 1 nouveau dans
`tests/tools/test_shell.py` — `ShellTool.execute()` ne lève plus pour une
sortie non-UTF-8, reproduisant le scénario de régression identifié), zéro
régression sur les 377 tests préexistants (aucun modifié).

Mise à jour ultérieure (Tool HIGH — `delete_file`) : jusqu'ici, les trois
`Tool` de production (`read_file`/`list_directory` en `LOW`, `run_command` en
`MEDIUM`) ne déclenchaient jamais `REQUIRES_CONFIRMATION` en usage réel —
limite documentée depuis la Phase 5 (voir section 5, et `ARCHITECTURE.md`/
`README.md`). Nouveau `DeleteFileTool` (`delete_file`, `tools/filesystem.py`,
`RiskLevel.HIGH`) : supprime un unique fichier, opération destructive et
irréversible mais strictement bornée à un seul fichier (jamais un dossier —
échec propre dans ce cas, pas de suppression récursive). Choix motivé par la
préférence explicite du chantier pour une opération filesystem destructive
bornée par `Workspace`/`workspace_root` plutôt qu'une surface dangereuse
nouvelle (ex. un second Tool proche de `run_command` classé `HIGH`, déjà
écarté à la Phase 2 — voir section 4, `ShellTool`). Même schéma de
paramètres que `read_file`/`list_directory` (`path: string`, requis) :
`PathRestrictionRule` et `RiskLevelRule` (`guardrails.py`) s'appliquent donc
automatiquement, sans qu'aucune règle nouvelle n'ait été écrite — la
généricité déjà en place (détection de la propriété `path` depuis le
`ToolSpec`, jamais une liste de noms de Tools codée en dur) est la preuve que
ce Tool réutilise entièrement les mécanismes existants plutôt que d'en créer
un parallèle. `Workspace` (ABC) gagne une quatrième méthode abstraite,
`delete_file(path) -> None` (`workspace.py`) ; `LocalWorkspace.delete_file()`
fait `Path(path).unlink()`, même convention que `read_file`/`list_directory`
: laisse remonter `OSError` tel quel, capturé et traduit en
`ToolResult(success=False)` par `DeleteFileTool.execute()`, jamais par
`Workspace` lui-même. `ToolResult.output`, en cas de succès, est un dict
structuré `{"path": ..., "deleted": True}` plutôt qu'une simple chaîne (les
autres Tools filesystem restent inchangés, `output` non uniformisé entre eux
— voir section 5, point 20) : choisi pour rester lisible côté `Observation`/
prompt (`PromptBuilder._render_observations()`, chantier Phase 6) sans
introduire de nouvelle convention de format. `cli.py` : `_build_tools()`
enregistre désormais `DeleteFileTool(workspace)` en plus des trois Tools
existants — premier `Tool` `HIGH` réellement disponible via `peon run`/`peon
resume`, sans qu'aucun changement n'ait été nécessaire au chemin de
confirmation lui-même (déjà implémenté et testé depuis la Phase 5 via des
`Tool` de test `HIGH`). Aucun changement à `PolicyEngine`/`guardrails.py`/
`Executor`/`StateMachine`/`Runtime`/`EventLog`/`Storage`/`Checkpoint`/
`Tracer` : tout le chemin `Action -> REQUIRES_CONFIRMATION ->
ConfirmationRequest -> accept/refuse -> exécution ou non` était déjà en
place, seul un vrai Tool `HIGH` manquait pour l'exercer en usage réel (voir
`ARCHITECTURE.md`, section **CLI**, ancienne « Limite assumée »). Compteur de
tests : 382 -> 404 (7 nouveaux dans `tests/tools/test_filesystem.py` et 2
dans `tests/test_workspace.py` — `DeleteFileTool`/`LocalWorkspace.delete_file()`
en isolation : spec `HIGH`, suppression réelle vérifiée sur disque, fichier
absent, argument manquant/invalide, dossier au lieu d'un fichier, jamais
d'exception ; 3 nouveaux dans `tests/test_policy.py` — le vrai
`DeleteFileTool` (pas un stub) enregistré dans un `ToolRegistry` réel :
`HIGH` sans `workspace_root` -> `REQUIRES_CONFIRMATION`, chemin hors racine
-> `DENIED` avant toute confirmation, chemin dans la racine ->
`REQUIRES_CONFIRMATION` ; nouveau fichier `tests/test_integration_delete_file.py`
(4 tests) — cycle complet bout en bout sans mock interne, seul module à
exercer réellement `REQUIRES_CONFIRMATION -> AWAITING_CONFIRMATION ->
resume_confirmation()` avec un Tool de production plutôt qu'un `_StubTool` de
test : le fichier cible existe toujours juste après `REQUIRES_CONFIRMATION`
(aucune exécution avant confirmation), est réellement supprimé après
acceptation, reste intact après refus, et une reprise après « crash » simulé
(deux instances `Runtime` autour d'un `Checkpoint`, même scénario que
`tests/test_checkpoint.py`) aboutit à une suppression réelle sur disque ; 2
nouveaux dans `tests/test_cli.py` — `peon run` avec le vrai
`cli._build_tools()` (non monkeypatché, contrairement au reste du module) :
confirmation affichée pour `delete_file`, fichier réellement supprimé après
acceptation, intact après refus), zéro régression sur les 382 tests
préexistants (aucun modifié).

Mise à jour ultérieure (chantier « Reprise contrôlée après erreur LLM ») :
audit préalable de `LLMReasoner.decide()` (`reasoner.py`),
`InvalidLLMResponseError`, `OllamaRequestError` (`providers/ollama.py`) et
`Runtime._run_reasoning_cycle()` — constat que les quatre catégories
d'erreur transitoire observées avec un vrai Ollama (HTTP/réseau, JSON
invalide, `message.content` absent — ce dernier cas convergeant en pratique
avec « JSON invalide », un `content` vide échouant déjà à `json.loads` —,
décision non conforme au schéma `Decision`) convergent toutes aujourd'hui
vers la même `InvalidLLMResponseError`, sans information structurelle
permettant de les redistinguer sans inventer une nouvelle hiérarchie
d'exceptions (explicitement hors périmètre de ce chantier). Décision : retry
automatique très borné (option A), pas de reprise explicite via `Checkpoint`
(option B/C) — un `Checkpoint` n'est aujourd'hui sauvegardé par la CLI qu'au
moment d'`AWAITING_CONFIRMATION`, jamais lors d'une panne de raisonnement, et
le rendre utile pour ce cas demanderait de lui faire porter l'historique
(`EventLog`/`Observation`), c'est-à-dire une refonte du `Checkpoint`
explicitement exclue de ce chantier.

Nouvelle classe `RetryingReasoner` (`reasoner.py`), décorateur de `Reasoner`
— pas une variante de `LLMReasoner` — qui enveloppe n'importe quel
`Reasoner` et retente `decide()` un nombre borné de fois
(`max_attempts`, défaut 2, aucune boucle infinie, aucun backoff) uniquement
sur `InvalidLLMResponseError` ; toute autre exception remonte immédiatement
sans retry (bug d'implémentation à ne jamais masquer). Couche choisie
précisément parce qu'elle est la seule à voir à la fois le contrat
`Context -> Decision` et l'exception déjà normalisée par `reasoner.py`, sans
jamais avoir besoin de connaître un fournisseur concret : `Runtime` continue
d'appeler `reasoner.decide(context)` une seule fois par cycle et ignore
totalement qu'une reprise a eu lieu, `OllamaLLM`/`LLMReasoner`/`CLI` ne
changent pas. Garantie de sécurité centrale : le point où `decide()` est
appelé précède toujours la construction d'une `Action` dans
`Runtime._run_reasoning_cycle()` — aucune Action ni aucun Tool n'est donc
jamais rejoué par ce mécanisme, prouvé par des tests dédiés (compteur
d'exécutions d'un Tool resté à 1 malgré des tentatives de reasoning
multiples, y compris juste après une Action déjà exécutée avec succès).
`Tracer` optionnel sur `RetryingReasoner` (même convention que `Runtime`,
défaut `NoOpTracer`), un span `reasoner.decide.attempt` par tentative :
distingue les tentatives sans toucher à l'`EventLog` ni ajouter de nouvel
`EventType` métier. `build_runtime()` (`composition.py`) enveloppe désormais
toujours `LLMReasoner` dans un `RetryingReasoner` par défaut
(`reasoner_max_attempts`, `tracer` : nouveaux paramètres optionnels de pur
câblage, `tracer` transmis identique à `Runtime` et à `RetryingReasoner`
pour partager le même `Tracer`) ; `cli.py` inchangé, bénéficie de la reprise
sans nouvelle option. Validation réelle Ollama (deux modèles, dont un à
raisonnement visible) : comportement nominal confirmé de bout en bout
(1 tentative, aucun retry nécessaire) ; reproduire une réponse invalide/
transitoire sans manipulation fragile n'a pas été obtenu (les deux modèles
ont respecté le contrat JSON dès le premier essai) — ces chemins restent
couverts de façon déterministe par les tests avec faux serveur HTTP/LLM
stub. Compteur de tests : 404 -> 421 (9 nouveaux dans `tests/test_reasoner.py`
pour `RetryingReasoner` en isolation, 5 dans `tests/test_runtime.py` pour le
comportement observé depuis `Runtime`, 3 dans `tests/test_composition.py`
pour le câblage par défaut), zéro régression sur les 404 tests préexistants.

Mise à jour ultérieure (chantier « reprise durable avec historique ») :
audit préalable confirmant que `ContextBuilder.build_from_event_log()` et
`Runtime._run_reasoning_cycle()` reconstruisaient déjà le `Context` depuis
l'`EventLog` (pas depuis une liste d'`Observation` en mémoire) — le seul
maillon manquant pour une reprise vraiment durable était que `FileStorage`
ne persistait pas les événements eux-mêmes (`save_events`/`load_events`
délégués à un `InMemoryStorage` interne, jamais écrits sur disque) et que
`persist_events()` dupliquait tout l'historique déjà envoyé s'il était
rappelé plus d'une fois par session. Décision (option B du chantier,
détaillée en tête de ce document/dans `ARCHITECTURE.md`) : compléter
`Storage`/`FileStorage` plutôt que faire porter l'historique par
`Checkpoint` (option A, rejetée — duplication d'état, coût croissant à
chaque `save_checkpoint()`, contredit la séparation `Checkpoint` volontaire
documentée point 25/26 ci-dessus). Changements : `FileStorage` persiste
désormais l'`EventLog` dans un fichier JSON Lines séparé
(`<checkpoint>.events.jsonl`), réécrit atomiquement à chaque
`save_events()` (append-only côté contrat, pas côté fichier — tempfile +
`os.replace()`, comme `save_checkpoint()`) ; `CorruptedEventLogError`
nouvelle exception, même famille que `CorruptedCheckpointError`. `Runtime`
suit désormais `self._persisted_event_count` pour que `persist_events()`
n'envoie que le delta. `Runtime.resume_mission()` reconstruit
`self._observations` depuis `self._event_log` via
`ContextBuilder.build_from_event_log()` au lieu de le vider
inconditionnellement — sans effet si l'`EventLog` injecté est vierge (cas
par défaut, aucune régression sur les tests existants), mais restaure
l'historique complet si l'appelant l'a préalablement chargé via
`Runtime.load_event_log(storage)`. `build_runtime()` (`composition.py`)
accepte un `event_log` optionnel pour ce câblage ; `cli.py::resume` l'utilise
(`Runtime.load_event_log(_storage)` avant de construire le Runtime) et
`cli.py::_drive_to_completion` appelle `persist_events()` juste après chaque
`save_checkpoint()`, pour qu'un Checkpoint ne soit jamais sauvegardé sans
l'historique qui lui correspond. Aucune nouvelle boucle de raisonnement,
aucun changement à `Executor`/`PolicyEngine`/`StateMachine`/`Workspace`/
`Tracer`/aux Tools existants, aucune dépendance SQLite ni multi-mission.
Compteur de tests : 421 -> 435 (14 nouveaux : 6 dans `tests/test_storage.py`
pour la persistance/append-only/isolation/corruption de `FileStorage`,
1 dans `tests/test_runtime_storage.py` pour l'absence de duplication de
`persist_events()`, 7 dans le nouveau `tests/test_resume_history.py` — les
trois cas fonctionnels du chantier avec `InMemoryStorage`/`FileStorage`, un
round-trip complet, l'absence de duplication en session façon CLI, un
checkpoint absent, des événements corrompus —, et 1 test bout-en-bout à deux
process Python distincts partageant uniquement le disque), zéro régression
sur les 421 tests préexistants (un seul modifié :
`test_file_storage_events_stay_in_memory_only`, qui documentait l'ancien
comportement désormais délibérément changé, remplacé par des tests
équivalents pour le nouveau comportement).

Mise à jour ultérieure (audit de consolidation avant commit, aucune nouvelle
phase fonctionnelle) : relecture croisée des chantiers ci-dessus tels
qu'implémentés dans le working tree (pas seulement tels que documentés).
Deux problèmes réels trouvés et corrigés, tous deux dans `cli.py`, aucun
dans `runtime.py`/`storage.py`/le cœur déjà largement testé aux sections
précédentes : (1) `_drive_to_completion()` ne persistait `Checkpoint`/
`EventLog` qu'en entrant dans la boucle d'attente de confirmation, jamais
après l'avoir quittée — une Mission terminée (avec ou sans confirmation)
laissait un `Checkpoint` obsolète sur disque, encore marqué en attente
d'une confirmation déjà résolue en mémoire ; un `peon resume` ultérieur la
retrouvait telle quelle et ré-exécutait l'Action `HIGH` correspondante une
deuxième fois — violation reproduite (`tool.call_count` passant de 1 à 2
sur deux invocations CLI successives) avant correction, reproduite d'abord
en test avant d'être corrigée, comme demandé. Corrigé en persistant aussi
l'état final après la boucle, quel que soit le nombre de tours effectués.
(2) `peon resume` ne capturait `CorruptedCheckpointError`/
`CorruptedEventLogError` nulle part : un fichier corrompu remontait comme
trace Python brute plutôt que le message dédié déjà utilisé pour
`No checkpoint found.`. Corrigé par deux `except` dédiés. Reste de l'audit
(architecture, retry, guardrails, prompt/Observation) : aucune anomalie
trouvée, déjà couvert par les tests existants — voir aussi la correction de
deux affirmations devenues fausses dans `ARCHITECTURE.md` (section CLI, qui
décrivait encore un `EventLog` non persisté au redémarrage, contredisant sa
propre section **Storage** juste au-dessus) et `README.md` (erreurs réseau
Ollama présentées comme non gérées, alors que `RetryingReasoner`/
`Runtime._fail_mission_on_reasoning_error()` les gèrent depuis le chantier
retry ; compteur de tests obsolète). Complété par 3 tests d'intégration
couvrant des interactions entre chantiers qui n'étaient pas encore
exercées : `_build_llm` (`cli.py`) relié à un vrai `OllamaLLM` fonctionnel
via `--base-url`/`--timeout-seconds` (et aux valeurs par défaut sans
option), `RetryingReasoner` qui récupère toujours d'une panne LLM
transitoire après une reprise `build_runtime()` + historique persisté
(`FileStorage`) — les deux seules interactions de la liste d'audit qui
n'étaient pas déjà couvertes ; les quatre autres (`FileStorage`+`EventLog`+
`Checkpoint`, `Runtime`+resume+historique, Tool `HIGH`+Checkpoint+
historique+confirmation, `--workspace-root`+`DeleteFileTool`) l'étaient
déjà. Compteur de tests : 435 -> 441 (6 nouveaux : 3 dans
`tests/test_cli.py` pour les deux bugs, 2 dans `tests/test_cli.py` pour le
câblage Ollama, 1 dans `tests/test_resume_history.py` pour le retry après
reprise), zéro régression sur les 435 tests préexistants.

## 7. État actuel — travail restant identifié

Pas de phase suivante choisie ici — cette section décrit uniquement ce qui
reste absent aujourd'hui, sans engager de priorité :

- ~~Backend `Storage` persistant sur disque pour l'**EventLog**~~ — résolu
  (chantier « reprise durable avec historique ») : `FileStorage` persiste
  désormais l'`EventLog` (JSON Lines, fichier séparé du Checkpoint) et
  `persist_events()` suit ce qui a déjà été envoyé pour ne jamais dupliquer.
  Reste absent : un backend transactionnel (SQLite ou autre) qui éviterait la
  réécriture complète du fichier à chaque `save_events()` — pas un prérequis
  pour la reprise, seulement une optimisation possible.
- ~~Reprise de Mission après arrêt du process : restauration de l'`EventLog`/
  des `Observation` absente~~ — résolu : `Runtime.resume_mission()`
  reconstruit désormais `self._observations` depuis l'`EventLog` injecté
  (via `ContextBuilder.build_from_event_log()`, réutilisé tel quel) ; si cet
  `EventLog` a été chargé au préalable depuis `Storage`
  (`Runtime.load_event_log()`, câblé par `cli.py::resume`), le `Context`
  reconstruit après reprise est identique à celui qu'aurait eu le process
  d'origine au même point — voir `ARCHITECTURE.md` (section **Storage**) et
  `tests/test_resume_history.py`. Reste absent : reprise multi-mission
  (toujours hors périmètre, non demandée par ce chantier).
- Tools concrets restants : `git.py`, `search.py`. Le chemin de confirmation
  (section 6, Phase 5) n'est plus limité à des `Tool` de test : `delete_file`
  (`RiskLevel.HIGH`, chantier « Tool HIGH — delete_file ») le déclenche
  désormais réellement via `peon run`/`peon resume` — reste ouvert :
  `delete_file` est borné à un seul fichier, aucun Tool ne couvre encore une
  opération destructive sur un dossier ou un dépôt git (`git push`, etc.).
- CLI au-delà de `run`/`resume` (Phase 5, terminée - voir section 4 et 6) :
  pas de commande d'inspection d'un Checkpoint sans le reprendre.
  `--workspace-root` existe désormais sur les deux commandes (voir point
  suivant) mais n'est pas persistée dans le `Checkpoint` : `peon resume`
  doit la refournir explicitement pour que la restriction reste active sur
  la suite du raisonnement repris (l'Action déjà confirmée au moment du
  `save_checkpoint()`, elle, s'exécute sans repasser par le Policy Engine -
  voir `Runtime.resume_confirmation()`). Une erreur LLM/Ollama fait
  désormais échouer la Mission proprement (`FAILED`, voir section 6,
  Robustesse LLM/Ollama) au lieu de crasher le process — la CLI n'a
  toutefois pas de message dédié au-delà de ce qu'elle affiche déjà pour une
  Mission `FAILED`.
- Sécurité au-delà de ce que couvre la Phase 3 (Policy/Guardrails, terminée -
  voir section 4 et 6) : `Workspace`/`LocalWorkspace` reste une indirection
  technique pure sans sandbox ni allowlist de commandes ni timeout shell (ces
  sujets restent hors périmètre, volontairement, cf. `ARCHITECTURE.md`) ;
  `PathRestrictionRule` est maintenant réellement branchée (`workspace_root`
  exposé par `build_runtime()` et par `--workspace-root` sur `cli.py`,
  `None` par défaut des deux côtés) ; `ToolAuthorizationRule` ne distingue
  pas « autorisé » de « enregistré » (pas de rôles/permissions par utilisateur ou
  mode d'exécution, voir section 5, point 28). Toute évolution en ce sens
  (ex. `DockerWorkspace`/`RemoteWorkspace`, autorisation plus fine que
  l'appartenance au `ToolRegistry`) reste à concevoir.
- Adaptateur OpenTelemetry pour `tracing.py` (Phase 4) : le port `Tracer`/
  `Span` est pensé pour ça, mais aucun adaptateur concret n'existe — seul
  `NoOpTracer` est fourni. Métriques de tokens toujours absentes : bloquées
  en amont par `LLM.generate()`/`Reasoner.decide()`, qui n'exposent aucune
  information d'usage aujourd'hui ; à résoudre côté ces contrats avant de
  pouvoir les faire remonter dans un span sans les fabriquer.

Une nouvelle session doit **demander à l'utilisateur** quelle est la
prochaine étape plutôt que d'en choisir une — conformément à la méthode de
travail validée (section 1) : chaque phase est explicitement commandée, une à
la fois, jamais anticipée.

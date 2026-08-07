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
| **Context** | Donnée immuable remise au Reasoner : objectif, statut, itération, observations, outils disponibles. | ✅ implémenté (`models/context.py`) |
| **Context Builder** | Seul point de passage entre Event Log, Tool Registry et Reasoner : sélectionne les événements pertinents, décrit les outils disponibles. Ne raisonne jamais, n'assemble aucun prompt (voir PromptBuilder). Deux points d'entrée : `build(observations=...)` et `build_from_event_log(event_log=...)` (reconstruit les `Observation` depuis les événements `OBSERVATION_PRODUCED`, utilisé par le Runtime). | ✅ implémenté (`context_builder.py`) |
| **Reasoner** (ex-Planner) | Reçoit un `Context`, appelle le LLM, retourne une `Decision`. Ne construit jamais son propre contexte, ne devine jamais les outils disponibles. | ✅ implémenté : ABC (`reasoner.py`) + implémentation concrète `LLMReasoner` |
| **LLM** | Abstraction pure d'un fournisseur de modèle : `generate(messages) -> str`. | ✅ contrat implémenté (`llm.py`) — **aucun client concret branché** (Ollama/OpenAI/...) |
| **PromptBuilder** | Transforme un `Context` en messages LLM (`Context -> list[dict[str, str]]`), déterministe, sans logique métier. | ✅ implémenté (`prompts.py`) |
| **State Machine** | Autorité unique de transition, fonction pure `(état, événement) -> état`. Ne consulte jamais elle-même le Policy Engine — le Verdict lui est fourni comme donnée de l'événement. | ✅ implémenté (`state_machine.py`) |
| **Policy Engine** | Fonction pure `(Action) -> Verdict`. Consulte le Tool Registry (métadonnées uniquement). Détecte les commandes dangereuses, déclenche la confirmation. | ✅ implémenté (`policy.py`) |
| **Executor** | Exécute une Action déjà validée via le Tool résolu par le Tool Registry ; convertit les échecs en `ExecutionError`. Ne parle jamais au LLM ni au Policy Engine. | ✅ implémenté (`executor.py`) |
| **Tool Registry** | Source de vérité unique sur les outils disponibles (instances `Tool` exécutables, pas seulement leur description). | ✅ implémenté (`tool_registry.py`) |
| **Tool** | Contrat d'une capacité atomique : `spec` (ToolSpec) + `execute(arguments) -> ToolResult`. | ✅ contrat implémenté (`tools/base.py`) — implémentations concrètes : `ReadFileTool` (`read_file`, `LOW`), `ListDirectoryTool` (`list_directory`, `LOW`), dans `tools/filesystem.py` ; `ShellTool` (`run_command`, `MEDIUM`), dans `tools/shell.py` ; **restent à faire** `git.py`/`search.py` |
| **Observation** | Modèle plat (`kind` + `summary` + `details`), sans dépendance vers ToolResult/ExecutionError/Verdict ni aucun composant — la traduction elle-même reste une responsabilité du Runtime. Le Reasoner ne voit jamais un ToolResult brut. | ✅ implémenté (`models/observation.py`), produite en conditions réelles par le `Runtime` |
| **Event Log** | Journal append-only en mémoire pendant l'exécution (`append`, `list_events`, `list_events_by_type`), zéro dépendance vers Storage. | ✅ implémenté (`event_log.py`) |
| **Storage** | Abstraction `save_events`/`load_events` (ABC), zéro dépendance vers Event Log ni logique métier. | ✅ abstraction + `InMemoryStorage` implémentées (`storage.py`) — **aucun backend disque** (SQLite ou autre) pour l'instant |
| **Runtime** | Seul composant impur : orchestre tous les appels (Context Builder, Reasoner, Policy Engine, Executor), écrit dans l'Event Log, consomme un `ConfirmationResponse` (`resume_confirmation`). | ✅ implémenté (`runtime.py`) — persistance vers `Storage` optionnelle et explicite (`persist_events()`/`load_event_log()`), jamais automatique |
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
de ce mécanisme.

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
  Non encore connectée au Runtime : ce dernier ne sait pas encore réagir à
  cette exception (cf. section 5).
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
8. Qui valide `Action.arguments` contre `ToolSpec.parameters_schema` ? Le Tool
   lui-même, l'Executor en amont, les deux ? Non tranché.
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
19. Aucune restriction de chemin dans `ReadFileTool`/`ListDirectoryTool`
    (volontaire, hors périmètre de leur phase respective) : un futur Policy
    Engine devra décider s'il borne les chemins accessibles (sandbox projet,
    refus des chemins absolus/`~`...), sans que cela ne remonte dans le Tool
    lui-même — non conçu, non implémenté.
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
24. `Runtime.persist_events()` fait un instantané complet de l'Event Log à
    chaque appel, sans suivi de ce qui a déjà été persisté : l'appeler deux
    fois duplique dans `Storage`. Pas de branchement automatique dans la
    boucle de raisonnement (delibérément hors périmètre de la phase Storage).
25. Recharger un `EventLog` depuis `Storage` (`Runtime.load_event_log`) ne
    restaure ni la `Mission` (statut, compteur d'itérations) ni la
    `StateMachine` ni une `ConfirmationRequest` en attente — seul l'historique
    brut des événements est reconstructible. Une vraie « reprise de mission
    après arrêt du process » reste à concevoir.
26. `Storage` n'a qu'une implémentation en mémoire (`InMemoryStorage`) : malgré
    l'abstraction en place, une Mission ne survit pas encore réellement à un
    arrêt du process. Un backend disque (SQLite ou autre) reste à écrire
    derrière la même interface `Storage`.
27. `Runtime` ne retient qu'une seule `ConfirmationRequest` en attente à la
    fois (`self._pending_confirmation`, réinitialisée à chaque `run()`) :
    suffisant pour une Mission à la fois, pas conçu pour plusieurs Missions
    concurrentes sur un même `Runtime`.

## 6. État actuel d'implémentation

**Implémenté et testé (263 tests, tous verts) :**

```
src/peon/
  __init__.py, cli.py                    # scaffold Phase 0 (peon --version)
  models/
    mission.py, context.py, decision.py, action.py,
    tool_spec.py, tool_result.py, execution_error.py, verdict.py,
    confirmation.py, events.py, observation.py
  tools/
    base.py                              # contrat Tool (ABC)
    filesystem.py                        # ReadFileTool (read_file), ListDirectoryTool (list_directory)
    shell.py                             # ShellTool (run_command, RiskLevel.MEDIUM)
  tool_registry.py                       # enregistre des Tool, pas seulement des ToolSpec
  executor.py
  policy.py
  state_machine.py                       # fonction pure transition(), evenements dedies
  event_log.py                           # journal append-only en memoire (Event/EventType)
  context_builder.py                     # build() et build_from_event_log() -> Context
  reasoner.py                            # ABC Reasoner + LLMReasoner (implementation concrete)
  llm.py                                 # ABC LLM, aucun client concret
  prompts.py                             # PromptBuilder : Context -> messages LLM
  storage.py                             # ABC Storage + InMemoryStorage (save_events/load_events)
  runtime.py                             # orchestrateur : assemble tous les composants ci-dessus,
                                          # + resume_confirmation, persist_events, load_event_log
```

Le cycle complet `Mission -> Context -> Reasoner -> Decision -> Policy Engine
-> Executor -> Tool -> ToolResult -> Observation -> Event Log` est exercé de
bout en bout par des tests d'intégration sans mock interne
(`tests/test_integration_read_file.py`,
`tests/test_integration_list_directory.py`,
`tests/test_integration_run_command.py`), avec de vrais `Tool` (I/O disque et
sous-processus réels) et un Reasoner stub déterministe (pas de LLM branché).
La boucle de confirmation humaine complète (`tests/test_confirmation_flow.py`)
et le round-trip `EventLog -> Storage -> reload -> ContextBuilder`
(`tests/test_runtime_storage.py`) sont également exercés de bout en bout.

**Non implémenté :** un backend `Storage` persistant sur disque (SQLite ou
autre) derrière l'abstraction déjà en place, `tools/git.py`, `tools/search.py`,
tout fournisseur `LLM` concret (Ollama/OpenAI/...) derrière l'abstraction
`llm.py`, et toute CLI interactive capable de produire un `ConfirmationResponse`
réel (le mécanisme de reprise lui-même, `Runtime.resume_confirmation`, est
implémenté et testé — voir section 4).

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

## 7. État actuel — travail restant identifié

Pas de phase suivante choisie ici — cette section décrit uniquement ce qui
reste absent aujourd'hui, sans engager de priorité :

- Backend `Storage` persistant sur disque (SQLite ou autre) derrière
  l'abstraction déjà en place ; suivi incrémental de `persist_events()` pour
  éviter la duplication en cas d'appels multiples.
- Reprise complète d'une Mission après arrêt du process (restauration de la
  `Mission`/`StateMachine`/`ConfirmationRequest` en attente depuis un
  `EventLog` rechargé, pas seulement l'historique brut des événements).
- Tools concrets restants : `git.py`, `search.py`.
- Un fournisseur `LLM` concret (Ollama, a minima, cohérent avec la stack
  documentée en section 1) derrière l'abstraction `llm.py`.
- Connexion de `LLMReasoner`/`InvalidLLMResponseError` au Runtime.
- CLI interactive capable de produire un vrai `ConfirmationResponse`
  utilisateur (le mécanisme de reprise côté Runtime, lui, est déjà implémenté
  et testé).
- CLI au-delà de `peon --version`.

Une nouvelle session doit **demander à l'utilisateur** quelle est la
prochaine étape plutôt que d'en choisir une — conformément à la méthode de
travail validée (section 1) : chaque phase est explicitement commandée, une à
la fois, jamais anticipée.

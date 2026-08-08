# Péon — Architecture

## Philosophie

Le LLM ne pilote jamais le runtime. Il est une fonction pure : `Context -> Decision`.
Toute décision doit ensuite franchir une transition validée par la **State Machine**
avant de produire le moindre effet de bord. Le **Runtime** est le seul composant
impur (celui qui fait vraiment de l'I/O) : c'est lui qui orchestre les appels aux
autres composants, y compris au **Policy Engine**, dont il transmet ensuite le
`Verdict` déjà calculé à la **State Machine**. Cette dernière reste une fonction
de transition pure — `(État, Événement) -> État` — sans jamais consulter
elle-même le Policy Engine, lui-même pur par ailleurs. Cette distinction
pur/impur est volontaire : elle rend la logique de sécurité et de transition
testable sans mock d'I/O, de LLM ou de process externe.

## Composants

### Runtime
Seul composant impur (orchestrateur), implémenté (`runtime.py`). À chaque
cycle, appelle dans l'ordre : **Context Builder** (reconstruit le `Context`
depuis l'**Event Log**), **Reasoner** (`Context -> Decision`), **Policy
Engine** (`Action -> Verdict`), **State Machine** (transition pure, `Verdict`
fourni en donnée) puis, si `ALLOWED`, l'**Executor** (exécute l'Action déjà
validée) — et écrit chaque étape dans l'**Event Log**. Persiste
optionnellement vers un **Storage** injecté au constructeur
(`persist_events()`, instantané complet à la demande) et peut reconstruire un
`EventLog` depuis un `Storage` (`load_event_log()`, méthode statique). Expose
`resume_confirmation(mission, response)` : c'est l'unique consommateur d'un
`ConfirmationResponse` externe — il vérifie que la réponse correspond à la
`ConfirmationRequest` et à la `Mission` en attente, puis reprend l'action
suspendue (exécution via l'Executor si acceptée, retour au raisonnement avec
une `Observation` si refusée). `Storage` reste un collaborateur injecté,
jamais un singleton global : le Runtime fonctionne sans lui, la persistance
étant simplement indisponible dans ce cas.

Expose également `save_checkpoint(mission)` (instantané explicite d'un
**Checkpoint** vers `Storage`, à la demande — même philosophie que
`persist_events()`, jamais automatique) et `resume_mission(checkpoint)` : un
**nouveau** Runtime (nouvel `EventLog`, potentiellement vierge) restaure ainsi
la `Mission` et l'éventuelle `ConfirmationRequest` en attente d'un
`Checkpoint` chargé, sans rejouer l'historique — juste assez d'état pour que
`resume_confirmation()` fonctionne normalement ensuite. Aucune duplication de
la boucle de raisonnement : ce mécanisme réutilise `resume_confirmation()` et
les transitions déjà existantes.

### Mission
Unité de travail de haut niveau : objectif utilisateur, statut de cycle de vie
(`en cours` / `succès` / `échec` / `max itérations`), compteur d'itérations et
limite max. Donnée pure — pas de comportement propre. Sa progression est décidée
et écrite exclusivement par la State Machine.

### Checkpoint
Implémenté (`models/checkpoint.py`). Instantané composable — compose une
`Mission` et une éventuelle `ConfirmationRequest` en attente plutôt que de
dupliquer leurs champs — suffisant pour reconstruire l'état nécessaire à une
reprise après arrêt/crash du process, dans le cas ciblé par cette phase :
`action -> REQUIRES_CONFIRMATION`. Ne porte ni l'`EventLog` ni les
`Observation` (pas de persistance automatique des événements dans le
checkpoint, pas d'event-sourcing/replay complet dans cette phase — hors
périmètre, voir **Hors périmètre du MVP**). Modèle Pydantic sérialisable
(`model_dump_json()`/`model_validate_json()`), `frozen=True` au niveau du
`Checkpoint` lui-même (il représente un instantané déjà pris, jamais
recalculé après coup — même rationale que `Event`), même si la `Mission`
qu'il embarque reste mutable. Produit et consommé exclusivement par le
**Runtime** (`save_checkpoint()` / `resume_mission()`), persisté via
**Storage** (`save_checkpoint()` / `load_checkpoint()`).

### Context Builder
Seul point de passage entre l'**Event Log**, le **Tool Registry** et le
**Reasoner**. Responsable de : sélectionner les événements pertinents, choisir
les fichiers à fournir, interroger le Tool Registry pour décrire les outils
disponibles au Reasoner, tronquer ou résumer si nécessaire, gérer le budget de
tokens. Ne raisonne pas, ne décide rien, n'assemble aucun prompt — construction
déterministe/heuristique d'un `Context` (donnée), distinct du prompt LLM
proprement dit (cette traduction `Context -> messages` est portée par
`PromptBuilder`, voir **Reasoner** ci-dessous) — **ContextBuilder ne construit
jamais de prompt**. Deux points d'entrée : `build(observations=...)`
(construit un `Context` à partir d'une liste d'`Observation` déjà en main) et
`build_from_event_log(event_log=...)` (reconstruit les `Observation`
directement depuis les événements `OBSERVATION_PRODUCED` de l'**Event Log**,
dans leur ordre d'apparition — voir **Event Log** ci-dessous). Le Runtime
utilise exclusivement la seconde forme dans sa boucle de raisonnement ; la
première reste utile de façon autonome (tests, composition manuelle). Le
Reasoner n'a **aucun accès direct** à la Mission, à l'Event Log ni au Tool
Registry : tout ce qu'il voit transite par le Context que ce composant lui
remet.

### Reasoner (ex-Planner)
Reçoit un `Context`, appelle le LLM, retourne une `Decision` brute et validée
structurellement. Peut, selon la situation : demander la lecture d'un fichier,
demander l'exécution d'un outil, demander une confirmation, ou signaler la fin
de la mission. Ne devine jamais quels outils existent — il ne connaît que ceux
listés dans le Context que lui a remis le Context Builder. Ne planifie pas un
enchaînement d'étapes à l'avance — une décision à la fois, informée par
l'Observation la plus récente (boucle réactive, type ReAct). Implémentation
concrète : `LLMReasoner` (`reasoner.py`), qui délègue la construction du
prompt à `PromptBuilder` (`Context -> messages`, dans `prompts.py`) et l'appel
modèle à l'abstraction `LLM` (`llm.py`). Une réponse LLM invalide (JSON
malformé, `kind` absent/inconnu, arguments incorrects) lève
`InvalidLLMResponseError` ; cette exception n'est pas encore connectée au
Runtime (voir `CONTEXT.md`, points ouverts).

### State Machine
Autorité unique de transition, fonction pure `(état, événement) -> état`,
implémentée (`state_machine.py`). Ne consulte jamais elle-même le **Policy
Engine** : pour toute décision de type action, elle reçoit le `Verdict` déjà
calculé comme donnée de l'événement `PolicyEvaluated` — c'est l'appelant (le
**Runtime**) qui interroge le Policy Engine puis lui transmet le
résultat. Transitionne vers `MAX_ITERATIONS` sur réception de l'événement
correspondant, mais ne compare elle-même jamais `iteration_count` à
`max_iterations` : elle ne connaît que l'état courant, pas la Mission complète
— cette comparaison reste une responsabilité non assignée (cf. `CONTEXT.md`,
point ouvert correspondant). N'effectue elle-même aucune I/O — elle retourne un
état suivant, c'est le **Runtime** qui exécute réellement les effets et qui
écrit dans l'Event Log.

### Policy Engine
Composant dédié, séparé de la State Machine. Fonction pure :
`(Action, contexte d'exécution) -> Verdict`, où :

- `ALLOWED` — l'action peut s'exécuter.
- `DENIED` — l'action est refusée (raison incluse).
- `REQUIRES_CONFIRMATION` — l'action est bloquée en attente d'une confirmation
  utilisateur explicite.
- `REWRITE` *(réservé, non implémenté dans le MVP)* — le Policy Engine peut
  proposer une action alternative plutôt qu'un simple refus (ex. `rm -rf build`
  → suggestion d'une suppression ciblée). Le verdict porte alors une action
  alternative en plus de la raison. Traité par la State Machine comme `DENIED` :
  retour en `REASONING` avec une Observation qui inclut la suggestion — c'est
  toujours le Reasoner qui décide de l'adopter ou non, jamais le Policy Engine
  qui substitue l'action de sa propre initiative (même garantie que pour toute
  autre décision : le runtime ne se substitue jamais au LLM).

Responsabilités actuelles : validation des permissions, détection des
commandes dangereuses (`rm -rf`, `git push`, suppression massive...),
déclenchement de la confirmation utilisateur. Peut consulter le **Tool
Registry** (niveau de risque déclaré par l'outil) comme signal d'entrée.
N'exécute rien, n'a aucun effet de bord. Conçu pour accueillir plus tard des
règles supplémentaires (sandbox, quotas, modes d'exécution) sans toucher à la
State Machine ni au Reasoner.

### Executor
Exécute une `Action` déjà validée (verdict `ALLOWED`, ou confirmation obtenue).
Résout le `Tool` concerné via le **Tool Registry**, l'invoque, retourne un
`ToolResult`. Ne parle jamais au LLM, ne consulte jamais le Policy Engine —
quand il reçoit une Action, la décision d'autorisation a déjà été prise en amont.

### Tool Registry
Registre des capacités disponibles, implémenté (`tool_registry.py`). Enregistre
des instances `Tool` exécutables (pas seulement leurs `ToolSpec`) ; `list_tools()`
n'expose en retour que les `ToolSpec` dérivés (`tool.spec`), par principe de
moindre privilège — le Policy Engine et le Context Builder n'obtiennent jamais
un `Tool` exécutable par cette voie. Responsabilités : connaître les outils
enregistrés, exposer leurs descriptions et leurs schémas de paramètres au
Context Builder, exposer leur niveau de risque au Policy Engine, exposer leurs
métadonnées (coût estimé) pour un futur Budget Manager. C'est la seule source
de vérité sur "quels outils existent" — ni le Reasoner ni le Context Builder ne
maintiennent leur propre liste.

### Tool
Capacité atomique (lire un fichier, lancer une commande, `git diff`...). Invoqué
exclusivement par l'Executor, résolu exclusivement via le Tool Registry. Chaque
Tool déclare : `name`, `description`, schéma de `parameters`, `risk level`,
`cost estimate`. Interface commune, validation de ses propres paramètres,
résultat structuré (`ToolResult`).

Implémentations concrètes existantes : `ReadFileTool` (`read_file`, risque
`LOW`) et `ListDirectoryTool` (`list_directory`, risque `LOW`), toutes deux
dans `tools/filesystem.py` — lecture seule, aucun effet de bord ; `ShellTool`
(`run_command`, risque `MEDIUM`), dans `tools/shell.py` — exécute une commande
shell arbitraire. Un Tool n'implémente jamais de filtrage de sécurité
lui-même (pas de whitelist, pas de refus de commande) : cette responsabilité
appartient exclusivement au Policy Engine, y compris pour `run_command` (dont
le `risk_level` `MEDIUM`, combiné à la détection de motifs dangereux du Policy
Engine, est le mécanisme retenu plutôt qu'un `HIGH` systématique — voir
**Policy Engine** ci-dessus).

Depuis la Phase 2 (Workspace), aucun de ces trois Tools n'accède plus
directement au filesystem ou à `subprocess` : chacun reçoit un **Workspace**
injecté au constructeur (`ReadFileTool(workspace)`,
`ListDirectoryTool(workspace)`, `ShellTool(workspace)`) et lui délègue
l'opération technique (`read_file`/`list_directory`/`run_command`). Le Tool
garde la responsabilité métier — validation des arguments, choix du message
d'erreur, construction du `ToolResult` — le Workspace ne fait que l'I/O brute.
Ce découplage ne change ni le comportement observable ni le contrat
`Tool.execute(arguments) -> ToolResult`.

### Workspace
Port technique introduit en Phase 2 (`workspace.py`), interposé entre les
Tools et le filesystem/processus réels : `Runtime → Executor → Tool →
Workspace → filesystem/subprocess`. Interface `Workspace` (ABC) réduite aux
trois opérations réellement utilisées par les Tools actuels — `read_file(path)
-> str`, `list_directory(path) -> list[str]`, `run_command(command) ->
CommandResult` (`NamedTuple` : `stdout`, `stderr`, `return_code`) — plutôt
qu'une API large anticipant des besoins non encore exprimés. Chaque méthode
laisse remonter telles quelles les exceptions du comportement d'origine
(`OSError`, `UnicodeDecodeError`) : c'est toujours le Tool appelant qui les
capture et les traduit en `ToolResult`, exactement comme avant l'introduction
de cette indirection.

`LocalWorkspace`, seule implémentation concrète à ce jour, reproduit
exactement l'ancien comportement des Tools (`pathlib.Path.read_text`,
`Path.iterdir`, `subprocess.run(..., shell=True, capture_output=True,
text=True)`) : aucun sandboxing, aucune restriction de chemin, aucune
allowlist de commandes, aucun timeout — ces sujets sont explicitement hors
périmètre de la Phase 2 et appartiennent à une Phase 3 (sécurité) à venir.
`build_runtime()` (`composition.py`) n'a pas eu besoin d'évoluer : il ne
construit jamais lui-même les `Tool`, il se contente d'enregistrer des
instances déjà construites — c'est l'appelant (tests, futur CLI) qui
instancie `LocalWorkspace()` et l'injecte dans chaque Tool avant de les
passer à `build_runtime(tools=...)`.

### Observation
Modèle implémenté (`models/observation.py`) : `kind` (`ObservationKind` —
`EXECUTION_RESULT` / `EXECUTION_ERROR` / `POLICY_REJECTION` /
`CONFIRMATION_DENIED` / `SYSTEM_INFO`), `summary` (texte traduit, jamais vide)
et `details` (dict libre, optionnel). Ne dépend d'aucun autre modèle ni
composant (ni `ToolResult`, ni `ExecutionError`, ni `Verdict`, ni Executor,
Policy Engine, State Machine ou Event Log) : c'est le **Runtime**, seul
composant à voir à la fois la source (`ToolResult`/rejet/refus) et la
destination (`Observation`), qui façonne la traduction. Le Reasoner ne voit
jamais un `ToolResult` brut.

### Event Log
Séquence append-only, **en mémoire pendant l'exécution**, de tout ce qui arrive
pendant une Mission. Source de vérité pour le Context Builder (lecture) et pour
l'audit/debug en cours de run. Aucune dépendance vers `storage.py` : l'Event
Log ne sait pas qu'il est persisté, il ne fait qu'accumuler et exposer des
requêtes (par type, par plage). Pour le MVP, l'état de la Mission reste
**explicite** (tenu par la State Machine) — le Event Log n'est pas la source
dont cet état est *dérivé* par fold ; c'est un journal fidèle en parallèle, pas
un event-sourcing complet.

Seuls les événements de type `OBSERVATION_PRODUCED` (voir **Vocabulaire
d'événements** plus bas pour la liste complète des `EventType`) sont
réinterprétés comme donnée : le **Context Builder** les relit
(`build_from_event_log`) et reconstruit un `Observation` complet (`kind` +
`summary` + `details`) à partir du payload de chacun, dans leur ordre
d'apparition. Tous les autres types (`ToolExecutionStarted`,
`ConfirmationGranted`, `StateTransitioned`...) restent des faits d'audit,
jamais réinterprétés comme du contexte pour le Reasoner. Un payload
d'`OBSERVATION_PRODUCED` invalide (Event Log corrompu ou construit à la main)
fait lever `MalformedObservationEventError` par le Context Builder plutôt que
d'être absorbé silencieusement — le Runtime étant l'unique producteur normal
de ces événements, un payload invalide signale un bug, pas une entrée externe
à tolérer.

### Storage
Abstraction de persistance (`storage.py`), volontairement minimale : `Storage`
(ABC) déclare `save_events(events: list[Event]) -> None` et
`load_events() -> list[Event]` — pas de logique métier, ne connaît ni Runtime,
ni Reasoner, ni Policy Engine, ni Executor, ni Tool. Étendue de façon additive
avec `save_checkpoint(checkpoint: Checkpoint) -> None` et
`load_checkpoint() -> Checkpoint | None` (Phase 1 — checkpoint/reprise) : les
signatures et la sémantique de `save_events`/`load_events` restent
inchangées. `Storage` connaît désormais aussi `Checkpoint` (donc, par
composition, `Mission` et `ConfirmationRequest`), mais toujours aucune
dépendance vers Runtime, Reasoner, Policy Engine ou Executor. Une seule
implémentation concrète existe à ce jour, `InMemoryStorage` :
`save_events`/`load_events` restent append-only (pas de suppression, pas de
modification d'un événement existant), `load_events()` retourne toujours une
copie, jamais la référence interne. `save_checkpoint`/`load_checkpoint` ne
retiennent qu'un seul `Checkpoint` à la fois (remplacé à chaque appel, pas
accumulé comme les événements — cohérent avec le périmètre mono-Mission de
cette phase), et retournent également des copies profondes (une `Mission`
étant mutable, contrairement à `Event`).

**État réel (pas SQLite actuellement)** : aucune persistance disque n'existe
encore — une Mission ne survit donc pas vraiment à un arrêt du process malgré
l'abstraction en place. Un backend SQLite reste une implémentation future
possible derrière la même interface `Storage`, non implémentée.

C'est le **Runtime** qui fait le pont explicite entre **Event Log** et
**Storage** (`persist_events()` / `load_event_log()`), en instantané complet
à la demande — pas automatiquement à chaque événement ajouté, et sans suivi
incrémental des événements déjà persistés (rappeler `persist_events()`
plusieurs fois duplique ; point ouvert, voir plus bas). `event_log.py` et
`storage.py` ne dépendent l'un de l'autre ni dans un sens ni dans l'autre —
décision délibérément préservée en ajoutant cette capacité au Runtime plutôt
qu'à l'Event Log lui-même (voir **Décisions validées**).

> Le terme **mémoire** n'est volontairement plus utilisé ici : il est réservé à
> une éventuelle **mémoire sémantique** future (résumés inter-missions, rappel
> de contexte au-delà d'une seule Mission — cf. la "porte ouverte" déjà notée
> dans l'`ARCHITECTURE.md` racine d'AI-Lab_v2). Non conçue, non implémentée.
> Event Log et Storage ne sont pas de la mémoire au sens de ce futur composant :
> ce sont un journal d'exécution et sa persistance.

## Points d'extension (non implémentés dans le MVP)

### Critic
Pas un état obligatoire de la State Machine, pas un maillon d'une chaîne de
validation bloquante. Conçu comme un **système de hooks** : le Runtime émet des
événements à des points précis du cycle (`BeforeToolExecution`,
`AfterToolExecution`, `BeforeMissionCompletion`, `BeforeCommit`), et un Critic,
s'il est enregistré, peut y réagir. S'il n'y en a aucun, ces événements
n'ont aucun effet observable — le Critic n'est jamais une dépendance obligatoire
du MVP. Le contrat exact d'intervention (un hook peut-il bloquer l'action, ou
seulement observer/annoter) est un point encore ouvert — voir plus bas.

### Budget Manager
Aujourd'hui, deux responsabilités de budget existent déjà mais sont
distribuées : la State Machine tient le compteur d'itérations, le Context
Builder gère son propre budget de tokens. Un futur `BudgetManager` centraliserait
itérations, tokens, temps maximal et coût d'exécution en une seule autorité
consultée par ces deux composants (et potentiellement par le Reasoner/Executor
pour le suivi de coût par appel). Emplacement architectural clair — consulté
par `state_machine.py` et `context_builder.py` — mais aucun fichier créé pour
l'instant.

## Diagrammes

### Composants

```mermaid
graph TD
    Runtime["Runtime<br/>(orchestrateur impur)"]
    SM["State Machine<br/>(transition pure)"]
    CB["Context Builder"]
    Reasoner["Reasoner (LLM)"]
    PE["Policy Engine<br/>(pure)"]
    Exec["Executor"]
    TR["Tool Registry"]
    Tools["Tools"]
    WS["Workspace<br/>(interface + LocalWorkspace)"]
    EL["Event Log<br/>(mémoire)"]
    ST["Storage<br/>(interface + InMemoryStorage)"]

    Runtime --> CB
    Runtime --> Reasoner
    Runtime --> SM
    Runtime --> PE
    Runtime --> Exec
    Runtime --> EL
    Runtime --> ST

    CB --> EL
    CB --> TR
    PE -.consulte.-> TR
    Exec --> TR
    Exec --> Tools
    TR --> Tools
    Tools --> WS
```

### Séquence — un cycle complet

```mermaid
sequenceDiagram
    participant RT as Runtime
    participant EL as Event Log
    participant CB as Context Builder
    participant TR as Tool Registry
    participant RE as Reasoner (LLM)
    participant SM as State Machine
    participant PE as Policy Engine
    participant EX as Executor
    participant T as Tool
    participant WS as Workspace
    participant ST as Storage

    RT->>EL: append(MissionCreated)
    Note over RT,ST: persist_events() est explicite, a la demande - pas automatique dans cette boucle

    loop tant que non terminal
        RT->>CB: build_from_event_log(event_log)
        CB->>TR: list_tools()
        TR-->>CB: descriptions + schémas
        CB->>EL: événements pertinents
        EL-->>CB: événements
        CB-->>RT: Context

        RT->>RE: decide(context)
        RE-->>RT: Decision
        RT->>EL: append(DecisionReceived)

        RT->>PE: evaluate(action)
        PE-->>RT: Verdict
        RT->>EL: append(PolicyEvaluated)
        RT->>SM: transition(état courant, PolicyEvaluated)
        SM-->>RT: état suivant

        alt ALLOWED
            RT->>EX: execute(action)
            EX->>TR: resolve(tool_name)
            TR-->>EX: Tool
            EX->>T: run(parameters)
            T->>WS: delegue l'operation technique
            WS-->>T: resultat brut / exception
            T-->>EX: ToolResult
            EX-->>RT: ToolResult
            RT->>RT: construit Observation
        else REQUIRES_CONFIRMATION
            RT->>EL: append(ConfirmationRequested)
            Note over RT: run() rend la main ici (AWAITING_CONFIRMATION) ; reprise externe via resume_confirmation(mission, response)
        else DENIED / REWRITE
            RT->>RT: construit Observation (rejet ou suggestion)
        end

        RT->>EL: append(ObservationProduced)
    end
```

### Machine d'état

```mermaid
stateDiagram-v2
    [*] --> CREATED
    CREATED --> REASONING: MissionCreated traité

    REASONING --> EXECUTING: Decision action, Policy ALLOWED
    REASONING --> REASONING: Decision action, Policy DENIED/REWRITE (Observation)
    REASONING --> AWAITING_CONFIRMATION: Policy REQUIRES_CONFIRMATION
    REASONING --> SUCCEEDED: Decision fin de mission (succès)
    REASONING --> FAILED: Decision fin de mission (échec) / erreur irrécupérable
    REASONING --> MAX_ITERATIONS: compteur >= limite

    AWAITING_CONFIRMATION --> EXECUTING: ConfirmationGranted
    AWAITING_CONFIRMATION --> REASONING: ConfirmationDenied (Observation)

    EXECUTING --> REASONING: ToolExecutionCompleted/Failed (Observation)

    CREATED --> ABORTED: arrêt externe
    REASONING --> ABORTED: arrêt externe
    AWAITING_CONFIRMATION --> ABORTED: arrêt externe
    EXECUTING --> ABORTED: arrêt externe (best-effort)

    SUCCEEDED --> [*]
    FAILED --> [*]
    MAX_ITERATIONS --> [*]
    ABORTED --> [*]
```

### Dépendances entre modules

```mermaid
graph LR
    runtime[runtime.py] --> state_machine[state_machine.py]
    runtime --> context_builder[context_builder.py]
    runtime --> reasoner[reasoner.py]
    runtime --> policy[policy.py]
    runtime --> executor[executor.py]
    runtime --> event_log[event_log.py]
    runtime --> storage[storage.py]

    context_builder --> event_log
    context_builder --> tool_registry[tool_registry.py]

    reasoner --> llm[llm.py]
    reasoner --> prompts[prompts.py]

    executor --> tool_registry
    executor --> tools[tools/]

    policy --> tool_registry
    tool_registry --> tools
    tools --> workspace[workspace.py]

    state_machine --> models[models/]
    context_builder --> models
    reasoner --> models
    policy --> models
    executor --> models
    event_log --> models
    storage --> models
```

Aucune dépendance circulaire : `runtime.py` dépend de tout, rien ne dépend de
lui ; `event_log.py` et `storage.py` ne dépendent l'un de l'autre ni dans un
sens ni dans l'autre. `workspace.py` ne dépend d'aucun autre module `peon.*`
(uniquement `pathlib`/`subprocess` de la stdlib) ; seul `tools/` en dépend.

## Machine d'états — table détaillée

| État | Événement déclencheur | Condition | Vers |
|---|---|---|---|
| `CREATED` | `MissionCreated` traité | — | `REASONING` |
| `REASONING` | `DecisionReceived` (action) | Policy → `ALLOWED` | `EXECUTING` |
| `REASONING` | `DecisionReceived` (action) | Policy → `DENIED` ou `REWRITE` | `REASONING` (Observation) |
| `REASONING` | `DecisionReceived` (action) | Policy → `REQUIRES_CONFIRMATION` | `AWAITING_CONFIRMATION` |
| `REASONING` | `DecisionReceived` (fin de mission) | succès | `SUCCEEDED` |
| `REASONING` | `DecisionReceived` (fin de mission) | échec | `FAILED` |
| `REASONING` | — | compteur d'itérations ≥ limite | `MAX_ITERATIONS` |
| `AWAITING_CONFIRMATION` | `ConfirmationGranted` (externe, CLI) | — | `EXECUTING` |
| `AWAITING_CONFIRMATION` | `ConfirmationDenied` (externe, CLI) | — | `REASONING` (Observation) |
| `EXECUTING` | `ToolExecutionCompleted` / `Failed` | — | `REASONING` |
| tout état non terminal | arrêt externe | — | `ABORTED` |

## Vocabulaire d'événements

Implémenté (`models/events.py`, `EventType` + `Event`) pour l'Event Log — un
modèle plat (`type: EventType`, `payload: dict` libre), pas une union
discriminée par événement : `MISSION_CREATED`, `CONTEXT_BUILT`,
`DECISION_RECEIVED`, `POLICY_EVALUATED`, `ACTION_VALIDATED`, `ACTION_REJECTED`,
`CONFIRMATION_REQUESTED`, `CONFIRMATION_GRANTED`, `CONFIRMATION_DENIED`,
`TOOL_EXECUTION_STARTED`, `TOOL_EXECUTION_COMPLETED`, `TOOL_EXECUTION_FAILED`,
`OBSERVATION_PRODUCED`, `STATE_TRANSITIONED`, `MISSION_SUCCEEDED`,
`MISSION_FAILED`, `MISSION_ABORTED`, `MAX_ITERATIONS_REACHED`.

Ce vocabulaire est distinct de celui, plus restreint, utilisé par
`state_machine.py` pour ses propres transitions (`MissionCreated`,
`PolicyEvaluated`, `MissionSucceeded`, `MissionFailed`, `MaxIterationsReached`,
`ConfirmationGranted`, `ConfirmationDenied`, `ToolExecutionFinished` — qui
fusionne `ToolExecutionCompleted`/`Failed` — et `AbortRequested`, absent de la
liste ci-dessus). Aucune traduction entre les deux vocabulaires n'existe encore
— cf. `CONTEXT.md`, section « Points volontairement laissés ouverts ».

Hooks d'extension (émis par le Runtime, sans consommateur dans le MVP tant
qu'aucun Critic n'est enregistré) : `BeforeToolExecution`,
`AfterToolExecution`, `BeforeMissionCompletion`, `BeforeCommit` (ce dernier
suppose une future capacité d'écriture git, absente de la liste d'outils
actuelle). Non implémentés.

## Décisions validées (état final)

- State Machine comme autorité unique de transition, fonction pure.
- Runtime comme unique composant impur / orchestrateur des appels aux autres
  composants.
- Event Log append-only, en mémoire, sans dépendance vers Storage.
- Storage : persistance SQLite séparée, alimentée par le Runtime en parallèle
  de l'Event Log — pas un troisième mécanisme d'historique.
- État explicite (tenu par la State Machine) — pas d'event-sourcing complet
  (état dérivé par fold) pour le MVP.
- Boucle de raisonnement réactive (type ReAct), sans plan global obligatoire.
- Observation distincte de ToolResult.
- Context Builder séparé du Reasoner — le Reasoner ne construit jamais son
  propre contexte et ne devine jamais les outils disponibles.
- Planner renommé Reasoner.
- Policy Engine séparé de la State Machine, fonction pure, verdict à 4 valeurs
  (`ALLOWED`/`DENIED`/`REQUIRES_CONFIRMATION`/`REWRITE`). `REWRITE` est traité
  par la State Machine comme `DENIED` (retour `REASONING`, la suggestion
  voyageant en donnée) ; un premier cas concret est implémenté dans
  `policy.py` (`rm -rf <cible>` non ciblé → suggestion `rm -rf -- ./<cible>`),
  illustratif et non exhaustif.
- `Observation` (`models/observation.py`) : modèle plat `kind`
  (`ObservationKind`) + `summary` + `details` libre, sans dépendance vers
  `ToolResult`, `ExecutionError`, `Verdict` ni aucun composant (Executor,
  Policy Engine, State Machine, Event Log) — la traduction depuis ces sources
  est une responsabilité du Runtime, pas du modèle lui-même.
- Tool Registry : source de vérité unique sur les outils disponibles,
  consultée par Context Builder, Policy Engine et Executor.
- Critic : système de hooks (non bloquants par défaut), pas un état ni une
  chaîne de validation obligatoire — non implémenté dans le MVP.
- Budget Manager : point d'extension identifié (consulté par State Machine et
  Context Builder), non implémenté dans le MVP.
- `agent.py` supprimé du découpage — son rôle est absorbé par la composition
  Runtime + State Machine + Context Builder + Reasoner + Executor.
- Terme "mémoire" réservé à une future mémoire sémantique, non conçue —
  Event Log et Storage ne sont pas cette mémoire.

## Points encore ouverts

1. **Contrat d'intervention du Critic** — un hook (ex. `BeforeToolExecution`)
   peut-il bloquer/annuler l'action, ou est-il purement observationnel
   (logging, alerte) tant qu'aucune décision contraire n'est prise ? À trancher
   avant d'écrire le premier Critic, pas avant.
2. **Adoption d'un `REWRITE`** — toujours retour au Reasoner via Observation
   (tranché ainsi dans ce document), jamais d'auto-application de l'alternative
   sans repasser par une Decision explicite. À confirmer que c'est bien
   l'intention, notamment si un jour un mode "autonome" veut court-circuiter
   cette étape.
3. **Budget Manager : centralisation ou agrégation ?** Le jour de son
   introduction, remplace-t-il les compteurs internes de la State Machine et du
   Context Builder (ceux-ci deviennent de simples clients), ou reste-t-il un
   agrégateur en lecture seule pour le reporting/CLI pendant que chacun garde
   son propre budget partiel ? Influence légèrement la forme des interfaces de
   Phase 1, sans être bloquant.
4. **Synchronicité de la persistance** — le Runtime appelle-t-il
   `storage.py` de façon synchrone à chaque événement (latence I/O dans la
   boucle chaude, mais SQLite local donc négligeable a priori), ou en
   best-effort différé/batché ? À trancher avant la Phase Storage, pas
   maintenant.
5. **Tool Registry statique ou dynamique** — outils enregistrés une fois au
   démarrage (liste fixe pour le MVP), ou le registre doit-il déjà prévoir un
   enregistrement à chaud (plugins, mentionnés comme évolution future dans le
   document de vision initial) ? Pas besoin de trancher avant Phase 1.

## Hors périmètre du MVP

Event-sourcing complet (état dérivé des événements), reprise après crash par
**replay complet de l'Event Log** (non branché — la Phase 1 checkpoint/reprise
restaure la `Mission` et une `ConfirmationRequest` en attente via `Checkpoint`,
pas l'historique complet des événements/observations ; voir **Checkpoint**
ci-dessus), backend `Storage` sur disque (SQLite ou autre — seule
`InMemoryStorage` existe, y compris pour les checkpoints), reprise
multi-mission (un `Runtime` ne retient qu'un `Checkpoint`/une confirmation en
attente à la fois), plan amont multi-étapes, Critic (hooks posés, aucun Critic
écrit), Budget Manager, verdict `REWRITE` du Policy Engine, mémoire
sémantique, multi-agents, Policy Engine au-delà de permissions/commandes
dangereuses/confirmation (pas de sandbox ni de quotas pour l'instant).

## Structure du projet

```text
peon/
├── pyproject.toml
├── README.md
├── ARCHITECTURE.md
├── CONTEXT.md
├── CHANGELOG.md
├── LICENSE
├── .gitignore
├── src/peon/
│   ├── __init__.py
│   ├── cli.py              # point d'entree Typer (peon --version)
│   ├── composition.py      # build_runtime() : assemble un Runtime a partir d'un LLM concret
│   │                        # et d'une liste de Tool, sans coupler Runtime a un fournisseur
│   ├── runtime.py          # orchestrateur impur : appelle context_builder/reasoner/executor,
│   │                        # alimente state_machine et event_log, persiste optionnellement
│   │                        # vers storage (persist_events / load_event_log,
│   │                        # save_checkpoint / resume_mission)
│   ├── state_machine.py    # transition pure (etat, evenement) -> etat ; ne consulte pas
│   │                        # policy.py (Verdict fourni par l'appelant, Runtime)
│   ├── context_builder.py  # Observations ou Event Log -> Context (build / build_from_event_log)
│   ├── reasoner.py         # Context -> Decision ; ABC Reasoner + LLMReasoner (appelle llm.py)
│   ├── policy.py           # Action (+ Tool Registry) -> Verdict
│   ├── executor.py         # Action validee -> ToolResult (resout le Tool via tool_registry.py)
│   ├── tool_registry.py    # registre des Tools : descriptions, schemas, risk level, cost estimate
│   ├── event_log.py        # journal append-only en memoire, zero dependance vers storage.py
│   ├── storage.py          # abstraction Storage (ABC) + InMemoryStorage (evenements et
│   │                        # checkpoints), pas de backend disque
│   ├── llm.py              # abstraction fournisseur LLM (ABC)
│   ├── prompts.py          # PromptBuilder : Context -> messages LLM
│   ├── workspace.py        # abstraction Workspace (ABC) + LocalWorkspace :
│   │                        # port technique filesystem/subprocess pour les Tools
│   │
│   ├── providers/
│   │   ├── __init__.py
│   │   └── ollama.py       # OllamaLLM : premier fournisseur LLM concret (API chat Ollama, HTTP)
│   │
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── filesystem.py   # ReadFileTool (read_file, LOW), ListDirectoryTool (list_directory, LOW)
│   │   │                    # -- toutes deux injectees avec un Workspace
│   │   └── shell.py        # ShellTool (run_command, MEDIUM) -- injecte avec un Workspace
│   │
│   └── models/              # schemas Pydantic partages entre composants
│       ├── __init__.py
│       ├── mission.py
│       ├── checkpoint.py    # Checkpoint : Mission + ConfirmationRequest|None (Phase 1)
│       ├── context.py
│       ├── decision.py
│       ├── action.py
│       ├── verdict.py       # ALLOWED / DENIED / REQUIRES_CONFIRMATION / REWRITE
│       ├── execution_error.py
│       ├── confirmation.py  # ConfirmationRequest / ConfirmationResponse
│       ├── observation.py   # kind (ObservationKind) + summary + details, zero dependance
│       ├── tool_result.py
│       ├── tool_spec.py     # declaration d'un Tool : name/description/parameters/risk/cost
│       └── events.py
│
└── tests/                   # miroir de src/peon/ (+ tests/tools/, tests/models/,
                              # tests/providers/, integration, test_checkpoint.py)
```

`tools/git.py` et `tools/search.py`, envisagés dans une version antérieure de
ce document, n'existent pas encore et ne sont plus listés dans l'arborescence
ci-dessus (qui ne documente que l'existant) — ils restent des extensions
possibles, mentionnées dans `CONTEXT.md`.

Notes sur ce découpage :

- `storage/` (sous-package) reste `storage.py` (module unique) : la
  responsabilité est assez étroite (une ABC + une implémentation mémoire) pour
  ne pas justifier un sous-package tant qu'un second backend (SQLite ou autre)
  n'est pas envisagé.
- `memory.py` a disparu du découpage — remplacé par le couple
  `event_log.py` (mémoire, pendant l'exécution) / `storage.py` (persistance
  explicite via le Runtime, en mémoire pour l'instant, disque plus tard).
  "Mémoire" reste un terme réservé au futur, pas un fichier actuel.
- `models/tool_spec.py` porte la déclaration statique d'un Tool (métadonnées),
  distincte de `tools/base.py` qui porte l'interface exécutable — même logique
  de séparation donnée/comportement que pour `models/mission.py` vs
  `state_machine.py`.
- `models/verdict.py` (schéma du verdict) reste distinct de `policy.py`
  (logique qui le produit), pour ne pas confondre les deux dans les imports.

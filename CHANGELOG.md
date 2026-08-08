# Changelog

## Unreleased

### Added

- `cli.py` (Phase 5 - CLI minimale) : `peon run "<goal>"` et `peon resume`,
  en plus de `peon --version` deja existant. Construit son `Runtime`
  exclusivement via `build_runtime()` (`composition.py`, inchange), ne
  reimplemente jamais la boucle ReAct - pilote uniquement l'API publique du
  Runtime (`run`, `resume_confirmation`, `resume_mission`, `save_checkpoint`,
  `pending_confirmation`), verifie par un test qui inspecte l'AST du module
  (aucune reference a `Reasoner`/`PolicyEngine`/`Executor`/`StateMachine`).
  `peon run` gere les confirmations en direct (affichage `Tool`/`Arguments`/
  `Reason`, lecture `y`/`N` via `typer.confirm`, `ConfirmationResponse` reel,
  boucle jusqu'a l'etat final). `peon resume` s'appuie reellement sur le
  Checkpoint de la Phase 1 (`Storage.load_checkpoint()` ->
  `Runtime.resume_mission()` -> meme boucle de confirmation si necessaire) ;
  absence de checkpoint -> message clair, `exit_code=1`. Configuration LLM
  minimale et explicite (`--model`/`--base-url`/`--timeout-seconds` sur
  `OllamaLLM`, valeurs par defaut en constantes de module) - pas de `.env`,
  pas de nouvelle couche de configuration. Deux limites documentees plutot
  que masquees : `InMemoryStorage` du process CLI ne permet a `peon resume`
  de retrouver un Checkpoint que dans le meme process, pas apres un vrai
  redemarrage ; aucun `Tool` reel livre n'est `RiskLevel.HIGH` aujourd'hui,
  donc le chemin de confirmation (implemente et teste) ne se declenche pas
  encore en usage reel.
- `tests/test_cli.py` (7 nouveaux tests) : objectif accepte et mission
  resolue avec un LLM stub, chemin nominal action puis fin, confirmation
  acceptee avec reprise reelle de l'action, confirmation refusee avec
  comportement `Runtime` inchange, `peon resume` sans checkpoint echoue
  proprement, `peon resume` retrouve et reprend un Checkpoint reel, absence
  de boucle ReAct dupliquee dans `cli.py`.

- `tracing.py` (Phase 4 - Observability / tracing technique) : port
  d'observabilite minimal, separe du vocabulaire metier - `Tracer`/`Span`
  (ABC) + `NoOpTracer` par defaut. `Tracer.start_span(name, **attributes)`
  est un context manager qui garantit la fermeture du span meme en cas
  d'exception (try/except/finally centralise dans la classe de base, pas
  duplique par implementation). Pas de dependance OpenTelemetry a ce stade -
  abstraction pensee pour qu'un futur adaptateur OTel s'y branche sans
  reecriture.
- `Runtime` recoit un `tracer` optionnel (`tracer=None` par defaut,
  comportement observable strictement inchange - voir `tests/test_tracing.py`,
  test 1). Quatre points instrumentes : `run()`, `resume_confirmation()`,
  l'appel au Reasoner (`reasoner.decide`, mesure l'appel LLM) et l'appel a
  l'Executor (`executor.run`, attribut technique `tool_name`). Aucun
  `EventType` ajoute, aucun evenement de tracing dans l'`EventLog`, aucun
  changement a `Storage`/`Checkpoint`/`StateMachine`/`PolicyEngine`/
  `Executor`/Tools. Pas de metriques de tokens : aucune source fiable
  n'existe dans `LLM`/`Reasoner` aujourd'hui, rien n'est fabrique.
- `tests/test_tracing.py` (9 tests) : comportement inchange sans tracer,
  sequence de spans coherente (debut/fin bien imbriques) sur une mission
  complete et sur `resume_confirmation()`, attribut `tool_name` du span
  Executor, fermeture de span garantie sur exception (avec propagation),
  `EventLog`/`Observation` strictement identiques avec ou sans tracer,
  contrat `NoOpTracer` teste isolement du Runtime.

- `guardrails.py` (Phase 3 - Policy / Guardrails) : regles composables du
  Policy Engine. `PolicyRule` (`Protocol`, `Action -> Verdict | None`) et
  cinq implementations - `ToolAuthorizationRule` (refuse un Tool absent du
  `ToolRegistry`), `DangerousCommandRule` (motif `rm -rf` non cible, reprise
  telle quelle de l'ancien `_check_dangerous_command`), `ArgumentsSchemaRule`
  (valide `Action.arguments` contre `ToolSpec.parameters_schema`, resout le
  point ouvert correspondant), `PathRestrictionRule` (restriction de chemin
  optionnelle, opt-in via `workspace_root`), `RiskLevelRule` (regle
  generique HIGH -> REQUIRES_CONFIRMATION, sinon ALLOWED, en dernier
  recours).
- `PolicyEngine.evaluate()` (`policy.py`) refactore pour composer cette
  chaine ordonnee au lieu de porter lui-meme la logique de regle. Ordre
  invariant preserve exactement (regle de commande dangereuse toujours
  prioritaire sur la regle generique de `risk_level`). Constructeur etendu
  de maniere additive (`rules=`, `workspace_root=`), le constructeur par
  defaut `PolicyEngine(registry)` reproduisant exactement le comportement
  observable pre-Phase 3 pour tout appelant existant.
- `tests/test_guardrails.py` (20 tests) et extension de `tests/test_policy.py`
  (13 tests) : chaque regle testee isolement, semantique de composition
  (premiere regle gagnante, `None` laisse continuer, fail-closed si aucune
  regle ne tranche), autorisation/schema/chemin via `PolicyEngine`.

- `Checkpoint` model (`models/checkpoint.py`) : instantane composable (pas de
  duplication de champs) d'une `Mission` et de son eventuelle
  `ConfirmationRequest` en attente, suffisant pour reprendre le cas
  `action -> REQUIRES_CONFIRMATION` apres un arret/crash du process.
- `Storage.save_checkpoint()` / `load_checkpoint()`, ajoutes de maniere
  additive (signatures de `save_events`/`load_events` inchangees), implementes
  par `InMemoryStorage`.
- `Runtime.save_checkpoint(mission)` / `Runtime.resume_mission(checkpoint)` :
  un nouveau `Runtime` peut restaurer la `Mission` et la confirmation en
  attente d'un `Checkpoint` charge, puis reprendre via
  `resume_confirmation()` deja existant, sans dupliquer la boucle ReAct.
- `Workspace` (ABC) / `LocalWorkspace` (`workspace.py`, Phase 2 - migration
  filesystem/shell) : port technique entre les Tools et le filesystem/
  subprocess reels (`read_file`, `list_directory`, `run_command`).
  `ReadFileTool`, `ListDirectoryTool`, `ShellTool` recoivent desormais ce
  `Workspace` par injection au constructeur et ne font plus d'appel direct a
  `pathlib`/`subprocess` ; comportement fonctionnel strictement inchange
  (pas de sandboxing, pas de restriction de chemins, pas de timeout - hors
  perimetre de cette phase).

### Fixed

- `PromptBuilder._render_observations()` (`prompts.py`) : un `Observation` de
  succes (`EXECUTION_RESULT`) ne transmettait au LLM que son `summary`
  generique ("outil 'x' execute avec succes"), jamais le contenu reel de
  `details["output"]` deja present dans le `Context` - decouvert lors d'un
  test reel avec Ollama (le LLM ne voyait pas le resultat de
  `run_command`/`read_file` et repetait l'action ou hallucinait jusqu'a
  `max_iterations`). `_render_observations()` ajoute desormais une ligne
  "resultat : ..." pour ces observations (string affichee telle quelle, dict/
  liste serialises en JSON), tronquee a 2000 caracteres (`_MAX_OUTPUT_CHARS`,
  nouvelle convention - aucune n'existait avant dans le code). Comportement
  des autres `ObservationKind` (`EXECUTION_ERROR`, `POLICY_REJECTION`,
  `CONFIRMATION_DENIED`) strictement inchange : leur `summary` etait deja le
  contenu informatif complet. Aucun changement a `Observation`/`Context`/
  `EventLog`/`Runtime`/`ContextBuilder`.
- `tests/test_prompts.py` (5 nouveaux tests) et
  `tests/test_integration_reasoner_uses_tool_output.py` (1 nouveau test,
  boucle complete `Runtime` avec un Reasoner qui ne lit que le texte de
  prompt reellement construit par `PromptBuilder`) : contenu reel d'un
  resultat reussi visible dans le prompt (sortie texte et sortie
  dict/JSON), troncature au-dela de la limite, rendu des erreurs inchange,
  sortie `None`/absente ignoree, second tour de la boucle ReAct qui exploite
  effectivement la sortie du premier appel d'outil.

### Tests

350 passing

## 0.1.0

### Implemented

- Mission lifecycle
- Pure State Machine
- Policy Engine
- Tool execution
- EventLog
- Observation model
- Context reconstruction
- Reasoning abstraction
- Confirmation flow
- Storage abstraction
- OllamaLLM provider (`providers/ollama.py`)
- Runtime composition wiring (`composition.py`)

### Tests

279 passing

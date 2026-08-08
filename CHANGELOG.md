# Changelog

## Unreleased

### Added

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

### Tests

337 passing

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

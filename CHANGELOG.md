# Changelog

## Unreleased

### Added

- `storage.py` : `FileStorage` persiste désormais l'`EventLog` sur disque
  (chantier « reprise durable avec historique »), pas seulement le
  `Checkpoint` - fichier JSON Lines séparé (`<checkpoint>.events.jsonl`),
  réécrit atomiquement (tempfile + `os.replace()`, comme `save_checkpoint()`)
  à chaque `save_events()` pour respecter le contrat append-only de
  `Storage` sans risquer une dernière ligne tronquée en cas de crash pendant
  l'écriture. Nouvelle exception `CorruptedEventLogError` (même famille que
  `CorruptedCheckpointError`) si une ligne persistée n'est plus un JSON
  `Event` valide. `runtime.py` : `Runtime` suit désormais le nombre
  d'événements déjà envoyés à `Storage` (`self._persisted_event_count`,
  initialisé à la taille de l'`EventLog` injecté) pour que
  `persist_events()` n'envoie que le delta - un appel répété ne duplique
  plus l'historique déjà persisté (ancien point ouvert documenté dans
  `ARCHITECTURE.md`/`CONTEXT.md`, résolu). `Runtime.resume_mission()`
  reconstruit `self._observations` depuis `self._event_log` via
  `ContextBuilder.build_from_event_log()` (déjà utilisé par la boucle de
  raisonnement à chaque cycle, aucune logique de replay séparée introduite)
  au lieu de le vider inconditionnellement - sans effet si l'`EventLog`
  injecté est vierge (comportement historique inchangé), mais restaure
  l'historique complet si l'appelant l'a préalablement chargé via
  `Runtime.load_event_log(storage)`. `composition.py` : `build_runtime()`
  accepte un paramètre `event_log` optionnel (défaut `None` -> `EventLog()`
  vierge, comportement historique inchangé) pour ce câblage. `cli.py` :
  `resume` charge l'`EventLog` persisté (`Runtime.load_event_log(_storage)`)
  avant de construire le Runtime ; `_drive_to_completion` appelle
  `runtime.persist_events()` juste après chaque `runtime.save_checkpoint()`,
  pour qu'un Checkpoint ne soit jamais sauvegardé sans l'historique qui lui
  correspond. Décision d'architecture (option B du chantier, détaillée dans
  `ARCHITECTURE.md`/`CONTEXT.md`) : compléter `Storage` plutôt que faire
  porter l'historique par `Checkpoint` (option A, rejetée - dupliquerait un
  état déjà disponible par une autre voie, coût croissant à chaque
  `save_checkpoint()`, contredit la séparation `Checkpoint` déjà actée).
  Aucun changement à `Executor`/`PolicyEngine`/`StateMachine`/`Workspace`/
  `Tracer`/aux Tools existants, aucune nouvelle boucle de raisonnement,
  aucune dépendance SQLite, pas de multi-mission.
- `tests/test_storage.py` : `test_file_storage_events_stay_in_memory_only`
  (documentait l'ancien comportement, désormais faux) remplacé par 6 tests
  couvrant la persistance `FileStorage` des événements à travers une
  nouvelle instance, l'append-only sur plusieurs appels, un fichier absent,
  un contenu corrompu (`CorruptedEventLogError`), l'isolation entre deux
  chemins de Checkpoint distincts, et `save_events([])` sans effet de bord.
- `tests/test_runtime_storage.py` (1 nouveau test) : deux appels successifs
  à `persist_events()` sur la même session ne dupliquent pas les événements
  déjà envoyés à `Storage`.
- `tests/test_resume_history.py` (nouveau fichier, 8 tests) : les trois cas
  fonctionnels du chantier - reprise après plusieurs Actions réussies
  (contexte reconstruit identique à celui de Runtime A), reprise en attente
  de confirmation avec historique préalable préservé, crash/nouveau process
  via deux instances `FileStorage` distinctes sur le même chemin disque -
  plus un round-trip complet `EventLog` via `FileStorage`, l'absence de
  duplication sur plusieurs checkpoints dans une même session façon
  `cli.py::_drive_to_completion`, un checkpoint jamais sauvegardé,
  des événements persistés corrompus, et un test bout-en-bout à deux
  process Python distincts (`subprocess`) qui ne partagent que le disque.
- `reasoner.py` : `RetryingReasoner`, décorateur de `Reasoner` (chantier
  « Reprise contrôlée après erreur LLM ») - enveloppe n'importe quel
  `Reasoner` (jamais `Ollama`-spécifique) et retente `decide()` un nombre
  borné de fois (`max_attempts`, défaut 2, aucune boucle infinie, aucun
  backoff) uniquement sur `InvalidLLMResponseError` ; toute autre exception
  remonte immédiatement. Audit préalable (`LLMReasoner.decide()`,
  `InvalidLLMResponseError`, `OllamaRequestError`,
  `Runtime._run_reasoning_cycle()`) : les erreurs transitoires observées
  avec un vrai Ollama (réseau/HTTP, JSON invalide, `message.content`
  absent, décision non conforme) convergent déjà toutes vers
  `InvalidLLMResponseError` sans distinction structurelle possible sans
  nouvelle hiérarchie d'exceptions (hors périmètre) - retry uniforme sur ce
  seul type plutôt qu'un tri par catégorie inventé. `Tracer` optionnel
  (même convention que `Runtime`, défaut `NoOpTracer`), un span
  `reasoner.decide.attempt` par tentative, aucun nouvel `EventType`
  métier, `EventLog` non touché. `Runtime`/`OllamaLLM`/`CLI`/`LLMReasoner`
  inchangés : `Runtime` continue d'appeler `reasoner.decide(context)` une
  seule fois par cycle, avant toute construction d'`Action` - garantie
  structurelle qu'aucune Action ni aucun Tool n'est jamais rejoué par ce
  mécanisme, quel que soit le nombre de tentatives internes. Reprise
  explicite via `Checkpoint` écartée : la CLI ne sauvegarde un `Checkpoint`
  qu'au moment d'`AWAITING_CONFIRMATION`, jamais lors d'une panne de
  raisonnement, et le rendre utile ici demanderait de lui faire porter
  l'`EventLog`/les `Observation` (refonte hors périmètre).
  `composition.py` : `build_runtime()` enveloppe désormais toujours
  `LLMReasoner` dans un `RetryingReasoner` (`reasoner_max_attempts`,
  `tracer` : nouveaux paramètres optionnels de câblage uniquement).
- `tests/test_reasoner.py` (9 nouveaux tests) : `RetryingReasoner` en
  isolation - premier essai réussi (aucun retry), reprise après un échec
  transitoire, épuisement de toutes les tentatives, borne stricte à
  `max_attempts`, défaut faible, exception inattendue jamais retentée,
  `max_attempts < 1` refusé, un span par tentative, comportement inchangé
  sans `Tracer`.
- `tests/test_runtime.py` (5 nouveaux tests) : comportement observé depuis
  `Runtime` avec un `RetryingReasoner` injecté - reprise transparente pour
  le compteur d'itérations de la Mission, échec de la Mission seulement
  après épuisement des tentatives, aucune exécution de Tool supplémentaire
  pendant une reprise interne au Reasoner, historique préservé quand une
  Action réussie est suivie d'une panne LLM qui épuise ses tentatives,
  agnosticisme fournisseur (stub `Reasoner` sans aucun import Ollama).
- `tests/test_composition.py` (3 nouveaux tests) : `build_runtime()`
  récupère par défaut d'une panne LLM transitoire générique (aucun import
  Ollama), `reasoner_max_attempts` configurable, `tracer` transmis à la
  fois à `Runtime` et à `RetryingReasoner`.
- `tools/filesystem.py` : `DeleteFileTool` (`delete_file`), premier `Tool` de
  production classe `RiskLevel.HIGH` - jusqu'ici les trois `Tool` reels
  (`read_file`/`list_directory` en `LOW`, `run_command` en `MEDIUM`) ne
  declenchaient jamais `REQUIRES_CONFIRMATION` en usage reel (voir la limite
  notee dans `ARCHITECTURE.md`/`README.md` depuis la Phase 5). Supprime un
  seul fichier via `Workspace.delete_file(path)` (nouvelle methode abstraite,
  implementee par `LocalWorkspace.delete_file()` -> `Path(path).unlink()`,
  meme convention que `read_file`/`list_directory` : laisse remonter `OSError`
  tel quel, capture et traduction en `ToolResult(success=False)` faites par le
  Tool appelant, jamais par `Workspace`). Meme schema de parametres que
  `read_file`/`list_directory` (`path: string`, requis) : reutilise donc sans
  aucun changement `PathRestrictionRule` (`guardrails.py`, deja generique sur
  toute propriete `path` declaree par un `ToolSpec`) et `RiskLevelRule`
  (`HIGH -> REQUIRES_CONFIRMATION`, deja generique). Aucune nouvelle regle de
  Policy, aucun changement a `PolicyEngine`/`Executor`/`StateMachine`/
  `Runtime` : le chemin de confirmation existant (`AWAITING_CONFIRMATION`,
  `ConfirmationRequest`/`ConfirmationResponse`, checkpoint/reprise) est
  desormais reellement exerce de bout en bout par un Tool de production, sans
  qu'aucun de ces composants n'ait eu besoin d'evoluer. `cli.py` :
  `_build_tools()` enregistre desormais `DeleteFileTool(workspace)` en plus
  des trois Tools existants - premier `Tool` `HIGH` reellement disponible via
  `peon run`/`peon resume`.
- `tests/tools/test_filesystem.py` (7 nouveaux tests), `tests/test_workspace.py`
  (2 nouveaux tests) : `DeleteFileTool`/`LocalWorkspace.delete_file()` -
  spec HIGH avec `path` requis, suppression reelle d'un fichier existant
  (verifiee sur disque), fichier absent, argument manquant/invalide, dossier
  au lieu d'un fichier, jamais d'exception pour un argument invalide.
- `tests/test_policy.py` (3 nouveaux tests) : le vrai `DeleteFileTool`
  (pas un stub) enregistre dans un `ToolRegistry` reel - `HIGH` declenche
  `REQUIRES_CONFIRMATION` sans `workspace_root`, chemin hors racine `DENIED`
  avant toute confirmation, chemin dans la racine `REQUIRES_CONFIRMATION`
  (combinaison HIGH + `PathRestrictionRule`).
- `tests/test_integration_delete_file.py` (nouveau, 4 tests) : cycle complet
  bout en bout sans mock interne, seul module a exercer reellement
  `REQUIRES_CONFIRMATION -> AWAITING_CONFIRMATION -> resume_confirmation()`
  avec un Tool de production (les autres tests de confirmation existants
  utilisent tous un `_StubTool` `HIGH` de test) - le fichier cible existe
  toujours juste apres `REQUIRES_CONFIRMATION` (aucune execution avant
  confirmation), est reellement supprime apres acceptation, reste intact
  apres refus, et une reprise apres "crash" simule (deux instances `Runtime`
  distinctes autour d'un `Checkpoint`, meme scenario que
  `tests/test_checkpoint.py`) aboutit bien a une suppression reelle sur
  disque, pas seulement a un statut de Mission correct.
- `tests/test_cli.py` (2 nouveaux tests) : `peon run` avec le vrai
  `cli._build_tools()` (non monkeypatche, contrairement au reste du module) -
  confirmation affichee pour `delete_file`, fichier reellement supprime apres
  acceptation (`y`), intact apres refus (`n`).

- `composition.py` / `cli.py` (wiring de la restriction de Workspace/chemins) :
  `PathRestrictionRule` existait (`guardrails.py`, `PolicyEngine(registry,
  workspace_root=...)`) mais aucun appelant reel ne la configurait -
  desormais reellement branchable de bout en bout. `build_runtime()` gagne
  `workspace_root: Path | str | None = None`, pur pass-through vers
  `PolicyEngine` (aucune resolution/interpretation du chemin dans
  `composition.py` lui-meme). `cli.py` gagne `--workspace-root` sur `peon
  run` et `peon resume` (`Path | None`, `None` par defaut), normalise par
  `Path.resolve()` a la frontiere CLI (`_normalize_workspace_root()`) avant
  d'atteindre `build_runtime()`. Opt-in des deux cotes : `None` par defaut
  partout, aucune regression pour un appelant qui ne fournit pas l'option.
  `--workspace-root` n'est pas persistee dans le `Checkpoint` : `peon
  resume` doit la refournir pour qu'elle s'applique aux Actions de la
  boucle de raisonnement reprise (l'Action deja confirmee au moment du
  `save_checkpoint()` s'execute sans repasser par le Policy Engine,
  comportement inchange de `Runtime.resume_confirmation()`). Aucun
  changement a `policy.py`/`guardrails.py` (deja prets), `Workspace`,
  `Runtime`, `EventLog`, `Storage`/`Checkpoint`, `StateMachine`, `Tracer`.
- `tests/test_guardrails.py` (5 nouveaux tests) : comportement de
  `PathRestrictionRule` verifie explicitement plutot que seulement suppose
  correct - chemin inexistant dans/hors racine (`Path.resolve()` ne requiert
  pas l'existence), repertoire frere partageant un prefixe de nom
  (`root-evil` a cote de `root`, comparaison via `Path.parents` jamais
  `str.startswith()`), casse et separateurs `/`/`\` sur Windows
  (`skipif` hors Windows), jonction NTFS creee dans la racine mais pointant
  vers une cible exterieure (`skipif` hors Windows, ignore si la creation de
  jonction echoue dans l'environnement de test).
- `tests/test_policy.py` (1 nouveau test) : `PathRestrictionRule` garde la
  priorite sur `RiskLevelRule` generique dans la chaine par defaut (un Tool
  `HIGH` avec un chemin hors racine est `DENIED`, pas
  `REQUIRES_CONFIRMATION`).
- `tests/test_composition.py` (3 nouveaux tests) et `tests/test_cli.py`
  (4 nouveaux tests) : `build_runtime()`/`peon run`/`peon resume` sans
  `workspace_root`/`--workspace-root` ne restreignent toujours rien
  (regression) ; avec, un chemin dans la racine est autorise et un chemin
  hors racine est refuse (Tool jamais execute) ; `peon resume
  --workspace-root` refuse une nouvelle Action hors racine pendant la boucle
  de raisonnement reprise tout en laissant s'executer l'Action deja
  confirmee du `Checkpoint`.

- `storage.py` (Persistance disque du Checkpoint) : `FileStorage(Storage)`,
  nouvelle implementation concrete a cote d'`InMemoryStorage` (inchangee) -
  persiste uniquement le `Checkpoint` sur disque, en JSON
  (`model_dump_json()`/`model_validate_json()`, deja fournis par le modele).
  `save_events`/`load_events` delegues a un `InMemoryStorage` interne : les
  evenements restent en memoire, la persistance de l'`EventLog` restant hors
  perimetre de cette phase. Ecriture atomique (`tempfile.mkstemp()` +
  `os.replace()`), repertoire parent cree si besoin. Fichier absent -> `None` ; JSON
  invalide/incompatible -> `CorruptedCheckpointError` (nouvelle exception)
  plutot qu'une corruption silencieuse. Mono-mission comme
  `InMemoryStorage` : chaque `save_checkpoint()` remplace le fichier
  precedent. Aucune dependance externe (stdlib + `pydantic`, deja present).
- `cli.py` : `cli._storage` passe d'`InMemoryStorage()` a
  `FileStorage(_DEFAULT_CHECKPOINT_PATH)`, avec
  `_DEFAULT_CHECKPOINT_PATH = Path.home() / ".peon" / "checkpoint.json"` -
  emplacement deterministe, pas de `.env`, pas de nouvelle couche de
  configuration. `peon resume` retrouve desormais un Checkpoint apres un
  vrai redemarrage du process `peon`, plus seulement dans le meme process.
- `tests/test_storage.py` (7 nouveaux tests), `tests/test_checkpoint.py`
  (1 nouveau test) et `tests/test_cli.py` (1 nouveau test) : round-trip
  `FileStorage` via une nouvelle instance sur le meme fichier, fichier
  absent, JSON invalide, contenu ecrit valide, remplacement au second
  `save_checkpoint()`, creation du repertoire parent manquant, evenements
  non partages entre deux `FileStorage` sur le meme fichier ; scenario
  `Runtime` A/B avec deux `FileStorage` distinctes sur le meme fichier, sans
  etat Python partage ; `peon resume` reel (`CliRunner`/`tmp_path`) qui
  retrouve un Checkpoint ecrit par une premiere instance de `FileStorage`
  puis relu par une seconde.

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
- `LLMReasoner.decide()` (`reasoner.py`) : un echec de `LLM.generate()`
  (reseau, timeout, reponse fournisseur invalide - `OllamaRequestError` pour
  Ollama, mais n'importe quel fournisseur futur) remontait brut, sans
  traduction, jusqu'a l'appelant. `decide()` capture desormais toute
  exception levee par `self._llm.generate()` et la relance en
  `InvalidLLMResponseError` (exception deja existante du Reasoner, `from exc`
  pour preserver la cause d'origine) - aucun nouveau type d'exception
  introduit, aucun changement au contrat `LLM.generate() -> str`.
- `Runtime._run_reasoning_cycle()` (`runtime.py`) : une `InvalidLLMResponseError`
  levee par `self._reasoner.decide(context)` (JSON invalide, ou echec LLM
  traduit ci-dessus) n'etait pas capturee et remontait comme une exception non
  geree, crashant la mission en cours - confirme lors de tests reels avec
  Ollama sur des prompts longs (`gpt-oss:20b`). Capture desormais cette
  exception (nouvelle methode privee `_fail_mission_on_reasoning_error()`) et
  fait transiter la Mission vers `FAILED` via le meme evenement
  `MissionFailed` que `_finish_mission(..., outcome="failure")` - aucune
  Action executee, aucune Observation/Decision fabriquee pour ce tour,
  historique (`EventLog`, observations precedentes) intact, aucun retry.
  `Runtime` n'importe toujours pas `providers.ollama` : seule
  `InvalidLLMResponseError` (`reasoner.py`) lui est connue, quel que soit le
  fournisseur `LLM` concret. Comportement nominal (LLM qui repond
  correctement) strictement inchange.
- `tests/test_reasoner.py` (2 nouveaux tests) et `tests/test_runtime.py`
  (3 nouveaux tests) : `LLMReasoner.decide()` traduit `OllamaRequestError` et
  une exception generique en `InvalidLLMResponseError` avec un seul appel a
  `generate()` ; une `InvalidLLMResponseError` directe echoue la mission sans
  crash ni faux `DECISION_RECEIVED`/`POLICY_EVALUATED` ; le meme scenario bout
  en bout via `LLMReasoner` + un `LLM` qui echoue comme `OllamaLLM`
  (`OllamaRequestError`) ; historique preserve quand une action reussie
  precede un echec LLM au tour suivant.
- `LocalWorkspace.run_command()` (`workspace.py`) : `subprocess.run(...,
  text=True)` decodait `stdout`/`stderr` avec l'encodage preferi du systeme
  et `errors="strict"` implicite - une sortie shell produisant des octets
  invalides pour cet encodage levait `UnicodeDecodeError`, non capturee par
  `ShellTool.execute()` (qui ne capture que `OSError`) et donc remontee
  brute jusqu'au `Runtime` - identifie lors de tests reels avec Ollama sur
  Windows, particulierement pertinent avec le mode UTF-8 par defaut des
  versions recentes de Python. Corrige en passant desormais explicitement
  `encoding="utf-8", errors="replace"` a `subprocess.run()` : `stdout`/
  `stderr` restent toujours des `str`, les octets invalides sont remplaces
  (`U+FFFD`) au lieu de lever, `return_code` inchange. Responsabilite du
  decodage gardee dans `Workspace` (pas remontee dans `ShellTool`) ;
  contrats `Workspace.run_command() -> CommandResult` et `Tool.execute() ->
  ToolResult` inchanges. Aucun changement a `Runtime`/`PolicyEngine`/
  `Executor`/`Reasoner`/`LLM`.
- `tests/test_workspace.py` (4 nouveaux tests) et `tests/tools/test_shell.py`
  (1 nouveau test) : sortie UTF-8 normale, octets invalides sur `stdout`
  seul, sur `stderr` seul, sur les deux simultanement avec `return_code` non
  nul preserve, `ShellTool.execute()` qui ne leve plus pour une sortie
  non-UTF-8 - via un script Python minimal ecrit dans `tmp_path`, portable,
  sans dependre d'une commande Windows/Unix particuliere.
- `_drive_to_completion()` (`cli.py`, audit de consolidation) : ne
  persistait `Checkpoint`/`EventLog` qu'en entrant dans la boucle d'attente
  de confirmation, jamais apres l'avoir quittee. Une Mission qui se
  terminait (avec ou sans confirmation) sans repasser par une nouvelle pause
  laissait donc sur disque le dernier `Checkpoint` ecrit - une
  `ConfirmationRequest` deja resolue en memoire. Un `peon resume` ulterieur
  la retrouvait telle quelle et re-executait l'Action `HIGH` correspondante
  une deuxieme fois (viole "exactement une execution", voir
  `DeleteFileTool`/`RiskLevelRule`). Persiste desormais aussi l'etat final
  apres la boucle, quel que soit le nombre de tours effectues : un
  `Checkpoint` sur disque reflete toujours l'etat courant, jamais une pause
  deja resolue.
- `peon resume` (`cli.py`) : un `Checkpoint`/`EventLog` corrompu sur disque
  (`CorruptedCheckpointError`/`CorruptedEventLogError`, deja leves par
  `FileStorage`) n'etait capture nulle part dans la CLI et remontait comme
  une trace Python brute. `resume` capture desormais ces deux exceptions et
  affiche un message dedie (`Checkpoint file is corrupted.` /
  `Event log file is corrupted.`) avant de sortir en erreur.
- `tests/test_cli.py` (3 nouveaux tests) : reprise apres succes ne
  re-execute plus l'Action deja confirmee ; Checkpoint corrompu et EventLog
  corrompu produisent chacun un message explicite plutot qu'une trace non
  geree.
- `tests/test_cli.py` (2 nouveaux tests) et `tests/test_resume_history.py`
  (1 nouveau test), couverture d'integration manquante identifiee par
  l'audit : `_build_llm` relie reellement `--base-url`/`--timeout-seconds` a
  un `OllamaLLM` fonctionnel (vrai serveur HTTP local, meme convention que
  `test_composition.py`) et aux valeurs par defaut de la CLI quand aucune
  option n'est fournie ; `RetryingReasoner` continue de recuperer d'une
  panne LLM transitoire *apres* une reprise `build_runtime()` + historique
  persiste (`FileStorage`), sans perdre l'observation d'avant redemarrage
  ni rejouer l'Action deja executee.

### Tests

441 passing

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

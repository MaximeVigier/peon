# Changelog

## Unreleased

### Added

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

295 passing

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

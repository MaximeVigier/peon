import json
from pathlib import Path

import pytest

from peon.models.action import Action
from peon.models.checkpoint import Checkpoint
from peon.models.confirmation import ConfirmationRequest
from peon.models.events import Event, EventType
from peon.models.mission import Mission, MissionStatus
from peon.models.tool_spec import RiskLevel
from peon.storage import CorruptedCheckpointError, CorruptedEventLogError, FileStorage, InMemoryStorage, Storage


def test_storage_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        Storage()  # type: ignore[abstract]


def test_saving_then_loading_returns_the_saved_events() -> None:
    storage = InMemoryStorage()
    first = Event(type=EventType.MISSION_CREATED)
    second = Event(type=EventType.MISSION_SUCCEEDED)

    storage.save_events([first, second])

    assert storage.load_events() == [first, second]


def test_load_events_returns_a_copy_not_the_internal_reference() -> None:
    storage = InMemoryStorage()
    storage.save_events([Event(type=EventType.MISSION_CREATED)])

    snapshot = storage.load_events()
    snapshot.append(Event(type=EventType.MISSION_SUCCEEDED))

    assert len(storage.load_events()) == 1


def test_save_events_is_append_only_across_multiple_calls() -> None:
    storage = InMemoryStorage()
    first = Event(type=EventType.MISSION_CREATED)
    second = Event(type=EventType.POLICY_EVALUATED)
    third = Event(type=EventType.MISSION_SUCCEEDED)

    storage.save_events([first])
    storage.save_events([second, third])

    assert storage.load_events() == [first, second, third]


def test_loading_an_empty_storage_returns_an_empty_list() -> None:
    storage = InMemoryStorage()

    assert storage.load_events() == []


# --- FileStorage : persistance disque du Checkpoint --------------------------


def _checkpoint(goal: str = "deployer le service") -> Checkpoint:
    mission = Mission(goal=goal)
    mission.status = MissionStatus.AWAITING_CONFIRMATION
    request = ConfirmationRequest(
        mission_id=mission.id,
        action=Action(reasoning="publier", tool_name="git_push", arguments={}, risk_level=RiskLevel.HIGH),
        reason="commande jugee dangereuse",
    )
    return Checkpoint(mission=mission, pending_confirmation=request)


def test_file_storage_round_trips_through_a_brand_new_instance(tmp_path: Path) -> None:
    checkpoint = _checkpoint()
    path = tmp_path / "checkpoint.json"

    FileStorage(path).save_checkpoint(checkpoint)
    reloaded = FileStorage(path).load_checkpoint()

    assert reloaded == checkpoint


def test_file_storage_load_checkpoint_with_no_file_returns_none(tmp_path: Path) -> None:
    storage = FileStorage(tmp_path / "does-not-exist.json")

    assert storage.load_checkpoint() is None


def test_file_storage_load_checkpoint_with_invalid_json_raises_explicitly(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.json"
    path.write_text("this is not json", encoding="utf-8")
    storage = FileStorage(path)

    with pytest.raises(CorruptedCheckpointError):
        storage.load_checkpoint()


def test_file_storage_save_checkpoint_writes_valid_json(tmp_path: Path) -> None:
    checkpoint = _checkpoint()
    path = tmp_path / "checkpoint.json"

    FileStorage(path).save_checkpoint(checkpoint)

    raw = path.read_text(encoding="utf-8")
    assert json.loads(raw)  # ne leve pas -- contenu JSON valide
    assert Checkpoint.model_validate_json(raw) == checkpoint


def test_file_storage_second_save_replaces_the_first_checkpoint(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.json"
    storage = FileStorage(path)
    first = _checkpoint("premiere mission")
    second = _checkpoint("deuxieme mission")

    storage.save_checkpoint(first)
    storage.save_checkpoint(second)

    assert FileStorage(path).load_checkpoint() == second
    # Aucun fichier temporaire ne doit trainer a cote du checkpoint final.
    assert list(tmp_path.iterdir()) == [path]


def test_file_storage_creates_the_parent_directory_if_missing(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "dir" / "checkpoint.json"
    checkpoint = _checkpoint()

    FileStorage(path).save_checkpoint(checkpoint)

    assert path.exists()
    assert FileStorage(path).load_checkpoint() == checkpoint


# --- FileStorage : persistance disque de l'EventLog ---------------------------


def test_file_storage_events_persist_across_a_brand_new_instance(tmp_path: Path) -> None:
    # Contrairement au comportement historique (evenements delegues a un
    # InMemoryStorage interne, jamais ecrits sur disque), une nouvelle
    # instance de FileStorage pointant sur le meme Checkpoint retrouve
    # desormais les evenements deja sauvegardes -- meme garantie que pour le
    # Checkpoint (voir test_file_storage_round_trips_through_a_brand_new_instance).
    path = tmp_path / "checkpoint.json"
    first = Event(type=EventType.MISSION_CREATED)
    second = Event(type=EventType.MISSION_SUCCEEDED)
    FileStorage(path).save_events([first, second])

    reloaded = FileStorage(path).load_events()

    assert reloaded == [first, second]


def test_file_storage_save_events_is_append_only_across_multiple_calls(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.json"
    storage = FileStorage(path)
    first = Event(type=EventType.MISSION_CREATED)
    second = Event(type=EventType.POLICY_EVALUATED)
    third = Event(type=EventType.MISSION_SUCCEEDED)

    storage.save_events([first])
    storage.save_events([second, third])

    assert storage.load_events() == [first, second, third]
    # Verifie sur une instance fraiche, pas seulement via l'objet qui a ecrit.
    assert FileStorage(path).load_events() == [first, second, third]


def test_file_storage_load_events_with_no_file_returns_an_empty_list(tmp_path: Path) -> None:
    storage = FileStorage(tmp_path / "checkpoint.json")

    assert storage.load_events() == []


def test_file_storage_load_events_with_corrupted_file_raises_explicitly(tmp_path: Path) -> None:
    checkpoint_path = tmp_path / "checkpoint.json"
    events_path = tmp_path / "checkpoint.json.events.jsonl"
    events_path.write_text("this is not a valid event\n", encoding="utf-8")

    with pytest.raises(CorruptedEventLogError):
        FileStorage(checkpoint_path).load_events()


def test_file_storage_events_are_isolated_between_different_checkpoint_paths(tmp_path: Path) -> None:
    storage_a = FileStorage(tmp_path / "mission-a.json")
    storage_b = FileStorage(tmp_path / "mission-b.json")

    storage_a.save_events([Event(type=EventType.MISSION_CREATED)])

    assert storage_a.load_events() != []
    assert storage_b.load_events() == []


def test_file_storage_save_events_with_an_empty_list_does_not_create_a_file(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.json"
    storage = FileStorage(path)

    storage.save_events([])

    assert storage.load_events() == []
    assert list(tmp_path.iterdir()) == []

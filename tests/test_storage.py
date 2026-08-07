import pytest

from peon.models.events import Event, EventType
from peon.storage import InMemoryStorage, Storage


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

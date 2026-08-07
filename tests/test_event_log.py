import pytest
from pydantic import ValidationError

from peon.event_log import EventLog
from peon.models.events import Event, EventType


def test_log_starts_empty() -> None:
    log = EventLog()

    assert log.list_events() == []


def test_append_adds_the_event() -> None:
    log = EventLog()
    event = Event(type=EventType.MISSION_CREATED)

    log.append(event)

    assert log.list_events() == [event]


def test_append_preserves_insertion_order() -> None:
    log = EventLog()
    first = Event(type=EventType.MISSION_CREATED)
    second = Event(type=EventType.POLICY_EVALUATED)
    third = Event(type=EventType.MISSION_SUCCEEDED)

    log.append(first)
    log.append(second)
    log.append(third)

    assert log.list_events() == [first, second, third]


def test_list_events_returns_a_snapshot_not_a_live_view() -> None:
    log = EventLog()
    log.append(Event(type=EventType.MISSION_CREATED))

    snapshot = log.list_events()
    log.append(Event(type=EventType.MISSION_SUCCEEDED))

    assert len(snapshot) == 1
    assert len(log.list_events()) == 2


def test_list_events_by_type_filters_events() -> None:
    log = EventLog()
    created = Event(type=EventType.MISSION_CREATED)
    succeeded = Event(type=EventType.MISSION_SUCCEEDED)
    log.append(created)
    log.append(succeeded)

    assert log.list_events_by_type(EventType.MISSION_CREATED) == [created]


def test_list_events_by_type_with_no_match_returns_empty() -> None:
    log = EventLog()
    log.append(Event(type=EventType.MISSION_CREATED))

    assert log.list_events_by_type(EventType.MISSION_FAILED) == []


def test_appended_events_cannot_be_mutated() -> None:
    log = EventLog()
    log.append(Event(type=EventType.MISSION_CREATED))

    with pytest.raises(ValidationError):
        log.list_events()[0].type = EventType.MISSION_FAILED  # type: ignore[misc]

import uuid
from datetime import datetime

import pytest
from pydantic import ValidationError

from peon.models.events import Event, EventType


def test_defaults_are_sensible() -> None:
    event = Event(type=EventType.MISSION_CREATED)

    assert isinstance(event.id, uuid.UUID)
    assert isinstance(event.timestamp, datetime)
    assert event.payload == {}


def test_payload_is_carried_as_given() -> None:
    event = Event(type=EventType.TOOL_EXECUTION_FAILED, payload={"tool_name": "run_command"})

    assert event.payload == {"tool_name": "run_command"}


def test_invalid_event_type_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Event(type="not_a_real_event")


def test_event_is_frozen() -> None:
    event = Event(type=EventType.MISSION_CREATED)

    with pytest.raises(ValidationError):
        event.type = EventType.MISSION_FAILED  # type: ignore[misc]


def test_two_events_have_distinct_ids() -> None:
    first = Event(type=EventType.MISSION_CREATED)
    second = Event(type=EventType.MISSION_CREATED)

    assert first.id != second.id

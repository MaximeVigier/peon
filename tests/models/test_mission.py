import uuid
from datetime import datetime

import pytest
from pydantic import ValidationError

from peon.models.mission import Mission, MissionStatus


def test_defaults_are_sensible() -> None:
    mission = Mission(goal="Corriger le bug de parsing")

    assert isinstance(mission.id, uuid.UUID)
    assert mission.status == MissionStatus.CREATED
    assert mission.iteration_count == 0
    assert mission.max_iterations == 20
    assert isinstance(mission.created_at, datetime)


def test_goal_is_stripped() -> None:
    mission = Mission(goal="  Corriger le bug  ")

    assert mission.goal == "Corriger le bug"


@pytest.mark.parametrize("goal", ["", "   "])
def test_blank_goal_is_rejected(goal: str) -> None:
    with pytest.raises(ValidationError):
        Mission(goal=goal)


def test_negative_iteration_count_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Mission(goal="x", iteration_count=-1)


def test_max_iterations_below_one_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Mission(goal="x", max_iterations=0)


def test_iteration_count_at_limit_is_allowed() -> None:
    mission = Mission(goal="x", iteration_count=5, max_iterations=5)

    assert mission.iteration_count == mission.max_iterations


def test_iteration_count_above_limit_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Mission(goal="x", iteration_count=6, max_iterations=5)


def test_invalid_status_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Mission(goal="x", status="running")

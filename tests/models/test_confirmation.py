import uuid
from datetime import datetime

import pytest
from pydantic import ValidationError

from peon.models.action import Action
from peon.models.confirmation import ConfirmationRequest, ConfirmationResponse
from peon.models.tool_spec import RiskLevel


def _action() -> Action:
    return Action(
        reasoning="Nettoyer le dossier de build",
        tool_name="run_command",
        arguments={"command": "rm -rf build"},
        risk_level=RiskLevel.HIGH,
    )


def test_confirmation_request_defaults() -> None:
    mission_id = uuid.uuid4()
    request = ConfirmationRequest(mission_id=mission_id, action=_action(), reason="commande a risque eleve")

    assert isinstance(request.id, uuid.UUID)
    assert request.mission_id == mission_id
    assert isinstance(request.requested_at, datetime)


def test_confirmation_request_reason_is_stripped() -> None:
    request = ConfirmationRequest(mission_id=uuid.uuid4(), action=_action(), reason="  risque eleve  ")

    assert request.reason == "risque eleve"


@pytest.mark.parametrize("reason", ["", "   "])
def test_confirmation_request_rejects_blank_reason(reason: str) -> None:
    with pytest.raises(ValidationError):
        ConfirmationRequest(mission_id=uuid.uuid4(), action=_action(), reason=reason)


def test_confirmation_request_requires_mission_id() -> None:
    with pytest.raises(ValidationError):
        ConfirmationRequest(action=_action(), reason="x")


def test_confirmation_response_granted_without_note() -> None:
    response = ConfirmationResponse(request_id=uuid.uuid4(), granted=True)

    assert response.granted is True
    assert response.note is None
    assert isinstance(response.decided_at, datetime)


def test_confirmation_response_denied_with_note() -> None:
    response = ConfirmationResponse(request_id=uuid.uuid4(), granted=False, note="chemin non fiable")

    assert response.granted is False
    assert response.note == "chemin non fiable"


@pytest.mark.parametrize("note", ["", "   "])
def test_confirmation_response_blank_note_becomes_none(note: str) -> None:
    response = ConfirmationResponse(request_id=uuid.uuid4(), granted=True, note=note)

    assert response.note is None


def test_confirmation_response_requires_request_id() -> None:
    with pytest.raises(ValidationError):
        ConfirmationResponse(granted=True)


def test_confirmation_response_is_independent_of_any_request_instance() -> None:
    response = ConfirmationResponse(request_id=uuid.uuid4(), granted=True)

    assert response.request_id is not None

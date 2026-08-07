import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, Field, field_validator

from peon.models.action import Action


class ConfirmationRequest(BaseModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    mission_id: uuid.UUID
    action: Action
    reason: str = Field(min_length=1)
    requested_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("reason")
    @classmethod
    def _reason_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("reason ne peut pas etre vide")
        return stripped


class ConfirmationResponse(BaseModel):
    request_id: uuid.UUID
    granted: bool
    decided_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    note: str | None = None

    @field_validator("note")
    @classmethod
    def _blank_note_becomes_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

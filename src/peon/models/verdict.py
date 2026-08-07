from enum import Enum

from pydantic import BaseModel, Field, field_validator, model_validator

from peon.models.action import Action


class VerdictType(str, Enum):
    ALLOWED = "allowed"
    DENIED = "denied"
    REQUIRES_CONFIRMATION = "requires_confirmation"
    REWRITE = "rewrite"


class Verdict(BaseModel):
    type: VerdictType
    reason: str = Field(min_length=1)
    suggested_action: Action | None = None

    @field_validator("reason")
    @classmethod
    def _reason_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("reason ne peut pas etre vide")
        return stripped

    @model_validator(mode="after")
    def _suggested_action_only_on_rewrite(self) -> "Verdict":
        if self.type is VerdictType.REWRITE and self.suggested_action is None:
            raise ValueError("suggested_action est requis quand type est REWRITE")
        if self.type is not VerdictType.REWRITE and self.suggested_action is not None:
            raise ValueError("suggested_action ne doit etre defini que pour REWRITE")
        return self

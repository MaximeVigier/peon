from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ObservationKind(str, Enum):
    EXECUTION_RESULT = "execution_result"
    EXECUTION_ERROR = "execution_error"
    POLICY_REJECTION = "policy_rejection"
    CONFIRMATION_DENIED = "confirmation_denied"
    SYSTEM_INFO = "system_info"


class Observation(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: ObservationKind
    summary: str = Field(min_length=1)
    details: dict[str, Any] | None = None

    @field_validator("summary")
    @classmethod
    def _summary_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("summary ne peut pas etre vide")
        return stripped

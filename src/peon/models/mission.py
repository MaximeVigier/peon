import uuid
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field, field_validator, model_validator


class MissionStatus(str, Enum):
    CREATED = "created"
    REASONING = "reasoning"
    EXECUTING = "executing"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    MAX_ITERATIONS = "max_iterations"
    ABORTED = "aborted"


class Mission(BaseModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    goal: str
    status: MissionStatus = MissionStatus.CREATED
    iteration_count: int = Field(default=0, ge=0)
    max_iterations: int = Field(default=20, ge=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("goal")
    @classmethod
    def _goal_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("goal ne peut pas etre vide")
        return stripped

    @model_validator(mode="after")
    def _iteration_count_within_limit(self) -> "Mission":
        if self.iteration_count > self.max_iterations:
            raise ValueError("iteration_count ne peut pas depasser max_iterations")
        return self

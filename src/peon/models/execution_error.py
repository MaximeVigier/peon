from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class ErrorCategory(str, Enum):
    INVALID_REQUEST = "invalid_request"
    TOOL_FAILURE = "tool_failure"
    SYSTEM_ERROR = "system_error"


_EXPECTED_CATEGORIES = frozenset({ErrorCategory.INVALID_REQUEST, ErrorCategory.TOOL_FAILURE})


class ExecutionError(BaseModel):
    category: ErrorCategory
    message: str = Field(min_length=1)
    tool_name: str | None = None
    details: dict[str, Any] | None = None

    @property
    def is_expected(self) -> bool:
        return self.category in _EXPECTED_CATEGORIES

    @field_validator("message")
    @classmethod
    def _message_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("message ne peut pas etre vide")
        return stripped

    @model_validator(mode="after")
    def _tool_failure_requires_tool_name(self) -> "ExecutionError":
        if self.category is ErrorCategory.TOOL_FAILURE and not (self.tool_name and self.tool_name.strip()):
            raise ValueError("tool_name est requis quand category est TOOL_FAILURE")
        return self

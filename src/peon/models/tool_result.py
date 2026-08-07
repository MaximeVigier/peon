from typing import Any

from pydantic import BaseModel, Field, model_validator


class ToolResult(BaseModel):
    success: bool
    output: Any = None
    error: str | None = None
    return_code: int | None = None
    duration_ms: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _error_matches_success(self) -> "ToolResult":
        if self.success and self.error is not None:
            raise ValueError("error doit etre None quand success est True")
        if not self.success and not self.error:
            raise ValueError("error est requis quand success est False")
        return self

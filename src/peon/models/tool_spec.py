from enum import Enum
from typing import Annotated, Any

from pydantic import BaseModel, Field, StringConstraints

ToolName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$")]


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ToolSpec(BaseModel):
    name: ToolName
    description: str = Field(min_length=1)
    parameters_schema: dict[str, Any]
    risk_level: RiskLevel
    cost_estimate: float | None = Field(default=None, ge=0)

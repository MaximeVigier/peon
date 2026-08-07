from typing import Any

from pydantic import BaseModel, Field

from peon.models.tool_spec import RiskLevel, ToolName


class Action(BaseModel):
    reasoning: str = Field(min_length=1)
    tool_name: ToolName
    arguments: dict[str, Any] = Field(default_factory=dict)
    risk_level: RiskLevel

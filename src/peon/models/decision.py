from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field

from peon.models.tool_spec import ToolName


class ActionDecision(BaseModel):
    kind: Literal["action"] = "action"
    reasoning: str = Field(min_length=1)
    tool_name: ToolName
    arguments: dict[str, Any] = Field(default_factory=dict)


class FinishDecision(BaseModel):
    kind: Literal["finished"] = "finished"
    outcome: Literal["success", "failure"]
    summary: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)


Decision = Annotated[Union[ActionDecision, FinishDecision], Field(discriminator="kind")]

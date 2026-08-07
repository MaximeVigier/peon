from pydantic import BaseModel, ConfigDict, Field, field_validator

from peon.models.mission import MissionStatus
from peon.models.observation import Observation
from peon.models.tool_spec import ToolSpec


class Context(BaseModel):
    model_config = ConfigDict(frozen=True)

    mission_goal: str = Field(min_length=1)
    mission_status: MissionStatus
    mission_iteration_count: int = Field(ge=0)
    observations: list[Observation] = Field(default_factory=list)
    available_tools: list[ToolSpec] = Field(default_factory=list)

    @field_validator("mission_goal")
    @classmethod
    def _mission_goal_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("mission_goal ne peut pas etre vide")
        return stripped

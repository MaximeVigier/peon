import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EventType(str, Enum):
    MISSION_CREATED = "mission_created"
    CONTEXT_BUILT = "context_built"
    DECISION_RECEIVED = "decision_received"
    POLICY_EVALUATED = "policy_evaluated"
    ACTION_VALIDATED = "action_validated"
    ACTION_REJECTED = "action_rejected"
    CONFIRMATION_REQUESTED = "confirmation_requested"
    CONFIRMATION_GRANTED = "confirmation_granted"
    CONFIRMATION_DENIED = "confirmation_denied"
    TOOL_EXECUTION_STARTED = "tool_execution_started"
    TOOL_EXECUTION_COMPLETED = "tool_execution_completed"
    TOOL_EXECUTION_FAILED = "tool_execution_failed"
    OBSERVATION_PRODUCED = "observation_produced"
    STATE_TRANSITIONED = "state_transitioned"
    MISSION_SUCCEEDED = "mission_succeeded"
    MISSION_FAILED = "mission_failed"
    MISSION_ABORTED = "mission_aborted"
    MAX_ITERATIONS_REACHED = "max_iterations_reached"


class Event(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    type: EventType
    payload: dict[str, Any] = Field(default_factory=dict)

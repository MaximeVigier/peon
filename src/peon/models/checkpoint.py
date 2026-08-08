"""Checkpoint : instantane suffisant pour reconstruire une Mission suspendue
apres arret/crash du process, sans dupliquer les champs de `Mission` ni de
`ConfirmationRequest` (composition, pas recopie).

Ne porte ni l'EventLog ni les Observations : la reprise complete de
l'historique de raisonnement (event-sourcing/replay) reste hors perimetre de
cette phase (voir ARCHITECTURE.md, "Hors perimetre du MVP"). Ce que porte un
Checkpoint suffit exactement au cas cible de cette phase : reprendre un
resume_confirmation() apres redemarrage.
"""

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field

from peon.models.confirmation import ConfirmationRequest
from peon.models.mission import Mission


class Checkpoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    mission: Mission
    pending_confirmation: ConfirmationRequest | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

"""Checkpoint : instantane suffisant pour reconstruire une Mission suspendue
apres arret/crash du process, sans dupliquer les champs de `Mission` ni de
`ConfirmationRequest` (composition, pas recopie).

Ne porte ni l'EventLog ni les Observations, et ce n'est pas une lacune :
l'historique complet est deja porte ailleurs, par `Storage.save_events()`/
`load_events()`, reconstruit via `Runtime.load_event_log()` puis
`ContextBuilder.build_from_event_log()` (voir ARCHITECTURE.md, section
Storage). Dupliquer cet historique dans le Checkpoint reproduirait un etat
deja disponible par une autre voie, pour un cout croissant a chaque
`save_checkpoint()`. Un Checkpoint reste donc volontairement minimal :
Mission + ConfirmationRequest en attente, rien de plus.
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

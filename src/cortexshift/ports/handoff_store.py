"""Port defining persistence boundaries for canonical CortexShift handoffs."""

from datetime import datetime
from typing import Protocol, runtime_checkable

from cortexshift.domain.handoff import HandoffFailureCode, HandoffRecord, HandoffStatus


@runtime_checkable
class HandoffStore(Protocol):
    """Abstract port for persisting and querying canonical handoff records.

    Kept deliberately narrow and cohesive so the Phase 2 ``StateStore`` interface is not
    re-expanded. A single concrete adapter may implement several persistence ports.
    """

    def save_handoff(self, handoff: HandoffRecord) -> None:
        """Persist or update a canonical HandoffRecord including its payload."""
        ...

    def update_handoff_delivery(
        self,
        handoff_id: str,
        status: HandoffStatus,
        target_session_id: str | None = None,
        delivered_at: datetime | None = None,
        failure_code: HandoffFailureCode | None = None,
    ) -> None:
        """Update the delivery metadata of an existing handoff record."""
        ...

    def get_handoff(self, handoff_id: str) -> HandoffRecord | None:
        """Retrieve a HandoffRecord by its stable identifier."""
        ...

    def list_handoffs(
        self,
        project_id: str | None = None,
        task_id: str | None = None,
        limit: int = 20,
    ) -> list[HandoffRecord]:
        """List handoff records, ordered newest first."""
        ...

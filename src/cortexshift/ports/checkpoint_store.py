"""Port defining persistence boundaries for canonical CortexShift checkpoints."""

from typing import Protocol, runtime_checkable

from cortexshift.domain.checkpoint import CheckpointRecord


@runtime_checkable
class CheckpointStore(Protocol):
    """Abstract port for persisting and querying canonical checkpoint records.

    Kept deliberately narrow and cohesive so the Phase 2 ``StateStore`` interface is not
    re-expanded. A single concrete adapter may implement several persistence ports.
    """

    def save_checkpoint(self, checkpoint: CheckpointRecord) -> None:
        """Persist a canonical CheckpointRecord including its payload."""
        ...

    def get_checkpoint(self, checkpoint_id: str) -> CheckpointRecord | None:
        """Retrieve a CheckpointRecord by its stable identifier."""
        ...

    def list_checkpoints(
        self,
        project_id: str | None = None,
        task_id: str | None = None,
        limit: int | None = 20,
    ) -> list[CheckpointRecord]:
        """List checkpoint records, ordered newest first."""
        ...

    def get_latest_checkpoint(
        self,
        task_id: str,
    ) -> CheckpointRecord | None:
        """Retrieve the newest checkpoint record for a given task."""
        ...

    def list_task_checkpoint_history(
        self,
        task_id: str,
    ) -> list[CheckpointRecord]:
        """List every checkpoint for one task, oldest first, with deterministic ordering."""
        ...

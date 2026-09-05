"""Port defining repository inspection and snapshot persistence capabilities."""

from pathlib import Path
from typing import Protocol, runtime_checkable

from cortexshift.domain.git import GitSnapshot, RepositoryInspection


@runtime_checkable
class RepositoryInspector(Protocol):
    """Abstract port for inspecting the repository filesystem and Git status.

    Extracts empirical repository state without embedding shell or git specifics
    into core domain logic.
    """

    def inspect(self, project_root: Path | str, project_id: str = "") -> RepositoryInspection:
        """Inspect the repository and return a RepositoryInspection domain model.

        Args:
            project_root: Absolute path to the CortexShift project root directory.
            project_id: Canonical identifier of the containing Project.

        Returns:
            A populated RepositoryInspection domain model.
        """
        ...


@runtime_checkable
class RepositorySnapshotStore(Protocol):
    """Abstract port for persisting and retrieving Git snapshots."""

    def save_snapshot(self, snapshot: GitSnapshot) -> None:
        """Persist a GitSnapshot."""
        ...

    def get_snapshot(self, snapshot_id: str) -> GitSnapshot | None:
        """Retrieve a GitSnapshot by its unique identifier."""
        ...

    def list_snapshots(self, project_id: str, limit: int = 10) -> list[GitSnapshot]:
        """List snapshots for a given project, ordered newest first."""
        ...

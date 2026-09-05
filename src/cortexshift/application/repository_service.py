"""Application service for Git repository inspection and snapshot persistence."""

from pathlib import Path

from cortexshift.adapters.git.inspector import GitRepositoryInspector
from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.application.locator import ProjectLocator
from cortexshift.domain.errors import (
    GitNotInstalledError,
    GitProbeError,
    GitProbeTimeoutError,
    NotAGitRepositoryError,
    ProjectNotInitializedError,
)
from cortexshift.domain.git import (
    GitSnapshot,
    RepositoryInspection,
    RepositoryInspectionStatus,
)
from cortexshift.domain.project import Project
from cortexshift.ports.repository import RepositoryInspector, RepositorySnapshotStore


class RepositoryService:
    """Coordinates live Git repository inspection and snapshot persistence.

    Enforces architecture layering:
    - Never executes raw subprocess commands directly (delegates to RepositoryInspector).
    - Never executes raw SQL statements directly (delegates to RepositorySnapshotStore).
    """

    def __init__(
        self,
        inspector: RepositoryInspector | None = None,
        store: RepositorySnapshotStore | None = None,
        locator: type[ProjectLocator] = ProjectLocator,
    ) -> None:
        self._inspector = inspector or GitRepositoryInspector()
        self._store = store
        self._locator = locator

    def _resolve_project_and_store(
        self, start_path: Path | None = None
    ) -> tuple[Path, Project, RepositorySnapshotStore]:
        """Locate initialized CortexShift project and open its snapshot store."""
        project_root = self._locator.find_project_root(start_path)
        if project_root is None:
            raise ProjectNotInitializedError()

        if self._store is not None:
            store = self._store
            # If store is also a StateStore, fetch default project
            if hasattr(store, "get_default_project"):
                project = store.get_default_project()
                if project is None:
                    raise ProjectNotInitializedError()
            else:
                project = Project(
                    id="proj_resolved",
                    name=project_root.name,
                    repo_path=str(project_root),
                )
            return project_root, project, store

        db_path = self._locator.get_database_path(project_root)
        sqlite_store = SQLiteStateStore(db_path, auto_migrate=True)
        project = sqlite_store.get_default_project()
        if project is None:
            sqlite_store.close()
            raise ProjectNotInitializedError()

        return project_root, project, sqlite_store

    def inspect_repository(self, start_path: Path | None = None) -> RepositoryInspection:
        """Perform a live, non-persisting inspection of the repository."""
        project_root, project, _ = self._resolve_project_and_store(start_path)
        return self._inspector.inspect(project_root=project_root, project_id=project.id)

    def capture_snapshot(self, start_path: Path | None = None) -> GitSnapshot:
        """Perform live inspection and persist the snapshot if successful.

        Raises:
            GitNotInstalledError: If Git is not found in PATH.
            NotAGitRepositoryError: If the project is not in a Git repository.
            GitProbeTimeoutError: If inspection timed out.
            GitProbeError: If inspection failed.
        """
        project_root, project, store = self._resolve_project_and_store(start_path)
        inspection = self._inspector.inspect(project_root=project_root, project_id=project.id)

        if inspection.status == RepositoryInspectionStatus.GIT_NOT_INSTALLED:
            raise GitNotInstalledError(
                inspection.diagnostic or "Git executable was not found in PATH."
            )

        if inspection.status == RepositoryInspectionStatus.NOT_GIT_REPOSITORY:
            raise NotAGitRepositoryError(
                inspection.diagnostic or "This CortexShift project is not inside a Git repository."
            )

        if inspection.status == RepositoryInspectionStatus.PROBE_ERROR:
            diag = inspection.diagnostic or "Git repository inspection failed."
            if "timed out" in diag.lower():
                raise GitProbeTimeoutError(diag)
            raise GitProbeError(diag)

        if inspection.snapshot is None:
            raise GitProbeError("No repository snapshot was generated.")

        store.save_snapshot(inspection.snapshot)
        return inspection.snapshot

    def list_snapshots(
        self,
        project_id: str | None = None,
        limit: int = 10,
        start_path: Path | None = None,
    ) -> list[GitSnapshot]:
        """List historical snapshots for a project, newest first."""
        _, project, store = self._resolve_project_and_store(start_path)
        pid = project_id or project.id
        return store.list_snapshots(project_id=pid, limit=limit)

    def get_snapshot(self, snapshot_id: str, start_path: Path | None = None) -> GitSnapshot | None:
        """Retrieve a stored snapshot by ID."""
        _, _, store = self._resolve_project_and_store(start_path)
        return store.get_snapshot(snapshot_id)

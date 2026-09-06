"""Application service for Git repository inspection and snapshot persistence."""

from collections.abc import Iterator
from contextlib import contextmanager
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

    @contextmanager
    def _resolve_project_and_store(
        self, start_path: Path | None = None
    ) -> Iterator[tuple[Path, Project, RepositorySnapshotStore]]:
        """Locate the initialized project and yield its snapshot store.

        A store opened here is closed on exit. An injected store belongs to its caller
        and is left open, so callers that share one connection (the MCP server, for
        instance) keep working.
        """
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
            yield project_root, project, store
            return

        db_path = self._locator.get_database_path(project_root)
        sqlite_store = SQLiteStateStore(db_path, auto_migrate=True)
        try:
            project = sqlite_store.get_default_project()
            if project is None:
                raise ProjectNotInitializedError()
            yield project_root, project, sqlite_store
        finally:
            sqlite_store.close()

    def inspect_repository(self, start_path: Path | None = None) -> RepositoryInspection:
        """Perform a live, non-persisting inspection of the repository."""
        with self._resolve_project_and_store(start_path) as (project_root, project, _):
            return self._inspector.inspect(project_root=project_root, project_id=project.id)

    def capture_snapshot(self, start_path: Path | None = None) -> GitSnapshot:
        """Perform live inspection and persist the snapshot if successful.

        Raises:
            GitNotInstalledError: If Git is not found in PATH.
            NotAGitRepositoryError: If the project is not in a Git repository.
            GitProbeTimeoutError: If inspection timed out.
            GitProbeError: If inspection failed.
        """
        with self._resolve_project_and_store(start_path) as (project_root, project, store):
            return self._capture(project_root, project, store)

    def _capture(
        self,
        project_root: Path,
        project: Project,
        store: RepositorySnapshotStore,
    ) -> GitSnapshot:
        """Inspect the working tree and persist the resulting snapshot."""
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
        with self._resolve_project_and_store(start_path) as (_, project, store):
            pid = project_id or project.id
            return store.list_snapshots(project_id=pid, limit=limit)

    def get_snapshot(self, snapshot_id: str, start_path: Path | None = None) -> GitSnapshot | None:
        """Retrieve a stored snapshot by ID."""
        with self._resolve_project_and_store(start_path) as (_, _, store):
            return store.get_snapshot(snapshot_id)

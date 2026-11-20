"""Application service for Git repository inspection and snapshot persistence."""

from collections.abc import Iterator
from contextlib import contextmanager

from cortexshift.adapters.git.inspector import GitRepositoryInspector
from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.application.locator import ProjectLocator
from cortexshift.application.workspace import WorkspaceIdentity, WorkspaceStart, open_workspace
from cortexshift.domain.errors import (
    GitNotInstalledError,
    GitProbeError,
    GitProbeTimeoutError,
    NotAGitRepositoryError,
)
from cortexshift.domain.git import (
    GitSnapshot,
    RepositoryInspection,
    RepositoryInspectionStatus,
)
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
        self, start_path: WorkspaceStart = None
    ) -> Iterator[tuple[WorkspaceIdentity, RepositorySnapshotStore]]:
        """Locate the initialized project and yield its snapshot store.

        A store opened here is closed on exit. An injected store belongs to its caller
        and is left open, so callers that share one connection (the MCP server, for
        instance) keep working.
        """
        override = self._store if isinstance(self._store, SQLiteStateStore) else None
        workspace, store = open_workspace(
            start_path, locator=self._locator, auto_migrate=True, store_override=override
        )
        try:
            yield workspace, self._store if self._store is not None else store
        finally:
            if override is None:
                store.close()

    def inspect_repository(self, start_path: WorkspaceStart = None) -> RepositoryInspection:
        """Perform a live, non-persisting inspection of the repository."""
        with self._resolve_project_and_store(start_path) as (workspace, _):
            return self._inspector.inspect(
                project_root=workspace.root, project_id=workspace.project.id
            )

    def capture_snapshot(self, start_path: WorkspaceStart = None) -> GitSnapshot:
        """Perform live inspection and persist the snapshot if successful.

        Raises:
            GitNotInstalledError: If Git is not found in PATH.
            NotAGitRepositoryError: If the project is not in a Git repository.
            GitProbeTimeoutError: If inspection timed out.
            GitProbeError: If inspection failed.
        """
        with self._resolve_project_and_store(start_path) as (workspace, store):
            return self._capture(workspace, store)

    def _capture(
        self,
        workspace: WorkspaceIdentity,
        store: RepositorySnapshotStore,
    ) -> GitSnapshot:
        """Inspect the working tree and persist the resulting snapshot."""
        inspection = self._inspector.inspect(
            project_root=workspace.root, project_id=workspace.project.id
        )

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

        workspace.revalidate()
        store.save_snapshot(inspection.snapshot)
        return inspection.snapshot

    def list_snapshots(
        self,
        project_id: str | None = None,
        limit: int = 10,
        start_path: WorkspaceStart = None,
    ) -> list[GitSnapshot]:
        """List historical snapshots for a project, newest first."""
        with self._resolve_project_and_store(start_path) as (workspace, store):
            pid = project_id or workspace.project.id
            return store.list_snapshots(project_id=pid, limit=limit)

    def get_snapshot(
        self, snapshot_id: str, start_path: WorkspaceStart = None
    ) -> GitSnapshot | None:
        """Retrieve a stored snapshot by ID."""
        with self._resolve_project_and_store(start_path) as (_, store):
            return store.get_snapshot(snapshot_id)

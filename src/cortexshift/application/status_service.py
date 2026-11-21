"""Application service for assembling project status and active task metrics."""

from cortexshift.adapters.workspace_lease import FileWorkspaceLeaseManager
from cortexshift.application.locator import (
    DATABASE_FILE_NAME,
    STATE_DIR_NAME,
    ProjectLocator,
)
from cortexshift.application.task_startup_context import objective_retrieval
from cortexshift.application.workspace import WorkspaceStart, open_workspace
from cortexshift.domain.session import CONTINUED_FROM_SESSION_KEY, UNFINALIZED_SESSION_STATUSES
from cortexshift.domain.status import (
    ActiveTaskSummary,
    InvocationSummary,
    ProgressSummary,
    ProjectStatus,
)
from cortexshift.ports.workspace_lease import WorkspaceLeaseManager


class ProjectStatusService:
    """Queries project persistence to construct canonical status models."""

    def __init__(
        self,
        locator: type[ProjectLocator] = ProjectLocator,
        lease_manager: WorkspaceLeaseManager | None = None,
    ) -> None:
        self.locator = locator
        self._leases = lease_manager or FileWorkspaceLeaseManager()

    def get_status(self, start_path: WorkspaceStart = None) -> ProjectStatus:
        """Inspect the project root for the given path and return structured status.

        Args:
            start_path: Starting path or project directory. Defaults to Path.cwd().

        Returns:
            A populated ProjectStatus entity.

        Raises:
            ProjectNotInitializedError: If no initialized project root is found.
            DatabaseStateError: If the database is inaccessible or corrupted.
            UnsupportedSchemaVersionError: If the database is at an incompatible schema version.
        """
        workspace, store = open_workspace(start_path, locator=self.locator)
        project = workspace.project
        with store:
            try:
                workspace_busy = self._leases.get_lease(workspace.root).is_locked()
            except OSError:
                workspace_busy = None
            schema_version = store.get_schema_version()
            active_id = store.get_active_task_id(project.id)

            active_task_summary: ActiveTaskSummary | None = None
            latest_session: InvocationSummary | None = None
            if active_id is not None:
                active_task = store.get_task(active_id)
                if active_task is not None:
                    active_task_summary = ActiveTaskSummary(
                        id=active_task.id,
                        title=active_task.title,
                        status=active_task.status,
                        objective=active_task.objective,
                        progress=ProgressSummary(
                            completed=len(active_task.completed_items),
                            remaining=len(active_task.remaining_items),
                            issues=len(active_task.known_issues),
                        ),
                    )
                    sessions = store.list_sessions(task_id=active_task.id, limit=1)
                    if sessions:
                        session = sessions[0]
                        handoff_id = session.metadata.get("handoff_id")
                        handoff = (
                            store.get_handoff(handoff_id) if isinstance(handoff_id, str) else None
                        )
                        continuation = session.metadata.get(CONTINUED_FROM_SESSION_KEY)
                        latest_session = InvocationSummary(
                            id=session.id,
                            provider_id=session.provider_id,
                            status=session.status,
                            resumed_from_session_id=session.resumed_from_session_id,
                            continued_from_session_id=(
                                continuation if isinstance(continuation, str) else None
                            ),
                            objective_retrieval=objective_retrieval(
                                session, len(active_task.objective)
                            ),
                            handoff_status=handoff.status if handoff else None,
                        )

            state_rel_path = f"{STATE_DIR_NAME}/{DATABASE_FILE_NAME}"
            return ProjectStatus(
                project_id=project.id,
                name=project.name,
                repo_path=project.repo_path,
                state_file=state_rel_path,
                schema_version=schema_version,
                active_task=active_task_summary,
                latest_session=latest_session,
                workspace_busy=workspace_busy,
                unfinalized_sessions=len(
                    store.list_sessions(
                        project_id=project.id, statuses=UNFINALIZED_SESSION_STATUSES, limit=None
                    )
                ),
            )

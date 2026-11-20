"""Application service for assembling project status and active task metrics."""

from cortexshift.application.locator import (
    DATABASE_FILE_NAME,
    STATE_DIR_NAME,
    ProjectLocator,
)
from cortexshift.application.workspace import WorkspaceStart, open_workspace
from cortexshift.domain.status import ActiveTaskSummary, ProgressSummary, ProjectStatus


class ProjectStatusService:
    """Queries project persistence to construct canonical status models."""

    def __init__(self, locator: type[ProjectLocator] = ProjectLocator) -> None:
        self.locator = locator

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
            schema_version = store.get_schema_version()
            active_id = store.get_active_task_id(project.id)

            active_task_summary: ActiveTaskSummary | None = None
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

            state_rel_path = f"{STATE_DIR_NAME}/{DATABASE_FILE_NAME}"
            return ProjectStatus(
                project_id=project.id,
                name=project.name,
                repo_path=project.repo_path,
                state_file=state_rel_path,
                schema_version=schema_version,
                active_task=active_task_summary,
            )

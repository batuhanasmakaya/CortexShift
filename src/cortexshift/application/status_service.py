"""Application service for assembling project status and active task metrics."""

from pathlib import Path

from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.application.locator import (
    DATABASE_FILE_NAME,
    STATE_DIR_NAME,
    ProjectLocator,
)
from cortexshift.domain.errors import ProjectNotInitializedError
from cortexshift.domain.status import ActiveTaskSummary, ProgressSummary, ProjectStatus


class ProjectStatusService:
    """Queries project persistence to construct canonical status models."""

    def __init__(self, locator: type[ProjectLocator] = ProjectLocator) -> None:
        self.locator = locator

    def get_status(self, start_path: Path | str | None = None) -> ProjectStatus:
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
        raw_path = Path(start_path) if start_path is not None else Path.cwd()
        project_root = self.locator.find_project_root(raw_path)
        if project_root is None:
            raise ProjectNotInitializedError()

        db_path = self.locator.get_database_path(project_root)
        with SQLiteStateStore(db_path, auto_migrate=False) as store:
            project = store.get_default_project()
            if project is None:
                raise ProjectNotInitializedError()

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

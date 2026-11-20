"""Application service for initializing CortexShift projects."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.application.locator import ProjectLocator
from cortexshift.application.workspace import open_project_state, project_database_path
from cortexshift.domain.project import Project


class ProjectInitResult(BaseModel):
    """Result of project initialization."""

    model_config = ConfigDict(frozen=True)

    project: Project
    state_path: str
    already_initialized: bool


class ProjectInitializationService:
    """Orchestrates safe, idempotent project initialization."""

    def __init__(self, locator: type[ProjectLocator] = ProjectLocator) -> None:
        self.locator = locator

    def initialize(
        self,
        target_path: Path | str | None = None,
        name: str | None = None,
    ) -> ProjectInitResult:
        """Initialize a directory as a CortexShift project.

        Args:
            target_path: Target directory to initialize. Defaults to Path.cwd().
            name: Optional custom project name. Defaults to the directory name.

        Returns:
            ProjectInitResult indicating success, project details, and idempotency status.

        Raises:
            ProjectConflictError: If the directory contains a database from another path.
            DatabaseStateError: If the database cannot be created or opened.
        """
        raw_path = Path(target_path) if target_path is not None else Path.cwd()
        resolved_path = raw_path.resolve()
        resolved_path.mkdir(parents=True, exist_ok=True)

        db_path = project_database_path(resolved_path, self.locator)
        if db_path.is_file():
            workspace, store = open_project_state(
                resolved_path, locator=self.locator, auto_migrate=True, allow_empty_database=True
            )
        else:
            workspace, store = None, SQLiteStateStore(db_path)

        with store:
            if workspace is not None:
                return ProjectInitResult(
                    project=workspace.project,
                    state_path=str(db_path),
                    already_initialized=True,
                )

            project_name = name.strip() if name and name.strip() else resolved_path.name
            project = Project(name=project_name, repo_path=str(resolved_path))
            store.save_project(project)

        return ProjectInitResult(
            project=project,
            state_path=str(db_path),
            already_initialized=False,
        )

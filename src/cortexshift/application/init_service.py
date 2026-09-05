"""Application service for initializing CortexShift projects."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.application.locator import ProjectLocator
from cortexshift.domain.errors import ProjectConflictError
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

        db_path = self.locator.get_database_path(resolved_path)

        # Check for existing initialization
        if self.locator.is_initialized(resolved_path):
            with SQLiteStateStore(db_path, auto_migrate=True) as store:
                existing_project = store.get_default_project()
                if existing_project is not None:
                    # Validate path consistency to guard against copied state
                    if Path(existing_project.repo_path).resolve() != resolved_path:
                        msg = (
                            f"Database at {db_path} has recorded repository path "
                            f"'{existing_project.repo_path}', which does not match "
                            f"'{resolved_path}'."
                        )
                        raise ProjectConflictError(msg)
                    return ProjectInitResult(
                        project=existing_project,
                        state_path=str(db_path),
                        already_initialized=True,
                    )

        # Fresh initialization
        project_name = name.strip() if name and name.strip() else resolved_path.name
        project = Project(
            name=project_name,
            repo_path=str(resolved_path),
        )

        with SQLiteStateStore(db_path, auto_migrate=True) as store:
            store.save_project(project)

        return ProjectInitResult(
            project=project,
            state_path=str(db_path),
            already_initialized=False,
        )

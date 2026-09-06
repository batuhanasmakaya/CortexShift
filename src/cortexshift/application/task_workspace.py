"""Project-root scoped Task operations with managed persistence lifecycle.

`TaskService` operates on an already-open `StateStore`. Outer adapters that address a
project by path — the TUI control center in particular — must not open SQLite themselves,
so this service resolves the initialized project root, manages store lifecycle, and
delegates every rule to `TaskService` and the canonical `Task` domain model.

It contains no task business rules of its own.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.application.locator import ProjectLocator
from cortexshift.application.task_service import TaskService
from cortexshift.domain.errors import NoActiveTaskError, ProjectNotInitializedError
from cortexshift.domain.project import Project
from cortexshift.domain.task import Task


@dataclass(frozen=True)
class ResolvedTaskWorkspace:
    """An opened project workspace bound to a single canonical Project."""

    project_root: Path
    project: Project
    tasks: TaskService


class TaskWorkspaceService:
    """Resolves a project by path and applies canonical Task operations to it."""

    def __init__(self, project_locator: type[ProjectLocator] = ProjectLocator) -> None:
        self._locator = project_locator

    @contextmanager
    def open(self, start_dir: Path | str | None = None) -> Iterator[ResolvedTaskWorkspace]:
        """Open the nearest initialized project and yield its Task service.

        Raises:
            ProjectNotInitializedError: If no initialized project root is found.
        """
        start_path = Path(start_dir) if start_dir is not None else None
        project_root = self._locator.find_project_root(start_path)
        if project_root is None:
            raise ProjectNotInitializedError()

        db_path = self._locator.get_database_path(project_root)
        store = SQLiteStateStore(db_path, auto_migrate=False)
        try:
            project = store.get_default_project()
            if project is None:
                raise ProjectNotInitializedError()
            yield ResolvedTaskWorkspace(
                project_root=project_root,
                project=project,
                tasks=TaskService(store),
            )
        finally:
            store.close()

    def list_tasks(self, start_dir: Path | str | None = None) -> list[Task]:
        """List every Task belonging to the resolved project."""
        with self.open(start_dir) as workspace:
            return workspace.tasks.list_tasks(workspace.project.id)

    def get_active_task(self, start_dir: Path | str | None = None) -> Task | None:
        """Return the active Task for the resolved project, or None."""
        with self.open(start_dir) as workspace:
            return workspace.tasks.get_active_task(workspace.project.id)

    def start_task(
        self,
        title: str,
        objective: str,
        requirements: list[str] | None = None,
        constraints: list[str] | None = None,
        set_active: bool = True,
        start_dir: Path | str | None = None,
    ) -> Task:
        """Create a new Task using the canonical creation rules."""
        with self.open(start_dir) as workspace:
            return workspace.tasks.start_task(
                project_id=workspace.project.id,
                title=title,
                objective=objective,
                requirements=requirements,
                constraints=constraints,
                set_active=set_active,
            )

    def activate_task(self, task_id: str, start_dir: Path | str | None = None) -> Task:
        """Activate an existing Task using canonical activation rules.

        Raises:
            TaskNotFoundError: If the Task does not belong to the resolved project.
            TaskNotActivatableError: If the Task is in a terminal status.
        """
        with self.open(start_dir) as workspace:
            return workspace.tasks.activate_task(workspace.project.id, task_id)

    def set_current_work(
        self,
        current_work: str | None,
        start_dir: Path | str | None = None,
    ) -> Task:
        """Replace the active Task's in-flight work description."""
        with self.open(start_dir) as workspace:
            return workspace.tasks.update_task(
                project_id=workspace.project.id,
                current_work=current_work,
                clear_current_work=current_work is None,
            )

    def mark_completed(self, items: list[str], start_dir: Path | str | None = None) -> Task:
        """Mark items completed on the active Task and drop them from remaining.

        Applies the canonical `Task.complete_items` rule — the same rule MCP agents use —
        so the Task itself is never completed as a side effect.
        """
        with self.open(start_dir) as workspace:
            task = self._require_active(workspace)
            updated = task.complete_items(items)
            workspace.tasks.store.save_task(updated)
            return updated

    def add_remaining(self, items: list[str], start_dir: Path | str | None = None) -> Task:
        """Append newly discovered remaining items to the active Task."""
        with self.open(start_dir) as workspace:
            return workspace.tasks.update_task(
                project_id=workspace.project.id,
                add_remaining=items,
            )

    def record_issues(self, items: list[str], start_dir: Path | str | None = None) -> Task:
        """Record known issues or blockers on the active Task."""
        with self.open(start_dir) as workspace:
            return workspace.tasks.update_task(
                project_id=workspace.project.id,
                add_issues=items,
            )

    @staticmethod
    def _require_active(workspace: ResolvedTaskWorkspace) -> Task:
        """Return the active Task or fail with the canonical error."""
        task = workspace.tasks.get_active_task(workspace.project.id)
        if task is None:
            raise NoActiveTaskError("No active task to update.")
        return task

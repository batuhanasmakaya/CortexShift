"""Application service managing Task lifecycle and active task pointer."""

from collections.abc import Callable

from cortexshift.domain.errors import (
    ActiveTaskExistsError,
    NoActiveTaskError,
    TaskAlreadyCompletedError,
    TaskNotActivatableError,
    TaskNotFoundError,
    TaskTerminalError,
)
from cortexshift.domain.task import Task, TaskStatus
from cortexshift.ports.state_store import StateStore


class TaskService:
    """Orchestrates persistent Task creation, updates, and activation transitions."""

    def __init__(self, store: StateStore) -> None:
        self._store = store

    def start_task(
        self,
        project_id: str,
        title: str,
        objective: str,
        requirements: list[str] | None = None,
        constraints: list[str] | None = None,
        set_active: bool = True,
        *,
        require_no_active: bool = False,
    ) -> Task:
        """Create a new task, persist it, and optionally set it as the active task.

        Args:
            project_id: Canonical ID of the owning project.
            title: Short descriptive task title.
            objective: High-level goal and scope of the task.
            requirements: Optional initial list of requirement strings.
            constraints: Optional initial list of constraint strings.
            set_active: Whether to set this task as active immediately (defaults to True).
            require_no_active: Refuse creation if the project's active pointer is occupied.

        Returns:
            The created Task entity.
        """
        initial_status = TaskStatus.IN_PROGRESS if set_active else TaskStatus.PENDING
        task = Task(
            project_id=project_id,
            title=title,
            objective=objective,
            requirements=requirements or [],
            constraints=constraints or [],
            status=initial_status,
        )

        with self._store.task_write_transaction():
            if require_no_active and self._store.get_active_task_id(project_id) is not None:
                raise ActiveTaskExistsError("An active Task already exists.")
            self._store.save_task(task)
            if set_active:
                self._store.set_active_task_id(project_id, task.id)
        return task

    def get_task(self, task_id: str) -> Task:
        """Retrieve a task by identifier.

        Raises:
            TaskNotFoundError: If the task does not exist.
        """
        task = self._store.get_task(task_id)
        if task is None:
            raise TaskNotFoundError(task_id)
        return task

    def get_active_task(self, project_id: str) -> Task | None:
        """Retrieve the currently active task for a project, or None if no task is active."""
        active_id = self._store.get_active_task_id(project_id)
        if active_id is None:
            return None
        return self._store.get_task(active_id)

    def require_active_task(self, project_id: str) -> Task:
        """Select the active Task for a provider operation."""
        task = self.get_active_task(project_id)
        if task is None or task.project_id != project_id:
            raise NoActiveTaskError()
        self._require_nonterminal(task, "continue")
        return task

    def list_tasks(self, project_id: str) -> list[Task]:
        """List all tasks associated with a project."""
        return self._store.list_tasks(project_id)

    def activate_task(self, project_id: str, task_id: str) -> Task:
        """Make an existing task the active task for the project.

        Args:
            project_id: Canonical ID of the owning project.
            task_id: Identifier of the task to activate.

        Returns:
            The newly activated Task entity.

        Raises:
            TaskNotFoundError: If the task does not exist or does not belong to the project.
            TaskNotActivatableError: If the task is in a terminal status (completed/cancelled).
        """
        with self._store.task_write_transaction():
            task = self._get_project_task(project_id, task_id, "activate")
            if task.is_terminal:
                raise TaskNotActivatableError(
                    f"Cannot activate task '{task_id}' because it is in terminal status "
                    f"'{task.status.value}'."
                )

            self._store.set_active_task_id(project_id, task.id)
            return task

    def complete_task(self, project_id: str, task_id: str | None = None) -> Task:
        """Mark a task as completed.

        If task_id is None, the currently active task is completed.
        If the completed task is currently active, the active task pointer is cleared.

        Args:
            project_id: Canonical ID of the owning project.
            task_id: Optional identifier of the task to complete.

        Returns:
            The updated Task entity.

        Raises:
            NoActiveTaskError: If task_id is None and no task is currently active.
            TaskNotFoundError: If the specified task does not exist.
            TaskAlreadyCompletedError: If the task is already completed.
            TaskTerminalError: If the task is cancelled.
        """
        with self._store.task_write_transaction():
            task = self._get_project_task(project_id, task_id, "complete")
            if task.status == TaskStatus.COMPLETED:
                raise TaskAlreadyCompletedError(task.id)
            self._require_nonterminal(task, "complete")

            completed = task.mark_completed()
            self._store.save_task(completed)
            if self._store.get_active_task_id(project_id) == task.id:
                self._store.set_active_task_id(project_id, None)
            return completed

    def update_task(
        self,
        project_id: str,
        task_id: str | None = None,
        current_work: str | None = None,
        clear_current_work: bool = False,
        add_completed: list[str] | None = None,
        add_remaining: list[str] | None = None,
        add_issues: list[str] | None = None,
        *,
        complete_items: list[str] | None = None,
        authorize: Callable[[], None] | None = None,
    ) -> Task:
        """Update fields and progression lists on a task.

        Defaults to modifying the active task if task_id is None.

        Args:
            project_id: Canonical ID of the owning project.
            task_id: Optional specific task identifier.
            current_work: Optional new text describing in-flight work.
            clear_current_work: If True, clears current_work to None.
            add_completed: Optional list of completed item strings to append.
            add_remaining: Optional list of remaining item strings to append.
            add_issues: Optional list of known issue strings to append.
            complete_items: Optional items to add to completed and remove from remaining.
            authorize: Optional caller-specific check under the Task write reservation.

        Returns:
            The updated Task entity.

        Raises:
            NoActiveTaskError: If task_id is None and no task is active.
            TaskNotFoundError: If the specified task does not exist.
            TaskTerminalError: If the task is completed or cancelled.
        """
        with self._store.task_write_transaction():
            if authorize is not None:
                authorize()
            task = self._get_project_task(project_id, task_id, "update")
            self._require_nonterminal(task, "update")

            if clear_current_work:
                task = task.with_current_work(None)
            elif current_work is not None:
                task = task.with_current_work(current_work)

            if add_completed:
                task = task.add_completed_items(add_completed)
            if add_remaining:
                task = task.add_remaining_items(add_remaining)
            if add_issues:
                task = task.add_known_issues(add_issues)
            if complete_items:
                task = task.complete_items(complete_items)

            self._store.save_task(task)
            return task

    def _get_project_task(self, project_id: str, task_id: str | None, action: str) -> Task:
        """Resolve and validate a mutation target within the caller's write transaction."""
        if task_id is None:
            task_id = self._store.get_active_task_id(project_id)
            if task_id is None:
                raise NoActiveTaskError(f"No active task to {action}.")

        task = self.get_task(task_id)
        if task.project_id != project_id:
            raise TaskNotFoundError(task_id)
        return task

    @staticmethod
    def _require_nonterminal(task: Task, action: str) -> None:
        if task.is_terminal:
            raise TaskTerminalError(
                f"Cannot {action} task '{task.id}' because it is in terminal status "
                f"'{task.status.value}'."
            )

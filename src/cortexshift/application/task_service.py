"""Application service managing Task lifecycle and active task pointer."""

from cortexshift.domain.errors import (
    NoActiveTaskError,
    TaskAlreadyCompletedError,
    TaskNotActivatableError,
    TaskNotFoundError,
)
from cortexshift.domain.task import Task, TaskStatus
from cortexshift.ports.state_store import StateStore


class TaskService:
    """Orchestrates persistent Task creation, updates, and activation transitions."""

    def __init__(self, store: StateStore) -> None:
        self.store = store

    def start_task(
        self,
        project_id: str,
        title: str,
        objective: str,
        requirements: list[str] | None = None,
        constraints: list[str] | None = None,
        set_active: bool = True,
    ) -> Task:
        """Create a new task, persist it, and optionally set it as the active task.

        Args:
            project_id: Canonical ID of the owning project.
            title: Short descriptive task title.
            objective: High-level goal and scope of the task.
            requirements: Optional initial list of requirement strings.
            constraints: Optional initial list of constraint strings.
            set_active: Whether to set this task as active immediately (defaults to True).

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

        self.store.save_task(task)
        if set_active:
            self.store.set_active_task_id(project_id, task.id)
        return task

    def get_task(self, task_id: str) -> Task:
        """Retrieve a task by identifier.

        Raises:
            TaskNotFoundError: If the task does not exist.
        """
        task = self.store.get_task(task_id)
        if task is None:
            raise TaskNotFoundError(task_id)
        return task

    def get_active_task(self, project_id: str) -> Task | None:
        """Retrieve the currently active task for a project, or None if no task is active."""
        active_id = self.store.get_active_task_id(project_id)
        if active_id is None:
            return None
        return self.store.get_task(active_id)

    def list_tasks(self, project_id: str) -> list[Task]:
        """List all tasks associated with a project."""
        return self.store.list_tasks(project_id)

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
        task = self.get_task(task_id)
        if task.project_id != project_id:
            raise TaskNotFoundError(task_id)

        if task.is_terminal:
            msg = (
                f"Cannot activate task '{task_id}' because it is in terminal status "
                f"'{task.status.value}'."
            )
            raise TaskNotActivatableError(msg)

        self.store.set_active_task_id(project_id, task.id)
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
        """
        if task_id is None:
            active_id = self.store.get_active_task_id(project_id)
            if active_id is None:
                raise NoActiveTaskError("No active task to complete.")
            target_task = self.get_task(active_id)
        else:
            target_task = self.get_task(task_id)
            if target_task.project_id != project_id:
                raise TaskNotFoundError(task_id)

        if target_task.status == TaskStatus.COMPLETED:
            raise TaskAlreadyCompletedError(target_task.id)

        completed_task = target_task.mark_completed()
        self.store.save_task(completed_task)

        # Clear active task pointer if this task was active
        active_id = self.store.get_active_task_id(project_id)
        if active_id == completed_task.id:
            self.store.set_active_task_id(project_id, None)

        return completed_task

    def update_task(
        self,
        project_id: str,
        task_id: str | None = None,
        current_work: str | None = None,
        clear_current_work: bool = False,
        add_completed: list[str] | None = None,
        add_remaining: list[str] | None = None,
        add_issues: list[str] | None = None,
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

        Returns:
            The updated Task entity.

        Raises:
            NoActiveTaskError: If task_id is None and no task is active.
            TaskNotFoundError: If the specified task does not exist.
        """
        if task_id is None:
            active_id = self.store.get_active_task_id(project_id)
            if active_id is None:
                raise NoActiveTaskError("No active task to update.")
            target_task = self.get_task(active_id)
        else:
            target_task = self.get_task(task_id)
            if target_task.project_id != project_id:
                raise TaskNotFoundError(task_id)

        updated = target_task
        if clear_current_work:
            updated = updated.with_current_work(None)
        elif current_work is not None:
            updated = updated.with_current_work(current_work)

        if add_completed:
            updated = updated.add_completed_items(add_completed)
        if add_remaining:
            updated = updated.add_remaining_items(add_remaining)
        if add_issues:
            updated = updated.add_known_issues(add_issues)

        self.store.save_task(updated)
        return updated

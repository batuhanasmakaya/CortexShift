"""Start a native provider on a new or explicitly selected active Task."""

from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from cortexshift.application.run_service import RunService
from cortexshift.application.session_launcher import ProviderSessionLauncher
from cortexshift.application.task_service import TaskService
from cortexshift.application.workspace import WorkspaceStart, open_workspace
from cortexshift.domain.errors import (
    ActiveTaskExistsError,
    TaskStartupError,
    TerminalRequiredError,
    WorkspaceLockedError,
)
from cortexshift.domain.session import STARTUP_OBJECTIVE_SERVED_KEY, Session
from cortexshift.domain.task import Task
from cortexshift.ports.provider import STARTUP_PROMPT, ManagedMcpBinder


@dataclass(frozen=True)
class StartResult:
    session: Session
    objective_length: int

    @property
    def objective_chars_served(self) -> int:
        return int(self.session.metadata[STARTUP_OBJECTIVE_SERVED_KEY])


class StartService(RunService):
    """Share provider discovery and the Session launcher with established workflows."""

    def start(
        self,
        provider_name: str,
        *,
        objective: str | None = None,
        task_id: str | None = None,
        replace_active: bool = False,
        start_dir: WorkspaceStart = None,
        on_launch: Callable[[str, Path, bool], None] | None = None,
        on_prepared: Callable[[Session], None] | None = None,
    ) -> StartResult:
        if (objective is None) == (task_id is None):
            raise TaskStartupError("Supply an objective or an existing Task ID.")
        if replace_active and task_id is not None:
            raise TaskStartupError("--replace-active requires a new objective.")
        if objective is not None:
            if not objective or objective.isspace():
                raise TaskStartupError("The Task objective is empty.")
            try:
                objective.encode("utf-8")
            except UnicodeEncodeError as err:
                raise TaskStartupError("The Task objective must be valid UTF-8.") from err

        workspace, store = open_workspace(start_dir)
        with closing(store):
            adapter = self._get_adapter(provider_name)
            if not isinstance(adapter, ManagedMcpBinder):
                raise TaskStartupError(
                    f"{adapter.display_name} does not support automatic Task context for start. "
                    f"Use cortexshift task start and cortexshift run {adapter.provider_id}."
                )
            executable = self._resolve_executable(adapter)
            if not self._is_tty():
                raise TerminalRequiredError("Starting a native provider requires a terminal (TTY).")

            preparation_error = (
                f"Could not prepare automatic Task context for {adapter.display_name}."
            )
            try:
                spec = adapter.build_launch_spec(workspace.root, executable, STARTUP_PROMPT)
            except Exception as err:
                raise TaskStartupError(preparation_error) from err
            if not spec.interactive or not spec.prompt_supplied:
                raise TaskStartupError(preparation_error)
            workspace.validate_launch_root(spec.cwd)
            lease = self._lease_manager.get_lease(workspace.root)
            if not lease.acquire():
                raise WorkspaceLockedError(lock_path=lease.lock_path)
            try:
                workspace.revalidate()
                task = self._select_task(
                    TaskService(store), workspace.project.id, objective, task_id, replace_active
                )
                launcher = ProviderSessionLauncher(
                    self._runner, store, self._checkpoint_service, workspace
                )

                def notify_launch(_: object, session: Session) -> None:
                    if on_prepared is not None:
                        on_prepared(session)
                    if on_launch is not None:
                        on_launch(adapter.display_name, workspace.root, objective is not None)

                try:
                    session = launcher.start_session(
                        task.id,
                        provider_id=adapter.provider_id,
                        native_session_id=spec.native_session_id,
                        metadata={STARTUP_OBJECTIVE_SERVED_KEY: 0},
                    )
                    session = launcher.run(
                        session, spec, on_launch=notify_launch, mcp_binder=adapter
                    )
                except Exception as err:
                    # Provider errors may include the managed binding or native argv.
                    raise TaskStartupError(
                        f"Could not launch {adapter.display_name}. The Task was saved. "
                        f"Retry with cortexshift start {adapter.provider_id} --task {task.id}."
                    ) from err
                return StartResult(session=session, objective_length=len(task.objective))
            finally:
                lease.release()

    @staticmethod
    def _select_task(
        tasks: TaskService,
        project_id: str,
        objective: str | None,
        task_id: str | None,
        replace_active: bool,
    ) -> Task:
        if objective is None:
            task = tasks.get_active_task(project_id)
            if (
                task is None
                or task.id != task_id
                or task.project_id != project_id
                or task.is_terminal
            ):
                raise TaskStartupError(
                    "The requested Task must be active in this workspace. "
                    "Find it with cortexshift task list and select it with "
                    "cortexshift task activate ID."
                )
            return task

        first_line = objective.lstrip().partition("\n")[0].partition("\r")[0].lstrip("#").strip()
        title = "".join(char for char in first_line[:80] if char.isprintable()).strip()
        try:
            return tasks.start_task(
                project_id, title or "New Task", objective, require_no_active=not replace_active
            )
        except ActiveTaskExistsError as err:
            raise TaskStartupError(
                "An active Task already exists. Use --replace-active to start a new Task "
                "and keep the previous Task, or use --task ID to work on the active Task."
            ) from err

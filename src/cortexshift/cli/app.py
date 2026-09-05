"""CortexShift Typer CLI application."""

import json
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich import box
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from cortexshift import __version__
from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.application.checkpoint_service import CheckpointService
from cortexshift.application.doctor import DoctorService, UnknownProviderError
from cortexshift.application.handoff_renderer import RenderedHandoffContext
from cortexshift.application.handoff_service import HandoffService
from cortexshift.application.init_service import ProjectInitializationService
from cortexshift.application.locator import DATABASE_FILE_NAME, STATE_DIR_NAME, ProjectLocator
from cortexshift.application.native_session import is_native_resumable, native_capabilities
from cortexshift.application.recovery_service import RecoveryReport, RecoveryService
from cortexshift.application.repository_service import RepositoryService
from cortexshift.application.resume_service import ResumeDryRunResult, ResumeService
from cortexshift.application.run_service import ProviderRuntimeRegistry, RunService
from cortexshift.application.session_service import SessionService
from cortexshift.application.status_service import ProjectStatusService
from cortexshift.application.switch_service import SwitchService
from cortexshift.application.task_service import TaskService
from cortexshift.domain.checkpoint import CheckpointKind, CheckpointRecord
from cortexshift.domain.doctor import AuthenticationStatus, DoctorReport
from cortexshift.domain.errors import (
    CheckpointNotFoundError,
    CortexShiftError,
    DatabaseStateError,
    HandoffDeliveryError,
    HandoffNotFoundError,
    InvalidCheckpointInputError,
    NativeResumeError,
    NoActiveTaskError,
    NoSourceSessionError,
    ProjectConflictError,
    ProjectNotInitializedError,
    ProviderNotFoundError,
    RepositoryInspectionError,
    SameProviderSwitchError,
    SessionNotFoundError,
    SessionRecoveryError,
    SessionTaskMismatchError,
    SnapshotNotFoundError,
    StateCorruptionError,
    TaskAlreadyCompletedError,
    TaskNotActivatableError,
    TaskNotFoundError,
    TerminalRequiredError,
    UnsupportedPromptError,
    UnsupportedSchemaVersionError,
    WorkspaceLockedError,
)
from cortexshift.domain.git import (
    RepositoryInspectionStatus,
)
from cortexshift.domain.handoff import HandoffRecord, HandoffStatus
from cortexshift.domain.launch import LaunchSpecification
from cortexshift.domain.project import Project
from cortexshift.domain.provider import ProviderId
from cortexshift.domain.session import Session, SessionStatus
from cortexshift.domain.status import ProjectStatus
from cortexshift.domain.task import Task, TaskStatus

console = Console()
err_console = Console(stderr=True)

app = typer.Typer(
    name="cortexshift",
    help="Switch agents. Keep the context. Provider-agnostic task handoff for coding agents.",
    add_completion=False,
)

task_app = typer.Typer(
    name="task",
    help="Manage persistent development tasks.",
    no_args_is_help=True,
)
app.add_typer(task_app, name="task")

repo_app = typer.Typer(
    name="repo",
    help="Inspect and snapshot Git repository state.",
    no_args_is_help=True,
)
app.add_typer(repo_app, name="repo")

session_app = typer.Typer(
    name="session",
    help="Inspect agent execution session history.",
    no_args_is_help=True,
)
app.add_typer(session_app, name="session")

handoff_app = typer.Typer(
    name="handoff",
    help="Preview and inspect canonical cross-provider handoffs.",
    no_args_is_help=True,
)
app.add_typer(handoff_app, name="handoff")

checkpoint_app = typer.Typer(
    name="checkpoint",
    help="Manage immutable development checkpoints.",
    no_args_is_help=True,
)
app.add_typer(checkpoint_app, name="checkpoint")


def print_version() -> None:
    """Print the version string."""
    console.print(f"CortexShift {__version__}")


def _handle_error(err: Exception) -> None:
    """Render friendly domain errors without Python tracebacks and exit."""
    if isinstance(err, ProjectNotInitializedError):
        err_console.print("\nCortexShift is not initialized here.\n\nRun:\n  cortexshift init\n")
        raise typer.Exit(code=1)
    if isinstance(err, NoActiveTaskError):
        err_console.print(
            "\nNo active task.\n\n"
            "No active CortexShift task.\n\n"
            "Create or activate one first:\n\n  cortexshift task start ...\n"
        )
        raise typer.Exit(code=1)

    if isinstance(
        err,
        (
            UnsupportedPromptError,
            ProviderNotFoundError,
            WorkspaceLockedError,
            TerminalRequiredError,
            NoSourceSessionError,
            SameProviderSwitchError,
            SessionTaskMismatchError,
            HandoffDeliveryError,
            NativeResumeError,
            CheckpointNotFoundError,
            InvalidCheckpointInputError,
            SessionRecoveryError,
        ),
    ):
        err_console.print(f"\n{err}\n")
        raise typer.Exit(code=1)
    if isinstance(
        err,
        (
            TaskNotFoundError,
            TaskNotActivatableError,
            TaskAlreadyCompletedError,
            ProjectConflictError,
            UnsupportedSchemaVersionError,
            StateCorruptionError,
            DatabaseStateError,
            RepositoryInspectionError,
            SnapshotNotFoundError,
            SessionNotFoundError,
            HandoffNotFoundError,
            UnknownProviderError,
        ),
    ):
        err_console.print(f"[red]Error:[/red] {err}")
        raise typer.Exit(code=1)
    if isinstance(err, CortexShiftError):
        err_console.print(f"[red]Error:[/red] {err}")
        raise typer.Exit(code=1)
    err_console.print(f"[red]Unexpected error:[/red] {err}")
    raise typer.Exit(code=1)


def _get_project_and_store() -> tuple[Project, SQLiteStateStore]:
    """Discover initialized project root and return canonical Project and SQLiteStateStore."""
    project_root = ProjectLocator.find_project_root()
    if project_root is None:
        raise ProjectNotInitializedError()

    db_path = ProjectLocator.get_database_path(project_root)
    store = SQLiteStateStore(db_path, auto_migrate=False)
    project = store.get_default_project()
    if project is None:
        store.close()
        raise ProjectNotInitializedError()

    return project, store


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version_flag: Annotated[
        bool,
        typer.Option(
            "--version",
            "-v",
            help="Show CortexShift version and exit.",
            is_eager=True,
        ),
    ] = False,
) -> None:
    """CortexShift root command callback."""
    if version_flag:
        print_version()
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())


@app.command(name="version")
def version_cmd() -> None:
    """Display the installed CortexShift version."""
    print_version()


# --- Doctor Command ---


def _render_rich_doctor(report: DoctorReport) -> None:
    """Render the doctor diagnostic report using Rich tables and formatting."""
    console.print("\n[bold]CortexShift Doctor[/bold]\n")

    # Environment section
    env_table = Table.grid(padding=(0, 2))
    env_table.add_column(style="dim", min_width=14)
    env_table.add_column()
    env_table.add_row("CortexShift", report.cortexshift_version)
    env_table.add_row("Python", report.python_version)
    env_table.add_row("Platform", f"{report.platform.system} / {report.platform.machine}")

    console.print("[bold]Environment[/bold]")
    console.print(env_table)
    console.print()

    # Providers table
    table = Table(box=box.ROUNDED, show_header=True, header_style="bold")
    table.add_column("Provider", min_width=14)
    table.add_column("CLI", justify="center", width=5)
    table.add_column("Version", min_width=12)
    table.add_column("Authentication", min_width=18)
    table.add_column("Notes", min_width=24)

    for p in report.providers:
        cli_str = "[green]✓[/green]" if p.installed else "[red]✗[/red]"
        if p.installed:
            version_str = p.version if p.version else "[dim]unknown[/dim]"
        else:
            version_str = "[dim]—[/dim]"

        if not p.installed:
            auth_str = "[dim]—[/dim]"
        elif p.authentication_status == AuthenticationStatus.AUTHENTICATED:
            auth_str = "[green]Authenticated[/green]"
        elif p.authentication_status == AuthenticationStatus.NOT_AUTHENTICATED:
            auth_str = "[yellow]Not authenticated[/yellow]"
        elif p.authentication_status == AuthenticationStatus.NOT_PROBED:
            auth_str = "[dim]Not probed[/dim]"
        else:
            auth_str = "[dim]Unknown[/dim]"

        notes_str = ", ".join(p.diagnostics) if p.diagnostics else ""

        table.add_row(
            p.display_name,
            cli_str,
            version_str,
            auth_str,
            notes_str,
        )

    console.print("[bold]Providers[/bold]\n")
    console.print(table)

    installed_count = sum(1 for p in report.providers if p.installed)
    total_count = len(report.providers)
    noun = "provider" if installed_count == 1 else "providers"
    console.print(f"\n{installed_count} of {total_count} {noun} detected.\n")


@app.command(name="doctor")
def doctor_cmd(
    json_output: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Output diagnostics as clean, machine-readable JSON.",
        ),
    ] = False,
    provider: Annotated[
        list[str] | None,
        typer.Option(
            "--provider",
            "-p",
            help="Filter diagnostics to specific provider(s). Can be specified multiple times.",
        ),
    ] = None,
) -> None:
    """Inspect the local environment and detect installed AI coding agent CLIs."""
    service = DoctorService()

    provider_ids: list[ProviderId] | None = None
    if provider:
        try:
            provider_ids = [ProviderId(p) for p in provider]
        except ValueError as err:
            err_console.print(f"[red]Error:[/red] {err}")
            raise typer.Exit(code=2) from err

    try:
        report = service.run_diagnostics(provider_ids=provider_ids)
    except UnknownProviderError as err:
        err_console.print(f"[red]Error:[/red] {err}")
        raise typer.Exit(code=2) from err

    if json_output:
        sys.stdout.write(report.model_dump_json(indent=2) + "\n")
    else:
        _render_rich_doctor(report)


# --- Init Command ---


@app.command(name="init")
def init_cmd(
    path: Annotated[
        str | None,
        typer.Argument(
            help="Target repository directory to initialize. Defaults to current directory.",
        ),
    ] = None,
    name: Annotated[
        str | None,
        typer.Option(
            "--name",
            "-n",
            help="Custom project name. Defaults to directory name.",
        ),
    ] = None,
) -> None:
    """Initialize a project-local CortexShift workspace."""
    service = ProjectInitializationService()
    target_path = Path(path) if path else Path.cwd()

    try:
        result = service.initialize(target_path=target_path, name=name)
    except Exception as err:
        _handle_error(err)
        return

    if result.already_initialized:
        console.print(f"CortexShift is already initialized for {result.project.name}.")
    else:
        console.print("\n[bold]Initialized CortexShift[/bold]\n")
        table = Table.grid(padding=(0, 2))
        table.add_column(style="dim", min_width=10)
        table.add_column()
        table.add_row("Project", result.project.name)
        table.add_row("Path", result.project.repo_path)
        table.add_row("State", f"{STATE_DIR_NAME}/{DATABASE_FILE_NAME}")
        console.print(table)
        console.print()


# --- Status Command ---


def _render_rich_status(status: ProjectStatus) -> None:
    """Render ProjectStatus using Rich formatting."""
    console.print("\n[bold]CortexShift Status[/bold]\n")

    console.print("[bold]Project[/bold]")
    console.print(f"  {status.name}")
    console.print(f"  [dim]{status.project_id}[/dim]\n")

    console.print("[bold]State[/bold]")
    console.print(f"  {status.state_file}")
    console.print(f"  [dim]Schema v{status.schema_version}[/dim]\n")

    console.print("[bold]Active Task[/bold]")
    if status.active_task is None:
        console.print("  [dim]None[/dim]\n")
    else:
        task = status.active_task
        console.print(f"  [dim]{task.id}[/dim]")
        console.print(f"  {task.title}")
        console.print(f"  [cyan]{task.status.value}[/cyan]\n")

        console.print("[bold]Progress[/bold]")
        console.print(f"  Completed   {task.progress.completed}")
        console.print(f"  Remaining   {task.progress.remaining}")
        console.print(f"  Issues      {task.progress.issues}\n")


@app.command(name="status")
def status_cmd(
    json_output: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Output status as clean, machine-readable JSON.",
        ),
    ] = False,
) -> None:
    """Display project identity, state location, active task, and progress."""
    service = ProjectStatusService()

    try:
        status = service.get_status()
    except Exception as err:
        _handle_error(err)
        return

    if json_output:
        sys.stdout.write(status.model_dump_json(indent=2) + "\n")
    else:
        _render_rich_status(status)


# --- Task Commands ---


@task_app.command(name="start")
def task_start_cmd(
    title_arg: Annotated[
        str | None,
        typer.Argument(
            help="Title of the task (can also be passed via --title).",
        ),
    ] = None,
    title: Annotated[
        str | None,
        typer.Option(
            "--title",
            "-t",
            help="Title of the task.",
        ),
    ] = None,
    objective: Annotated[
        str | None,
        typer.Option(
            "--objective",
            "-o",
            help="High-level objective and requirements for the task.",
        ),
    ] = None,
    requirement: Annotated[
        list[str] | None,
        typer.Option(
            "--requirement",
            "-r",
            help="Requirement for the task. Can be specified multiple times.",
        ),
    ] = None,
    constraint: Annotated[
        list[str] | None,
        typer.Option(
            "--constraint",
            "-c",
            help="Constraint for the task. Can be specified multiple times.",
        ),
    ] = None,
    set_active: Annotated[
        bool,
        typer.Option(
            "--set-active/--no-set-active",
            help="Set the newly created task as active immediately.",
        ),
    ] = True,
) -> None:
    """Create a new task and optionally set it as active."""
    effective_title = title or title_arg
    if not effective_title:
        if sys.stdin.isatty():
            effective_title = typer.prompt("Task title")
        else:
            err_console.print(
                "[red]Error:[/red] Missing required option '--title' (or positional title)."
            )
            raise typer.Exit(code=2)

    if not objective:
        if sys.stdin.isatty():
            objective = typer.prompt("Task objective")
        else:
            err_console.print("[red]Error:[/red] Missing required option '--objective'.")
            raise typer.Exit(code=2)

    try:
        project, store = _get_project_and_store()
    except Exception as err:
        _handle_error(err)
        return

    try:
        with store:
            service = TaskService(store)
            task = service.start_task(
                project_id=project.id,
                title=effective_title,
                objective=objective,
                requirements=requirement,
                constraints=constraint,
                set_active=set_active,
            )
    except Exception as err:
        _handle_error(err)
        return

    active_tag = " [green](active)[/green]" if set_active else ""
    console.print(f"\n[bold]Started task[/bold] [cyan]{task.id}[/cyan]{active_tag}\n")
    table = Table.grid(padding=(0, 2))
    table.add_column(style="dim", min_width=14)
    table.add_column()
    table.add_row("Title", task.title)
    table.add_row("Status", task.status.value)
    table.add_row("Objective", task.objective)
    if task.requirements:
        table.add_row("Requirements", f"{len(task.requirements)} item(s)")
    if task.constraints:
        table.add_row("Constraints", f"{len(task.constraints)} item(s)")
    console.print(table)
    console.print()


@task_app.command(name="list")
def task_list_cmd(
    status: Annotated[
        str | None,
        typer.Option(
            "--status",
            "-s",
            help="Filter tasks by status (e.g. in_progress, completed).",
        ),
    ] = None,
    all_tasks: Annotated[
        bool,
        typer.Option(
            "--all",
            "-a",
            help="Include all tasks (default behavior).",
        ),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Output task list as clean, machine-readable JSON.",
        ),
    ] = False,
) -> None:
    """List all persisted tasks for the current project."""
    try:
        project, store = _get_project_and_store()
    except Exception as err:
        _handle_error(err)
        return

    try:
        with store:
            service = TaskService(store)
            tasks = service.list_tasks(project.id)
            active_id = store.get_active_task_id(project.id)
    except Exception as err:
        _handle_error(err)
        return

    if status and not all_tasks:
        norm_status = status.strip().lower()
        valid_statuses = {s.value for s in TaskStatus}
        if norm_status not in valid_statuses:
            allowed = ", ".join(sorted(valid_statuses))
            err_console.print(
                f"[red]Error:[/red] Invalid status '{status}'. Valid options: {allowed}."
            )
            raise typer.Exit(code=2)
        tasks = [t for t in tasks if t.status.value == norm_status]

    if json_output:
        tasks_data = [
            {
                **t.model_dump(mode="json"),
                "is_active": (t.id == active_id),
            }
            for t in tasks
        ]
        sys.stdout.write(json.dumps(tasks_data, indent=2) + "\n")
        return

    if not tasks:
        console.print("\nNo tasks found. Create one with `cortexshift task start`.\n")
        return

    table = Table(box=box.ROUNDED, show_header=True, header_style="bold")
    table.add_column("ID", min_width=16)
    table.add_column("Status", min_width=12)
    table.add_column("Active", justify="center", min_width=8)
    table.add_column("Title", min_width=24)

    for t in tasks:
        is_active = t.id == active_id
        active_str = "[green]yes[/green]" if is_active else "[dim]no[/dim]"
        table.add_row(
            t.id,
            t.status.value,
            active_str,
            t.title,
        )

    console.print()
    console.print(table)
    console.print()


def _render_rich_task_details(task: Task, is_active: bool) -> None:
    """Render comprehensive task details in Rich format."""
    console.print(f"\n[bold]Task Details[/bold] — [cyan]{task.id}[/cyan]\n")

    active_tag = " [green](active)[/green]" if is_active else ""
    table = Table.grid(padding=(0, 2))
    table.add_column(style="dim", min_width=16)
    table.add_column()
    table.add_row("Title", task.title)
    table.add_row("Status", f"{task.status.value}{active_tag}")
    table.add_row("Objective", task.objective)

    if task.current_work:
        table.add_row("Current Work", task.current_work)

    table.add_row("Created", task.created_at.isoformat())
    table.add_row("Updated", task.updated_at.isoformat())
    console.print(table)

    def _render_list_section(title: str, items: list[str]) -> None:
        console.print(f"\n[bold]{title}[/bold]")
        if not items:
            console.print("  [dim]—[/dim]")
        else:
            for item in items:
                console.print(f"  • {item}")

    _render_list_section("Requirements", task.requirements)
    _render_list_section("Constraints", task.constraints)
    _render_list_section("Completed Items", task.completed_items)
    _render_list_section("Remaining Items", task.remaining_items)
    _render_list_section("Known Issues", task.known_issues)
    console.print()


@task_app.command(name="show")
def task_show_cmd(
    task_id: Annotated[
        str | None,
        typer.Argument(
            help="Identifier of task to show. If omitted, shows the active task.",
        ),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Output task details as clean, machine-readable JSON.",
        ),
    ] = False,
) -> None:
    """Inspect full details of a task (or active task if none specified)."""
    try:
        project, store = _get_project_and_store()
    except Exception as err:
        _handle_error(err)
        return

    try:
        with store:
            service = TaskService(store)
            active_id = store.get_active_task_id(project.id)

            if task_id is None:
                if active_id is None:
                    raise NoActiveTaskError("No active task for this project.")
                target_id = active_id
            else:
                target_id = task_id

            task = service.get_task(target_id)
            if task.project_id != project.id:
                raise TaskNotFoundError(target_id)
    except Exception as err:
        _handle_error(err)
        return

    if json_output:
        task_data = {
            **task.model_dump(mode="json"),
            "is_active": (task.id == active_id),
        }
        sys.stdout.write(json.dumps(task_data, indent=2) + "\n")
    else:
        _render_rich_task_details(task, is_active=(task.id == active_id))


@task_app.command(name="activate")
def task_activate_cmd(
    task_id: Annotated[
        str,
        typer.Argument(
            help="Identifier of the task to make active.",
        ),
    ],
) -> None:
    """Set an existing task as the active task."""
    try:
        project, store = _get_project_and_store()
    except Exception as err:
        _handle_error(err)
        return

    try:
        with store:
            service = TaskService(store)
            task = service.activate_task(project.id, task_id)
    except Exception as err:
        _handle_error(err)
        return

    console.print(f"Activated task {task.id}")


@task_app.command(name="complete")
def task_complete_cmd(
    task_id: Annotated[
        str | None,
        typer.Argument(
            help="Identifier of the task to complete. Defaults to active task.",
        ),
    ] = None,
) -> None:
    """Mark the active task (or specified task) as completed."""
    try:
        project, store = _get_project_and_store()
    except Exception as err:
        _handle_error(err)
        return

    try:
        with store:
            service = TaskService(store)
            task = service.complete_task(project.id, task_id)
    except Exception as err:
        _handle_error(err)
        return

    console.print(f"Completed task {task.id}")


@task_app.command(name="update")
def task_update_cmd(
    task_id: Annotated[
        str | None,
        typer.Argument(
            help="Identifier of the task to update. Defaults to active task.",
        ),
    ] = None,
    current_work: Annotated[
        str | None,
        typer.Option(
            "--current-work",
            "--work",
            "-w",
            help="Description of currently active work in flight.",
        ),
    ] = None,
    clear_current_work: Annotated[
        bool,
        typer.Option(
            "--clear-current-work",
            help="Clear in-flight work description.",
        ),
    ] = False,
    add_completed: Annotated[
        list[str] | None,
        typer.Option(
            "--add-completed",
            help="Add item to completed list. Can be repeated.",
        ),
    ] = None,
    add_remaining: Annotated[
        list[str] | None,
        typer.Option(
            "--add-remaining",
            help="Add item to remaining list. Can be repeated.",
        ),
    ] = None,
    add_issue: Annotated[
        list[str] | None,
        typer.Option(
            "--add-issue",
            "--add-known-issue",
            help="Add item to known issues list. Can be repeated.",
        ),
    ] = None,
) -> None:
    """Update progress, current work, and issues on a task."""
    try:
        project, store = _get_project_and_store()
    except Exception as err:
        _handle_error(err)
        return

    try:
        with store:
            service = TaskService(store)
            task = service.update_task(
                project_id=project.id,
                task_id=task_id,
                current_work=current_work,
                clear_current_work=clear_current_work,
                add_completed=add_completed,
                add_remaining=add_remaining,
                add_issues=add_issue,
            )
    except Exception as err:
        _handle_error(err)
        return

    console.print(f"Updated task {task.id}")


# --- Repository CLI Commands ---


def _format_safe_path(path: str) -> str:
    """Safely escape paths for terminal display avoiding ANSI/control code injection."""
    sanitized = "".join(c if (c >= " " and c != "\x7f") else f"\\x{ord(c):02x}" for c in path)
    return escape(sanitized)


def _render_file_list(title: str, files: list[str], max_display: int = 20) -> None:
    """Render a bounded list of files in human-readable output."""
    if not files:
        return
    console.print(f"  [bold]{title}[/bold] ({len(files)}):")
    for f in files[:max_display]:
        console.print(f"    {_format_safe_path(f)}")
    if len(files) > max_display:
        console.print(f"    [dim]... and {len(files) - max_display} more[/dim]")


def _format_repo_path(path_str: str, project_root_str: str) -> str:
    """Display project and git paths compactly avoiding unnecessary absolute home paths."""
    try:
        p = Path(path_str).resolve()
        proj = Path(project_root_str).resolve()
        if p == proj:
            return "."
        if proj.is_relative_to(p):
            rel = ".."
            cur = proj.parent
            while cur != p and cur != cur.parent:
                rel += "/.."
                cur = cur.parent
            return rel
        return str(p)
    except Exception:
        return path_str


@repo_app.command("status")
def repo_status(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Output repository inspection in JSON format."),
    ] = False,
) -> None:
    """Inspect the live repository state and changed files."""
    try:
        service = RepositoryService()
        inspection = service.inspect_repository()
    except Exception as err:
        _handle_error(err)
        return

    if json_output:
        sys.stdout.write(inspection.model_dump_json(indent=2) + "\n")
        return

    if inspection.status == RepositoryInspectionStatus.GIT_NOT_INSTALLED:
        console.print("\n[bold]Repository[/bold]\n")
        console.print("[yellow]Git executable was not found in PATH.[/yellow]\n")
        return

    if inspection.status == RepositoryInspectionStatus.NOT_GIT_REPOSITORY:
        console.print("\n[bold]Repository[/bold]\n")
        console.print(
            "Git is available, but this CortexShift project is not inside a Git repository.\n"
        )
        return

    if inspection.status == RepositoryInspectionStatus.PROBE_ERROR:
        console.print("\n[bold]Repository[/bold]\n")
        console.print(
            f"[red]{inspection.diagnostic or 'Git repository inspection failed.'}[/red]\n"
        )
        return

    snapshot = inspection.snapshot
    if snapshot is None:
        console.print("\n[bold]Repository[/bold]\n")
        console.print("[yellow]No repository information available.[/yellow]\n")
        return

    console.print("\n[bold]Repository[/bold]\n")

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", justify="left")
    grid.add_column(style="default", justify="left")

    if snapshot.git_version:
        grid.add_row("Git", snapshot.git_version)

    git_root_display = _format_repo_path(snapshot.git_root, snapshot.project_root)
    grid.add_row("Root", git_root_display)
    grid.add_row("Branch", snapshot.branch if snapshot.branch else "[dim](none / detached)[/dim]")
    grid.add_row("HEAD", snapshot.head_sha[:8] if snapshot.head_sha else "[dim](unborn)[/dim]")
    state_display = "[yellow]Dirty[/yellow]" if snapshot.dirty else "[green]Clean[/green]"
    grid.add_row("State", state_display)
    console.print(grid)

    console.print("\n[bold]Changes[/bold]")
    ch_grid = Table.grid(padding=(0, 2))
    ch_grid.add_column(style="dim", justify="left")
    ch_grid.add_column(style="default", justify="left")
    ch_grid.add_row("  Staged", str(len(snapshot.staged_files)))
    ch_grid.add_row("  Modified", str(len(snapshot.modified_files)))
    ch_grid.add_row("  Untracked", str(len(snapshot.untracked_files)))
    ch_grid.add_row("  Conflicted", str(len(snapshot.conflicted_files)))
    console.print(ch_grid)

    if snapshot.working_tree_diff_summary:
        console.print(f"\n[bold]Working tree[/bold]\n  {snapshot.working_tree_diff_summary}")
    if snapshot.staged_diff_summary:
        console.print(f"\n[bold]Staged[/bold]\n  {snapshot.staged_diff_summary}")

    if snapshot.staged_files:
        console.print()
        _render_file_list("Staged", snapshot.staged_files)
    if snapshot.modified_files:
        console.print()
        _render_file_list("Modified", snapshot.modified_files)
    if snapshot.untracked_files:
        console.print()
        _render_file_list("Untracked", snapshot.untracked_files)
    if snapshot.conflicted_files:
        console.print()
        _render_file_list("Conflicted", snapshot.conflicted_files)

    console.print()


@repo_app.command("snapshot")
def repo_snapshot(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Output persisted snapshot in JSON format."),
    ] = False,
) -> None:
    """Capture and persist a point-in-time Git repository snapshot."""
    try:
        service = RepositoryService()
        snapshot = service.capture_snapshot()
    except Exception as err:
        _handle_error(err)
        return

    if json_output:
        sys.stdout.write(snapshot.model_dump_json(indent=2) + "\n")
        return

    console.print("\n[bold green]Repository snapshot captured[/bold green]\n")
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", justify="left")
    grid.add_column(style="default", justify="left")

    total_changed = (
        len(snapshot.staged_files)
        + len(snapshot.modified_files)
        + len(snapshot.untracked_files)
        + len(snapshot.conflicted_files)
    )

    grid.add_row("Snapshot", snapshot.id)
    grid.add_row("Branch", snapshot.branch if snapshot.branch else "[dim](none / detached)[/dim]")
    grid.add_row("HEAD", snapshot.head_sha[:8] if snapshot.head_sha else "[dim](unborn)[/dim]")
    grid.add_row("State", "[yellow]Dirty[/yellow]" if snapshot.dirty else "[green]Clean[/green]")
    grid.add_row("Changed", f"{total_changed} files" if total_changed > 0 else "[dim]Clean[/dim]")
    console.print(grid)
    console.print()


@repo_app.command("snapshots")
def repo_snapshots(
    limit: Annotated[
        int,
        typer.Option("--limit", "-n", help="Maximum number of snapshots to display."),
    ] = 10,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Output snapshot list in JSON format."),
    ] = False,
) -> None:
    """List historical repository snapshots."""
    try:
        service = RepositoryService()
        snapshots = service.list_snapshots(limit=limit)
    except Exception as err:
        _handle_error(err)
        return

    if json_output:
        sys.stdout.write(
            json.dumps([snap.model_dump(mode="json") for snap in snapshots], indent=2) + "\n"
        )
        return

    if not snapshots:
        console.print("\n[dim]No repository snapshots captured yet.[/dim]\n")
        return

    table = Table(
        box=box.ROUNDED,
        show_header=True,
        header_style="bold cyan",
        title="\nRepository Snapshots",
    )
    table.add_column("Snapshot ID", style="cyan")
    table.add_column("Captured (UTC)", style="white")
    table.add_column("Branch", style="white")
    table.add_column("HEAD", style="dim")
    table.add_column("Dirty", justify="center")

    for s in snapshots:
        head_disp = s.head_sha[:8] if s.head_sha else "-"
        branch_disp = s.branch if s.branch else "(detached)"
        dirty_disp = "[yellow]yes[/yellow]" if s.dirty else "[green]no[/green]"
        captured_str = s.captured_at.strftime("%Y-%m-%d %H:%M:%S")
        table.add_row(s.id, captured_str, branch_disp, head_disp, dirty_disp)

    console.print(table)
    console.print()


@repo_app.command("show")
def repo_show(
    snapshot_id: Annotated[
        str,
        typer.Argument(help="Identifier of the snapshot to inspect."),
    ],
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Output snapshot details in JSON format."),
    ] = False,
) -> None:
    """Display detailed information for a stored repository snapshot."""
    try:
        service = RepositoryService()
        snapshot = service.get_snapshot(snapshot_id)
        if snapshot is None:
            raise SnapshotNotFoundError(snapshot_id)
    except Exception as err:
        _handle_error(err)
        return

    if json_output:
        sys.stdout.write(snapshot.model_dump_json(indent=2) + "\n")
        return

    console.print(f"\n[bold]Repository Snapshot: {snapshot.id}[/bold]\n")
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", justify="left")
    grid.add_column(style="default", justify="left")

    grid.add_row("Captured (UTC)", snapshot.captured_at.strftime("%Y-%m-%d %H:%M:%S"))
    if snapshot.git_version:
        grid.add_row("Git Version", snapshot.git_version)
    grid.add_row("Git Root", snapshot.git_root)
    grid.add_row("Branch", snapshot.branch if snapshot.branch else "[dim](none / detached)[/dim]")
    grid.add_row("HEAD", snapshot.head_sha if snapshot.head_sha else "[dim](unborn)[/dim]")
    grid.add_row("State", "[yellow]Dirty[/yellow]" if snapshot.dirty else "[green]Clean[/green]")
    console.print(grid)

    console.print("\n[bold]Changes[/bold]")
    ch_grid = Table.grid(padding=(0, 2))
    ch_grid.add_column(style="dim", justify="left")
    ch_grid.add_column(style="default", justify="left")
    ch_grid.add_row("  Staged", str(len(snapshot.staged_files)))
    ch_grid.add_row("  Modified", str(len(snapshot.modified_files)))
    ch_grid.add_row("  Untracked", str(len(snapshot.untracked_files)))
    ch_grid.add_row("  Conflicted", str(len(snapshot.conflicted_files)))
    console.print(ch_grid)

    if snapshot.working_tree_diff_summary:
        console.print(f"\n[bold]Working tree[/bold]\n  {snapshot.working_tree_diff_summary}")
    if snapshot.staged_diff_summary:
        console.print(f"\n[bold]Staged[/bold]\n  {snapshot.staged_diff_summary}")

    if snapshot.staged_files:
        console.print()
        _render_file_list("Staged", snapshot.staged_files)
    if snapshot.modified_files:
        console.print()
        _render_file_list("Modified", snapshot.modified_files)
    if snapshot.untracked_files:
        console.print()
        _render_file_list("Untracked", snapshot.untracked_files)
    if snapshot.conflicted_files:
        console.print()
        _render_file_list("Conflicted", snapshot.conflicted_files)

    console.print()


# --- Provider Run Command ---


@app.command("run", no_args_is_help=False)
def run_command(
    provider: Annotated[
        str,
        typer.Argument(
            help="Canonical provider identifier to launch (claude, codex, antigravity).",
        ),
    ],
    prompt: Annotated[
        str | None,
        typer.Option(
            "--prompt",
            "-p",
            help="Optional initial prompt to pass to the provider.",
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Simulate launch and display specification without starting process.",
        ),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Output machine-readable JSON (only valid with --dry-run).",
        ),
    ] = False,
) -> None:
    """Launch an interactive native coding agent session on the active task."""
    if json_output and not dry_run:
        err_console.print("[red]Error:[/red] --json is only supported with --dry-run.")
        raise typer.Exit(code=1)

    run_service = RunService()

    if dry_run:
        try:
            result = run_service.dry_run(provider_name=provider, prompt=prompt)
        except Exception as err:
            _handle_error(err)
            return

        if json_output:
            sys.stdout.write(json.dumps(result.to_dict(), indent=2) + "\n")
            return

        console.print("\n[bold]CortexShift Provider Launch (Dry Run)[/bold]\n")
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="bold cyan", justify="left")
        grid.add_column(style="default", justify="left")
        grid.add_row("Provider", result.display_name)
        grid.add_row("Executable", result.executable)
        grid.add_row("Project", f"{result.project_name} ({result.project_id})")
        grid.add_row("Task", f"{result.task_title} ({result.task_id})")
        grid.add_row("Directory", str(result.cwd))
        grid.add_row("Mode", result.mode)
        grid.add_row("Prompt", "supplied" if result.prompt_supplied else "none")
        console.print(grid)
        console.print(f"\n[bold]Command:[/bold] {' '.join(result.argv)}\n")
        return

    def _on_launch(
        _spec: LaunchSpecification,
        session: Session,
        task: Task,
        _project: Project,
    ) -> None:

        console.print("\n[bold]CortexShift[/bold]")
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="bold cyan", justify="left")
        grid.add_column(style="default", justify="left")
        grid.add_row("Task", task.title)
        adapter = run_service._registry.get(provider)
        display_name = adapter.display_name if adapter else provider
        grid.add_row("Provider", display_name)
        grid.add_row("Session", session.id)
        console.print(grid)
        console.print("\n[dim]Launching native provider...[/dim]\n")

    try:
        session = run_service.run(
            provider_name=provider,
            prompt=prompt,
            on_launch=_on_launch,
        )
    except Exception as err:
        _handle_error(err)
        return

    if session.status == SessionStatus.COMPLETED:
        console.print("\nSession completed.")
    elif session.status == SessionStatus.INTERRUPTED:
        console.print("\nSession interrupted.")
    else:
        console.print("\nSession failed.")
        if session.exit_code:
            raise typer.Exit(code=session.exit_code)
        raise typer.Exit(code=1)


@app.command("resume")
def resume_command(
    provider: Annotated[str, typer.Argument(help="Provider to resume exactly.")],
    session_id: Annotated[
        str | None, typer.Option("--session", help="Source CortexShift Session ID.")
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Preview without side effects.")
    ] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Requires --dry-run.")] = False,
) -> None:
    """Resume a known native conversation on the active task, without a handoff."""
    if json_output and not dry_run:
        err_console.print("[red]Error:[/red] --json is only supported with --dry-run.")
        raise typer.Exit(code=1)
    try:
        result = ResumeService().resume(provider, session_id, dry_run=dry_run)
    except Exception as err:
        _handle_error(err)
        return
    if isinstance(result, ResumeDryRunResult):
        if json_output:
            sys.stdout.write(json.dumps(result.to_dict(), indent=2) + "\n")
        else:
            console.print("\n[bold]CortexShift Resume (Dry Run)[/bold]")
            grid = Table.grid(padding=(0, 2))
            for key, value in result.to_dict().items():
                grid.add_row(key, escape(str(value)))
            console.print(grid)
        return
    console.print(f"\nSession {result.id}: {result.status.value}.")
    if result.status == SessionStatus.FAILED:
        console.print(
            "Exact native resume failed. Provider-owned state may no longer exist. "
            "The historical ID is preserved; no fresh session was started."
        )
        raise typer.Exit(code=result.exit_code or 1)


def _native_resumable(session: Session) -> bool:
    adapter = ProviderRuntimeRegistry().get(str(session.provider_id))
    return is_native_resumable(session, native_capabilities(adapter))


# --- Session Commands ---


@session_app.command("list")
def session_list_command(
    limit: Annotated[
        int,
        typer.Option(
            "--limit",
            help="Maximum number of sessions to return.",
        ),
    ] = 20,
    json_output: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Output machine-readable JSON format.",
        ),
    ] = False,
) -> None:
    """List historical agent execution sessions."""
    service = SessionService()
    try:
        sessions = service.list_sessions(limit=limit)
    except Exception as err:
        _handle_error(err)
        return

    if json_output:
        data = [
            {**s.model_dump(mode="json"), "native_resumable": _native_resumable(s)}
            for s in sessions
        ]
        sys.stdout.write(json.dumps(data, indent=2) + "\n")
        return

    if not sessions:
        console.print("\nNo sessions found.\n")
        return

    table = Table(
        title="Agent Sessions",
        box=box.ROUNDED,
        header_style="bold cyan",
    )
    table.add_column("Session", style="bold", min_width=12)
    table.add_column("Provider")
    table.add_column("Status")
    table.add_column("Native Resume", max_width=6)
    table.add_column("Started (UTC)")
    table.add_column("Task")

    provider_names = {
        "claude": "Claude Code",
        "codex": "Codex",
        "antigravity": "Antigravity",
    }

    for s in sessions:
        p_name = provider_names.get(str(s.provider_id).lower(), str(s.provider_id))
        status_style = (
            "green"
            if s.status == SessionStatus.COMPLETED
            else ("yellow" if s.status == SessionStatus.RUNNING else "red")
        )
        table.add_row(
            s.id,
            p_name,
            f"[{status_style}]{s.status.value}[/{status_style}]",
            "yes" if _native_resumable(s) else "no",
            s.started_at.strftime("%Y-%m-%d %H:%M:%S"),
            s.task_id,
        )

    console.print()
    console.print(table)
    console.print()


@session_app.command("show")
def session_show_command(
    session_id: Annotated[
        str,
        typer.Argument(
            help="Identifier of the session to inspect.",
        ),
    ],
    json_output: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Output machine-readable JSON format.",
        ),
    ] = False,
) -> None:
    """Show details of a specific agent execution session."""
    service = SessionService()
    try:
        session = service.get_session(session_id)
    except Exception as err:
        _handle_error(err)
        return

    if json_output:
        sys.stdout.write(
            json.dumps(
                {**session.model_dump(mode="json"), "native_resumable": _native_resumable(session)},
                indent=2,
            )
            + "\n"
        )
        return

    provider_names = {
        "claude": "Claude Code",
        "codex": "Codex",
        "antigravity": "Antigravity",
    }
    p_name = provider_names.get(str(session.provider_id).lower(), str(session.provider_id))

    console.print(f"\n[bold]Session: {session.id}[/bold]\n")
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", justify="left")
    grid.add_column(style="default", justify="left")
    grid.add_row("Task ID", session.task_id)
    grid.add_row("Provider", p_name)
    grid.add_row("Status", session.status.value)
    grid.add_row("Native Session ID", escape(session.native_session_id or "—"))
    grid.add_row("Native resumable", "yes" if _native_resumable(session) else "no")
    grid.add_row("Resumed from CortexShift Session", session.resumed_from_session_id or "—")
    grid.add_row("Started (UTC)", session.started_at.strftime("%Y-%m-%d %H:%M:%S"))
    grid.add_row(
        "Ended (UTC)",
        session.ended_at.strftime("%Y-%m-%d %H:%M:%S") if session.ended_at else "—",
    )
    grid.add_row("Exit Reason", session.exit_reason.value if session.exit_reason else "—")
    grid.add_row("Exit Code", str(session.exit_code) if session.exit_code is not None else "—")
    console.print(grid)
    console.print()


# --- Handoff Commands ---

PROVIDER_DISPLAY_NAMES = {
    "claude": "Claude Code",
    "codex": "Codex",
    "antigravity": "Antigravity",
}


def _provider_label(provider_id: str) -> str:
    """Render a canonical provider ID as its human-readable display name."""
    return PROVIDER_DISPLAY_NAMES.get(provider_id.lower(), provider_id)


def _render_handoff_summary(handoff: HandoffRecord) -> None:
    """Render the canonical engineering context of a handoff for human inspection."""
    payload = handoff.payload

    console.print(f"\n[bold]Handoff: {handoff.id}[/bold]\n")
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", justify="left")
    grid.add_column(style="default", justify="left")
    grid.add_row("Protocol", f"CortexShift Handoff Protocol v{handoff.protocol_version}")
    grid.add_row("Status", handoff.status.value)
    grid.add_row("From", f"{_provider_label(str(handoff.source_provider_id))}")
    grid.add_row("Source Session", handoff.source_session_id)
    grid.add_row("To", f"{_provider_label(str(handoff.target_provider_id))}")
    grid.add_row("Target Session", handoff.target_session_id or "—")
    grid.add_row("Task", f"{escape(payload.task_title)} ({handoff.task_id})")
    grid.add_row("Git Snapshot", handoff.git_snapshot_id or "—")
    grid.add_row("Created (UTC)", handoff.created_at.strftime("%Y-%m-%d %H:%M:%S"))
    grid.add_row(
        "Delivered (UTC)",
        handoff.delivered_at.strftime("%Y-%m-%d %H:%M:%S") if handoff.delivered_at else "—",
    )
    grid.add_row("Failure", handoff.failure_code.value if handoff.failure_code else "—")
    console.print(grid)

    console.print(f"\n[bold]Original Objective[/bold]\n  {escape(payload.original_objective)}")
    _render_bullet_section("Requirements", payload.requirements)
    _render_bullet_section("Constraints", payload.constraints)
    _render_bullet_section("Completed (recorded, verify against repository)", payload.completed)
    console.print(
        f"\n[bold]Current Work[/bold]\n  "
        f"{escape(payload.current_work) if payload.current_work else '[dim](none)[/dim]'}"
    )
    _render_bullet_section("Remaining", payload.remaining)
    console.print(
        "\n[bold]Important Decisions[/bold]\n  "
        + (
            "\n  ".join(escape(d) for d in payload.important_decisions)
            if payload.decisions_known and payload.important_decisions
            else "[dim]No structured decisions are recorded in CortexShift state.[/dim]"
        )
    )
    _render_bullet_section("Known Issues", payload.known_issues)

    console.print("\n[bold]Files Touched[/bold]")
    if payload.files_touched:
        _render_file_list("Paths", payload.files_touched)
    else:
        console.print("  [dim](none observed)[/dim]")

    console.print(f"\n[bold]Test Status[/bold]\n  [dim]{escape(payload.test_status.summary)}[/dim]")

    git = payload.git_state
    console.print("\n[bold]Git State[/bold]")
    git_grid = Table.grid(padding=(0, 2))
    git_grid.add_column(style="dim", justify="left")
    git_grid.add_column(style="default", justify="left")
    git_grid.add_row("  Status", git.status.value)
    if git.available:
        git_grid.add_row("  Branch", git.branch or "(detached / unborn)")
        git_grid.add_row("  HEAD", git.head_sha[:12] if git.head_sha else "(unborn)")
        git_grid.add_row("  Working tree", "dirty" if git.dirty else "clean")
        git_grid.add_row(
            "  Changes",
            f"staged {git.staged_count}, modified {git.modified_count}, "
            f"untracked {git.untracked_count}, conflicted {git.conflicted_count}",
        )
    console.print(git_grid)
    console.print(f"  [dim]{escape(git.note)}[/dim]")

    if payload.operator_note:
        console.print(f"\n[bold]Operator Note[/bold]\n  {escape(payload.operator_note)}")

    console.print(
        f"\n[bold]Recommended Next Action[/bold]\n  {escape(payload.recommended_next_action)}\n"
    )


def _render_bullet_section(title: str, items: list[str], max_display: int = 30) -> None:
    """Render a bounded bulleted list section for human output."""
    console.print(f"\n[bold]{title}[/bold]")
    if not items:
        console.print("  [dim](none recorded)[/dim]")
        return
    for item in items[:max_display]:
        console.print(f"  - {escape(item)}")
    if len(items) > max_display:
        console.print(f"  [dim]... and {len(items) - max_display} more[/dim]")


@handoff_app.command("preview")
def handoff_preview_command(
    target: Annotated[
        str,
        typer.Argument(
            help="Canonical target provider identifier (claude, codex, antigravity).",
        ),
    ],
    from_session: Annotated[
        str | None,
        typer.Option(
            "--from-session",
            help="Explicit source session ID to hand off from (defaults to the latest).",
        ),
    ] = None,
    note: Annotated[
        str | None,
        typer.Option("--note", help="Optional operator note to include as advisory context."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Output machine-readable JSON format."),
    ] = False,
) -> None:
    """Preview the canonical handoff context a target provider would receive.

    Persists nothing, launches nothing, and consumes zero model quota.
    """
    try:
        result = SwitchService().preview(
            target_provider_name=target,
            from_session_id=from_session,
            note=note,
        )
    except Exception as err:
        _handle_error(err)
        return

    if json_output:
        sys.stdout.write(json.dumps(result.to_dict(), indent=2) + "\n")
        return

    console.print("\n[bold]CortexShift Handoff Preview[/bold]")
    console.print(
        "[dim]Preview only. Nothing was persisted and no provider was launched.\n"
        "The repository may change after this preview, so it can become stale.[/dim]\n"
    )
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", justify="left")
    grid.add_column(style="default", justify="left")
    grid.add_row("Protocol", f"CortexShift Handoff Protocol v{result.protocol_version}")
    grid.add_row("Target", result.target_provider_name)
    grid.add_row("Delivery", result.delivery_strategy)
    grid.add_row(
        "Bootstrap model turn",
        "yes" if result.bootstrap_model_turn_required else "no",
    )
    grid.add_row(
        "Context size",
        f"{result.context_characters} / {result.context_max_characters} characters"
        + (" (truncated)" if result.context_truncated else ""),
    )
    console.print(grid)

    console.print("\n[bold]Receiving Agent Context[/bold]\n")
    console.print(escape(result.rendered_context))
    console.print()


@handoff_app.command("list")
def handoff_list_command(
    limit: Annotated[
        int,
        typer.Option("--limit", "-n", help="Maximum number of handoffs to display."),
    ] = 20,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Output machine-readable JSON format."),
    ] = False,
) -> None:
    """List historical canonical handoffs for this project."""
    try:
        handoffs = HandoffService().list_handoffs(limit=limit)
    except Exception as err:
        _handle_error(err)
        return

    if json_output:
        data = [h.model_dump(mode="json") for h in handoffs]
        sys.stdout.write(json.dumps(data, indent=2) + "\n")
        return

    if not handoffs:
        console.print("\n[dim]No handoffs recorded yet.[/dim]\n")
        return

    table = Table(title="Handoffs", box=box.ROUNDED, header_style="bold cyan")
    table.add_column("Handoff", style="bold")
    table.add_column("From")
    table.add_column("To")
    table.add_column("Status")
    table.add_column("Created (UTC)")

    for h in handoffs:
        status_style = {
            HandoffStatus.DELIVERED: "green",
            HandoffStatus.PREPARED: "yellow",
            HandoffStatus.FAILED: "red",
        }.get(h.status, "white")
        table.add_row(
            h.id,
            _provider_label(str(h.source_provider_id)),
            _provider_label(str(h.target_provider_id)),
            f"[{status_style}]{h.status.value}[/{status_style}]",
            h.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        )

    console.print()
    console.print(table)
    console.print()


@handoff_app.command("show")
def handoff_show_command(
    handoff_id: Annotated[
        str,
        typer.Argument(help="Identifier of the handoff to inspect."),
    ],
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Output machine-readable JSON format."),
    ] = False,
) -> None:
    """Show the canonical engineering context of a stored handoff."""
    try:
        handoff = HandoffService().get_handoff(handoff_id)
    except Exception as err:
        _handle_error(err)
        return

    if json_output:
        sys.stdout.write(json.dumps(handoff.model_dump(mode="json"), indent=2) + "\n")
        return

    _render_handoff_summary(handoff)


# --- Switch Command ---


@app.command("switch")
def switch_command(
    provider: Annotated[
        str,
        typer.Argument(
            help="Canonical target provider identifier (claude, codex, antigravity).",
        ),
    ],
    new_session: Annotated[
        bool, typer.Option("--new-session", help="Force a fresh native conversation.")
    ] = False,
    resume_session: Annotated[
        str | None, typer.Option("--resume-session", help="Prior target CortexShift Session ID.")
    ] = None,
    from_session: Annotated[
        str | None,
        typer.Option(
            "--from-session",
            help="Explicit source session ID to hand off from (defaults to the latest).",
        ),
    ] = None,
    note: Annotated[
        str | None,
        typer.Option(
            "--note",
            help="Optional operator note added as advisory context to the handoff.",
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Describe the switch without persisting, launching, or using model quota.",
        ),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Output machine-readable JSON (only valid with --dry-run)."),
    ] = False,
) -> None:
    """Hand the active task to another coding agent and launch it with full context.

    CortexShift builds the handoff deterministically from durable local state, so the
    outgoing agent does not need to be available, running, or even installed.

    Claude receives a fresh context prompt. Codex and Antigravity ingest context in
    one read-only bootstrap model turn, then resume the same native conversation.
    Known target conversations are reused by default. --dry-run runs no model turn.
    """
    if json_output and not dry_run:
        err_console.print("[red]Error:[/red] --json is only supported with --dry-run.")
        raise typer.Exit(code=1)

    service = SwitchService()

    if dry_run:
        try:
            preview = service.dry_run(
                target_provider_name=provider,
                from_session_id=from_session,
                note=note,
                new_session=new_session,
                resume_session_id=resume_session,
            )
        except Exception as err:
            _handle_error(err)
            return

        if json_output:
            sys.stdout.write(json.dumps(preview.to_dict(), indent=2) + "\n")
            return

        console.print("\n[bold]CortexShift Switch (Dry Run)[/bold]\n")
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="bold cyan", justify="left")
        grid.add_column(style="default", justify="left")
        grid.add_row("Protocol", f"CortexShift Handoff Protocol v{preview.protocol_version}")
        grid.add_row("Project", f"{preview.project_name} ({preview.project_id})")
        grid.add_row("Task", f"{escape(preview.task_title)} ({preview.task_id})")
        grid.add_row(
            "From",
            f"{_provider_label(preview.source_provider_id)} "
            f"({preview.source_session_id}, {preview.source_session_status})",
        )
        grid.add_row("To", f"{preview.target_provider_name} ({preview.target_executable})")
        grid.add_row("Target native mode", preview.target_native_mode)
        grid.add_row("Prior target Session", preview.selected_prior_target_session_id or "—")
        grid.add_row("Native session known", "yes" if preview.native_session_known else "no")
        grid.add_row("Delivery", preview.delivery_strategy)
        grid.add_row(
            "Bootstrap model turn",
            "yes — one read-only planning turn would run"
            if preview.bootstrap_model_turn_required
            else "no",
        )
        grid.add_row(
            "Git",
            f"{preview.git_status}"
            + (
                f" · {preview.git_branch or '(detached)'}"
                f" · {'dirty' if preview.git_dirty else 'clean'}"
                if preview.git_status == RepositoryInspectionStatus.READY.value
                else ""
            ),
        )
        grid.add_row(
            "Context size",
            f"{preview.context_characters} / {preview.context_max_characters} characters",
        )
        grid.add_row("Truncated", "yes" if preview.context_truncated else "no")
        console.print(grid)
        console.print("\n[dim]Dry run: nothing was persisted and nothing was launched.[/dim]\n")
        return

    def _on_prepared(handoff: HandoffRecord, rendered: RenderedHandoffContext) -> None:
        console.print("\n[bold]CortexShift Handoff[/bold]")
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="bold cyan", justify="left")
        grid.add_column(style="default", justify="left")
        grid.add_row("Handoff", handoff.id)
        grid.add_row("Task", escape(handoff.payload.task_title))
        grid.add_row("From", _provider_label(str(handoff.source_provider_id)))
        grid.add_row("To", _provider_label(str(handoff.target_provider_id)))
        grid.add_row("Git Snapshot", handoff.git_snapshot_id or "— (Git state unavailable)")
        if rendered.truncated:
            omitted = sum(o.omitted_items for o in rendered.omissions)
            grid.add_row("Context", f"{rendered.character_count} chars ({omitted} items omitted)")
        else:
            grid.add_row("Context", f"{rendered.character_count} chars")
        console.print(grid)

        adapter = service._registry.get(provider)
        if adapter is not None and adapter.bootstrap_model_turn_required:
            console.print(
                "\n[dim]Delivering the handoff in a read-only bootstrap model turn...[/dim]"
            )

    def _on_launch(_spec: LaunchSpecification, session: Session) -> None:
        adapter = service._registry.get(provider)
        if adapter is not None and adapter.bootstrap_model_turn_required:
            console.print(
                "\nHandoff delivered in a read-only bootstrap turn.\n"
                "Opening the same conversation in the native TUI.\n"
                "Review the prepared continuation plan and continue from there."
            )
        console.print(f"\n[dim]Session {session.id} — launching native provider...[/dim]\n")

    try:
        result = service.switch(
            target_provider_name=provider,
            from_session_id=from_session,
            note=note,
            new_session=new_session,
            resume_session_id=resume_session,
            on_prepared=_on_prepared,
            on_launch=_on_launch,
        )
    except Exception as err:
        _handle_error(err)
        return

    session = result.target_session
    if session.status == SessionStatus.COMPLETED:
        console.print("\nSession completed.")
    elif session.status == SessionStatus.INTERRUPTED:
        console.print("\nSession interrupted.")
    else:
        console.print("\nSession failed.")
        console.print(f"[dim]Handoff {result.handoff.id} was delivered.[/dim]")
        if session.exit_code:
            raise typer.Exit(code=session.exit_code)
        raise typer.Exit(code=1)


# --- Checkpoint Commands ---


def _render_checkpoint_details(checkpoint: CheckpointRecord) -> None:
    p = checkpoint.payload
    task = p.task
    git = p.git_state

    console.print("\n[bold]CortexShift Checkpoint[/bold]")
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", justify="left")
    grid.add_column(style="default", justify="left")
    grid.add_row("ID", checkpoint.id)
    grid.add_row("Kind", checkpoint.kind.value)
    grid.add_row("Task", f"{escape(task.task_title)} ({task.task_id}, {task.task_status})")
    if checkpoint.session_id:
        grid.add_row("Session", checkpoint.session_id)
    if checkpoint.git_snapshot_id:
        grid.add_row("Git Snapshot", checkpoint.git_snapshot_id)
    grid.add_row("Created", checkpoint.created_at.strftime("%Y-%m-%d %H:%M:%S UTC"))
    console.print(grid)

    console.print("\n[bold]Progress[/bold]")
    pgrid = Table.grid(padding=(0, 2))
    pgrid.add_column(style="dim", justify="left")
    pgrid.add_column(style="default", justify="left")
    pgrid.add_row(
        "Completed",
        f"{len(task.completed)} items" if task.completed else "(none recorded)",
    )
    pgrid.add_row("Current", escape(task.current_work or "(no in-flight work recorded)"))
    pgrid.add_row(
        "Remaining",
        f"{len(task.remaining)} items" if task.remaining else "(none recorded)",
    )
    console.print(pgrid)

    console.print("\n[bold]Decisions[/bold]")
    if p.decisions:
        for d in p.decisions[:10]:
            console.print(f"  - {escape(d)}")
        if len(p.decisions) > 10:
            console.print(
                f"  [dim]... {len(p.decisions) - 10} more decisions omitted from summary[/dim]"
            )
    else:
        console.print("  [dim](none recorded)[/dim]")

    if task.known_issues:
        console.print("\n[bold]Issues[/bold]")
        for issue in task.known_issues[:10]:
            console.print(f"  - {escape(issue)}")
        if len(task.known_issues) > 10:
            console.print(f"  [dim]... {len(task.known_issues) - 10} more issues omitted[/dim]")

    console.print("\n[bold]Tests[/bold]")
    if p.test_status.known:
        console.print(f"  Status: {p.test_status.provenance.value}")
        console.print(f"  Summary: {escape(p.test_status.summary)}")
    else:
        console.print("  [dim]Unknown (not verified)[/dim]")

    if p.operator_note:
        console.print(f"\n[bold]Operator Note[/bold]\n  {escape(p.operator_note)}")

    console.print("\n[bold]Repository[/bold]")
    rgrid = Table.grid(padding=(0, 2))
    rgrid.add_column(style="dim", justify="left")
    rgrid.add_column(style="default", justify="left")
    rgrid.add_row("Status", git.status.value)
    if git.available:
        rgrid.add_row("Branch", git.branch or "(detached)")
        rgrid.add_row("HEAD", git.head_sha[:12] if git.head_sha else "(unborn)")
        rgrid.add_row("Working tree", "dirty" if git.dirty else "clean")
        rgrid.add_row("Files touched", f"{len(p.files_touched)} files")
    console.print(rgrid)

    if p.files_touched:
        console.print("\n[bold]Files Touched[/bold]")
        for f in p.files_touched[:15]:
            console.print(f"  {escape(f)}")
        if len(p.files_touched) > 15:
            console.print(
                f"  [dim]... {len(p.files_touched) - 15} more files omitted from summary[/dim]"
            )


@checkpoint_app.command(name="create")
def checkpoint_create_cmd(
    decision: Annotated[
        list[str] | None,
        typer.Option("--decision", "-d", help="Structured engineering decision (repeatable)."),
    ] = None,
    test_summary: Annotated[
        str | None,
        typer.Option("--test-summary", "-t", help="Reported test execution summary."),
    ] = None,
    note: Annotated[
        str | None,
        typer.Option("--note", "-n", help="Optional operator note."),
    ] = None,
    session: Annotated[
        str | None,
        typer.Option("--session", "-s", help="Explicit session ID to associate with."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Output machine-readable JSON."),
    ] = False,
) -> None:
    """Create an immutable checkpoint of current Task and repository state.

    Can be safely called while a provider session is actively executing;
    does not acquire the workspace lease.
    """
    service = CheckpointService()
    try:
        cp = service.create_checkpoint(
            kind=CheckpointKind.MANUAL,
            session_id=session,
            decisions=decision,
            test_summary=test_summary,
            note=note,
        )
    except Exception as err:
        _handle_error(err)
        return

    if json_output:
        sys.stdout.write(cp.model_dump_json(indent=2) + "\n")
        return

    console.print(f"\nCreated checkpoint [bold]{cp.id}[/bold] ({cp.kind.value})")
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", justify="left")
    grid.add_column(style="default", justify="left")
    grid.add_row("Task", escape(cp.payload.task.task_title))
    if cp.session_id:
        grid.add_row("Session", cp.session_id)
    if cp.git_snapshot_id:
        grid.add_row("Git Snapshot", cp.git_snapshot_id)
    grid.add_row("Files Touched", str(len(cp.payload.files_touched)))
    console.print(grid)


@checkpoint_app.command(name="list")
def checkpoint_list_cmd(
    limit: Annotated[
        int,
        typer.Option("--limit", "-l", help="Maximum number of checkpoints to display."),
    ] = 20,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Output machine-readable JSON."),
    ] = False,
) -> None:
    """List historical checkpoints, newest first."""
    service = CheckpointService()
    try:
        checkpoints = service.list_checkpoints(limit=limit)
    except Exception as err:
        _handle_error(err)
        return

    if json_output:
        sys.stdout.write(
            json.dumps([cp.model_dump(mode="json") for cp in checkpoints], indent=2) + "\n"
        )
        return

    if not checkpoints:
        console.print("\nNo checkpoints found.")
        return

    console.print(f"\n[bold]Checkpoints ({len(checkpoints)})[/bold]")
    table = Table(box=box.SIMPLE, show_header=True)
    table.add_column("Checkpoint", style="bold cyan", no_wrap=True)
    table.add_column("Kind", style="default")
    table.add_column("Session", style="dim")
    table.add_column("Created (UTC)", style="default")

    for cp in checkpoints:
        created_str = cp.created_at.strftime("%Y-%m-%d %H:%M:%S")
        sess_str = f"{cp.session_id[:10]}…" if cp.session_id else "-"
        table.add_row(
            cp.id,
            cp.kind.value,
            sess_str,
            created_str,
        )
    console.print(table)


@checkpoint_app.command(name="show")
def checkpoint_show_cmd(
    checkpoint_id: Annotated[str, typer.Argument(help="Checkpoint ID to inspect.")],
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Output machine-readable JSON."),
    ] = False,
) -> None:
    """Display detailed structured state for a specific checkpoint."""
    service = CheckpointService()
    try:
        cp = service.get_checkpoint(checkpoint_id)
        if cp is None:
            raise CheckpointNotFoundError(checkpoint_id)
    except Exception as err:
        _handle_error(err)
        return

    if json_output:
        sys.stdout.write(cp.model_dump_json(indent=2) + "\n")
        return

    _render_checkpoint_details(cp)


@checkpoint_app.command(name="latest")
def checkpoint_latest_cmd(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Output machine-readable JSON."),
    ] = False,
) -> None:
    """Display the newest checkpoint for the active task."""
    service = CheckpointService()
    try:
        cp = service.get_latest_checkpoint()
    except Exception as err:
        _handle_error(err)
        return

    if cp is None:
        if json_output:
            sys.stdout.write("{}\n")
        else:
            console.print("\nNo checkpoints found for the active task.")
        return

    if json_output:
        sys.stdout.write(cp.model_dump_json(indent=2) + "\n")
        return

    _render_checkpoint_details(cp)


# --- Recovery Command ---


def _render_recovery_report(report: RecoveryReport) -> None:
    console.print("\n[bold]CortexShift Recovery[/bold]\n")
    if report.dry_run:
        console.print(
            "[yellow bold]Dry Run Preview — no changes were made to state.[/yellow bold]\n"
        )

    console.print("[bold]Task[/bold]")
    console.print(f"  {escape(report.task_title)}\n")

    console.print("[bold]Recovered Sessions[/bold]")
    if report.reconciled_session_ids:
        for s_id in report.reconciled_session_ids:
            console.print(f"  {s_id}   unexpected termination")
    else:
        console.print("  [dim]No stale running or initializing sessions found.[/dim]")
    console.print()

    if report.checkpoint_id:
        console.print("[bold]Checkpoint[/bold]")
        console.print(f"  {report.checkpoint_id}\n")

    console.print("[bold]Repository[/bold]")
    console.print(f"  {'Dirty' if report.dirty else 'Clean'}")
    console.print(f"  {len(report.files_touched)} changed files\n")

    if not report.dry_run and report.reconciled_session_ids:
        console.print("[bold]Next[/bold]")
        if report.checkpoint_id:
            console.print(
                f"  Review checkpoint:\n    cortexshift checkpoint show {report.checkpoint_id}\n"
            )
        console.print(
            "  Then continue with:\n"
            "    cortexshift resume <provider>\n"
            "  or\n"
            "    cortexshift switch <provider>\n"
        )


@app.command(name="recover")
def recover_cmd(
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Preview recovery actions without modifying state."),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Output machine-readable JSON."),
    ] = False,
) -> None:
    """Reconcile crashed or interrupted sessions and capture recovery state."""
    service = RecoveryService()
    try:
        report = service.recover(dry_run=dry_run)
    except Exception as err:
        _handle_error(err)
        return

    if json_output:
        sys.stdout.write(report.model_dump_json(indent=2) + "\n")
        return

    _render_recovery_report(report)


if __name__ == "__main__":
    app()

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
from cortexshift.application.doctor import DoctorService, UnknownProviderError
from cortexshift.application.init_service import ProjectInitializationService
from cortexshift.application.locator import DATABASE_FILE_NAME, STATE_DIR_NAME, ProjectLocator
from cortexshift.application.repository_service import RepositoryService
from cortexshift.application.status_service import ProjectStatusService
from cortexshift.application.task_service import TaskService
from cortexshift.domain.doctor import AuthenticationStatus, DoctorReport
from cortexshift.domain.errors import (
    CortexShiftError,
    DatabaseStateError,
    NoActiveTaskError,
    ProjectConflictError,
    ProjectNotInitializedError,
    RepositoryInspectionError,
    SnapshotNotFoundError,
    StateCorruptionError,
    TaskAlreadyCompletedError,
    TaskNotActivatableError,
    TaskNotFoundError,
    UnsupportedSchemaVersionError,
)
from cortexshift.domain.git import (
    RepositoryInspectionStatus,
)
from cortexshift.domain.project import Project
from cortexshift.domain.provider import ProviderId
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


def print_version() -> None:
    """Print the version string."""
    console.print(f"CortexShift {__version__}")


def _handle_error(err: Exception) -> None:
    """Render friendly domain errors without Python tracebacks and exit."""
    if isinstance(err, ProjectNotInitializedError):
        err_console.print("\nCortexShift is not initialized here.\n\nRun:\n  cortexshift init\n")
        raise typer.Exit(code=1)
    if isinstance(
        err,
        (
            TaskNotFoundError,
            NoActiveTaskError,
            TaskNotActivatableError,
            TaskAlreadyCompletedError,
            ProjectConflictError,
            UnsupportedSchemaVersionError,
            StateCorruptionError,
            DatabaseStateError,
            RepositoryInspectionError,
            SnapshotNotFoundError,
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


if __name__ == "__main__":
    app()

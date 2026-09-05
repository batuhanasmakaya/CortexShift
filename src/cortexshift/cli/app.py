"""CortexShift Typer CLI application."""

import sys
from typing import Annotated

import typer
from rich import box
from rich.console import Console
from rich.table import Table

from cortexshift import __version__
from cortexshift.application.doctor import DoctorService, UnknownProviderError
from cortexshift.domain.doctor import AuthenticationStatus, DoctorReport
from cortexshift.domain.provider import ProviderId

console = Console()
err_console = Console(stderr=True)

app = typer.Typer(
    name="cortexshift",
    help="Switch agents. Keep the context. Provider-agnostic task handoff for coding agents.",
    add_completion=False,
)


def print_version() -> None:
    """Print the version string."""
    console.print(f"CortexShift {__version__}")


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


if __name__ == "__main__":
    app()

"""CortexShift Typer CLI application."""

import typer
from rich.console import Console

from cortexshift import __version__

console = Console()

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
    version_flag: bool = typer.Option(
        False,
        "--version",
        "-v",
        help="Show CortexShift version and exit.",
        is_eager=True,
    ),
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


if __name__ == "__main__":
    app()

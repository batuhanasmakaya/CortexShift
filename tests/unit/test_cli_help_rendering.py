"""Help assertions must hold whether or not the environment asks Rich for color.

Typer forces Rich into terminal mode when ``GITHUB_ACTIONS``, ``FORCE_COLOR``, or
``PY_COLORS`` is set. The styling it then emits splits option names across escape
sequences, so ``--json`` stops being a substring of the help text on CI while remaining
one on a developer's laptop. These tests pin that difference down: the CLI keeps its
colors, and the suite keeps reading the help as text.
"""

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from rich.console import Console
from typer import rich_utils
from typer.testing import CliRunner

from cortexshift.cli.app import app
from tests.cli_runner import (
    FIXED_TERMINAL_WIDTH,
    AnsiFreeCliRunner,
    path_without_providers,
    pin_console_width,
    run_cli,
    strip_ansi,
    unwrapped,
)

ROOT = Path(__file__).resolve().parents[2]

# Every help page a test inspects for option names, with the options it must document.
# Extending the CLI's help tests should extend this list too.
HELP_PAGES = [
    (["--help"], ["--version", "--help"]),
    (["doctor", "--help"], ["--json", "--provider"]),
    (["handoff", "preview", "--help"], ["--json"]),
    (["handoff", "list", "--help"], ["--json"]),
    (["handoff", "show", "--help"], ["--json"]),
    (["init", "--help"], ["--name"]),
    (["run", "--help"], ["--prompt", "--dry-run", "--json"]),
    (["session", "show", "--help"], ["--json"]),
    (["switch", "--help"], ["--from-session", "--note", "--dry-run", "--json"]),
]
HELP_PAGE_ARGS = [args for args, _ in HELP_PAGES]
HELP_PAGE_IDS = [" ".join(args) for args in HELP_PAGE_ARGS]

# The environment variables Typer reads to decide it is writing to a terminal. GitHub
# Actions sets the first one on every runner.
COLOR_FORCING_VARIABLES = ["GITHUB_ACTIONS", "FORCE_COLOR", "PY_COLORS"]


@pytest.fixture
def styled_help(monkeypatch: pytest.MonkeyPatch) -> None:
    """Render help the way a colored terminal -- and every CI runner -- receives it.

    Typer resolves the environment variables once at import time, so the constant is
    what a test has to move. Setting it directly also makes the fixture behave the same
    whether or not the suite itself is running on CI.
    """
    monkeypatch.setattr(rich_utils, "FORCE_TERMINAL", True)


def test_help_really_is_styled_when_the_terminal_is_forced(styled_help: None) -> None:
    """Guard the premise: without this, the tests below would prove nothing."""
    raw = CliRunner().invoke(app, ["session", "show", "--help"]).stdout
    assert "\x1b[" in raw, "expected Rich to style help output under a forced terminal"


@pytest.mark.parametrize(("args", "options"), HELP_PAGES, ids=HELP_PAGE_IDS)
def test_option_names_survive_styled_help(
    styled_help: None, args: list[str], options: list[str]
) -> None:
    """The shared runner recovers option names that styling splits apart."""
    result = AnsiFreeCliRunner().invoke(app, args)

    assert result.exit_code == 0
    assert "\x1b" not in result.stdout
    for option in options:
        assert option in result.stdout, f"{option} is missing from `{' '.join(args)}`"


@pytest.mark.parametrize("args", HELP_PAGE_ARGS, ids=HELP_PAGE_IDS)
def test_stripping_recovers_the_unstyled_help_exactly(
    monkeypatch: pytest.MonkeyPatch, args: list[str]
) -> None:
    """Styling is the *only* difference between local and CI help output.

    Normalizing it away therefore weakens no assertion: what is left is the same text a
    developer reads locally, character for character.
    """
    monkeypatch.setattr(rich_utils, "FORCE_TERMINAL", False)
    unstyled = CliRunner().invoke(app, args).stdout

    monkeypatch.setattr(rich_utils, "FORCE_TERMINAL", True)
    styled = CliRunner().invoke(app, args).stdout

    assert styled != unstyled, "the two renderings should differ before normalization"
    assert strip_ansi(styled) == unstyled


def test_styled_error_output_is_normalized_on_every_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`stderr` and the mixed `output` stream are normalized alongside `stdout`.

    The CLI's own console styles error text too -- its highlighter marks up the quoted
    provider name -- so the same hazard reaches assertions about failures.
    """
    # `cortexshift.cli` re-exports the Typer object as `app`, so the module that owns the
    # console has to be reached through the import system, not the package attribute.
    cli_module = importlib.import_module("cortexshift.cli.app")
    monkeypatch.setattr(
        cli_module, "err_console", Console(stderr=True, force_terminal=True, color_system="256")
    )

    raw = CliRunner().invoke(app, ["doctor", "--provider", "no_such_provider"])
    assert "\x1b[" in raw.stderr, "expected the CLI's error console to style its output"

    result = AnsiFreeCliRunner().invoke(app, ["doctor", "--provider", "no_such_provider"])
    assert result.exit_code == 2
    assert "\x1b" not in result.stderr
    assert "\x1b" not in result.output
    assert "Unknown provider 'no_such_provider'" in result.stderr
    assert "Unknown provider 'no_such_provider'" in result.output


@pytest.mark.parametrize("variable", COLOR_FORCING_VARIABLES)
def test_a_real_cli_process_is_readable_under_each_color_variable(variable: str) -> None:
    """End-to-end cover for the CI trigger itself.

    Typer reads these variables at import time, so only a fresh process exercises them.
    ``GITHUB_ACTIONS`` is the one that turned this suite red on the first public CI run.
    """
    result = run_cli(
        [sys.executable, "-m", "cortexshift", "switch", "--help"],
        env={**os.environ, variable: "1"},
    )

    assert result.returncode == 0
    assert "\x1b" not in result.stdout
    for option in ("--from-session", "--note", "--dry-run", "--json"):
        assert option in result.stdout


@pytest.mark.parametrize("variable", COLOR_FORCING_VARIABLES)
def test_machine_readable_output_is_never_styled_in_the_first_place(variable: str) -> None:
    """`--json` bypasses Rich, and must keep doing so.

    Normalizing captured output would otherwise hide a regression here: a consumer
    piping `--json` into a parser has no test harness to strip escapes for it. Read the
    raw process output rather than the normalized helper, precisely to catch that.
    """
    result = subprocess.run(
        [sys.executable, "-m", "cortexshift", "doctor", "--json"],
        capture_output=True,
        text=True,
        check=False,
        # `doctor` is the one command that executes provider CLIs, for version and auth
        # probes. Hiding them keeps this test from running whatever agent the developer
        # happens to have installed -- CI has none, and the suite must match.
        env={**os.environ, variable: "1", "PATH": path_without_providers()},
    )

    assert result.returncode == 0
    assert "\x1b" not in result.stdout
    assert json.loads(result.stdout)["cortexshift_version"]


@pytest.mark.parametrize(("columns", "lines"), [("60", "17"), ("200", "51")])
def test_importing_the_suite_leaves_the_callers_console_size_alone(
    columns: str, lines: str
) -> None:
    """Nothing in test start-up may rewrite the terminal size the caller asked for.

    `COLUMNS=60 pytest` has to actually run at 60 columns, or the width-hostile runs
    verify nothing. A subprocess is the only honest check: it imports the test package,
    its root conftest, and the CLI in that order, then reports both the environment and
    the width Rich actually resolved from it.
    """
    # `cortexshift.cli` re-exports the Typer object as `app`, so the module that owns the
    # console has to be reached through the import system, not the package attribute.
    probe = (
        "import os, importlib, tests, tests.conftest;"
        " cli = importlib.import_module('cortexshift.cli.app');"
        " print(os.environ['COLUMNS'], os.environ['LINES'], cli.console.width,"
        " cli.console.height)"
    )
    result = run_cli(
        [sys.executable, "-c", probe],
        cwd=ROOT,
        env={**os.environ, "COLUMNS": columns, "LINES": lines},
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == [columns, lines, columns, lines]


@pytest.mark.parametrize("columns", ["60", "100", "200"])
def test_help_option_names_are_readable_at_any_terminal_width(columns: str) -> None:
    """Help assertions hold at whatever width the caller's terminal reports.

    Typer lays help out to fit, so the option column moves; the shared runner's job is
    to keep the names findable regardless.
    """
    result = run_cli(
        [sys.executable, "-m", "cortexshift", "switch", "--help"],
        cwd=ROOT,
        env={**os.environ, "COLUMNS": columns, "FORCE_COLOR": "1"},
    )

    assert result.returncode == 0
    assert "\x1b" not in result.stdout
    for option in ("--from-session", "--note", "--dry-run", "--json"):
        assert option in result.stdout


def test_unwrapped_matches_a_sentence_across_a_wrap() -> None:
    """Wrapped prose still has to contain the words, in order."""
    wrapped = "The historical ID is preserved; no fresh session \nwas started.\n"
    assert "no fresh session was started" in unwrapped(wrapped)
    assert "no fresh session was finished" not in unwrapped(wrapped)


def test_fixed_console_width_overrides_a_hostile_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The opt-in pin wins over `COLUMNS`, which a bordered table needs to survive.

    Rich freezes `COLUMNS` into a console at construction and the CLI builds its
    consoles at import, so the pin replaces those consoles rather than setting an
    environment variable -- an override that would arrive too late to have any effect.
    """
    monkeypatch.setenv("COLUMNS", "40")
    assert pin_console_width(monkeypatch) == FIXED_TERMINAL_WIDTH

    cli_module = importlib.import_module("cortexshift.cli.app")
    assert cli_module.console.width == FIXED_TERMINAL_WIDTH
    assert cli_module.err_console.width == FIXED_TERMINAL_WIDTH
    assert cli_module.err_console.stderr is True


def test_fixed_console_width_is_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    """A test that does not ask for the pin renders at the caller's own width.

    This is what keeps `COLUMNS=60 pytest` meaningful: only the handful of tests that
    assert on laid-out tables opt out of the caller's terminal size.
    """
    cli_module = importlib.import_module("cortexshift.cli.app")
    real_console = cli_module.console

    pin_console_width(monkeypatch, width=123)
    assert cli_module.console.width == 123

    monkeypatch.undo()
    assert cli_module.console is real_console


def test_strip_ansi_removes_the_sequence_families_rich_emits() -> None:
    """Colors, cursor movement, and OSC-8 hyperlinks all reduce to their text."""
    assert strip_ansi("\x1b[1;36m-\x1b[0m\x1b[1;36m-json\x1b[0m") == "--json"
    assert strip_ansi("\x1b[2K\x1b[1Gprogress") == "progress"
    assert strip_ansi("\x1b]8;;https://example.invalid\x1b\\link\x1b]8;;\x1b\\") == "link"
    assert strip_ansi("\x1b]0;window title\x07done") == "done"
    assert strip_ansi("plain text -- no escapes") == "plain text -- no escapes"

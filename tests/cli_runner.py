"""Environment-independent CLI invocation for the test suite.

Typer renders ``--help`` through Rich, and Rich emits ANSI styling whenever it believes
it is writing to a terminal. Typer decides that for us: ``typer.rich_utils`` forces a
terminal when ``GITHUB_ACTIONS``, ``FORCE_COLOR``, or ``PY_COLORS`` is set. A developer
laptop has none of those, so help output arrives as plain text; GitHub Actions sets
``GITHUB_ACTIONS``, so the same output arrives wrapped in escape sequences.

The styling does not merely surround a value, it splits one. Typer highlights the
leading dash of an option separately from the rest, so ``--json`` reaches a test as::

    \\x1b[1;36m-\\x1b[0m\\x1b[1;36m-json\\x1b[0m

``"--json" in result.stdout`` is therefore true locally and false in CI, for a CLI that
works identically in both. The same applies to the CLI's own Rich console, whose
highlighter styles versions, quoted names, and paths in the middle of a sentence.

Stripping the escapes from captured output makes those assertions describe what the
command said rather than how the terminal painted it. The CLI itself is untouched: real
users still get colored help and colored output.

Console width is the other environment-dependent half of CLI output, and it is left to
the caller: `COLUMNS=60 pytest` really does run at 60 columns. Assertions cope with that
themselves -- `unwrapped` for text that merely re-wraps, and the `fixed_console_width`
fixture (see `tests/conftest.py`) for the few tests that assert on laid-out tables,
which a narrow terminal genuinely truncates.
"""

import importlib
import locale
import os
import re
import shutil
import subprocess
from collections.abc import Mapping
from typing import Any

import pytest
from rich.console import Console
from typer.testing import CliRunner, Result

# The width the CLI renders at when no terminal answers, which is what CI and any
# redirected stream get. Tests that assert on laid-out output ask for it explicitly.
FIXED_TERMINAL_WIDTH = 80

# CSI sequences (colors, cursor movement), OSC sequences (window titles, hyperlinks,
# terminated by BEL or ST), and the two-character escapes. One definition, used for both
# text and the bytes the runner captures.
_ANSI_ESCAPE_PATTERN = r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[@-Z\\-_])"
_ANSI_ESCAPE = re.compile(_ANSI_ESCAPE_PATTERN)
_ANSI_ESCAPE_BYTES = re.compile(_ANSI_ESCAPE_PATTERN.encode())


def strip_ansi(text: str) -> str:
    """Return ``text`` without ANSI escape sequences.

    For output captured outside :class:`AnsiFreeCliRunner` -- a subprocess, say.
    """
    return _ANSI_ESCAPE.sub("", text)


def pin_console_width(monkeypatch: pytest.MonkeyPatch, width: int = FIXED_TERMINAL_WIDTH) -> int:
    """Render the CLI's own output at ``width`` for the duration of one test.

    Rich resolves ``COLUMNS`` into a console the moment it is constructed, and the CLI
    constructs its consoles at import time -- so a per-invocation environment override
    cannot reach them, and a narrow terminal makes the CLI genuinely truncate table
    cells. A test that asserts on table or grid *content* therefore has to say what
    width it wants; there is nothing left to recover once characters are dropped.

    Prefer :func:`unwrapped` when the text merely wraps. Reach for this only when the
    assertion is about laid-out output. ``monkeypatch`` restores the real consoles
    afterwards, so nothing leaks into the next test.
    """
    cli = importlib.import_module("cortexshift.cli.app")
    for name in ("console", "err_console"):
        original: Console = getattr(cli, name)
        monkeypatch.setattr(cli, name, Console(stderr=original.stderr, width=width))
    return width


PROVIDER_EXECUTABLES = ("claude", "codex", "agy")


def path_without_providers() -> str:
    """A `PATH` with every directory holding a provider CLI removed.

    A GitHub runner has no coding agent installed, and that is deliberate: CortexShift is
    tested against fakes and must never invoke a real agent -- not even for the version
    and auth probes that provider discovery runs. Handing this to a subprocess makes
    discovery answer the same way on a laptop as it does on a runner.

    Whole directories are dropped rather than the executables inside them, so nothing on
    the machine is touched and Git, Python, and uv stay resolvable.
    """
    kept = [
        entry
        for entry in os.environ.get("PATH", "").split(os.pathsep)
        if entry and not any(shutil.which(name, path=entry) for name in PROVIDER_EXECUTABLES)
    ]
    return os.pathsep.join(kept)


def unwrapped(text: str) -> str:
    """Collapse Rich's line wrapping so a sentence can be matched as one string.

    Rich re-wraps prose to the console width, and that width comes from the environment
    -- ``COLUMNS``, or the terminal when there is one. A phrase that sits on one line at
    80 columns can break across two at 120, which is the wrapping counterpart of the
    styling problem this module exists for.

    Collapsing runs of whitespace keeps the words and their order significant and makes
    only the wrap points irrelevant. Use it for prose; leave table and panel output
    alone, since collapsing does not remove their borders.
    """
    return " ".join(text.split())


def child_stream_encoding(env: Mapping[str, str] | None = None) -> str:
    """The encoding a child process will write its output in.

    A Python child honours `PYTHONIOENCODING` when it is set and otherwise takes the
    platform default -- exactly what any other program reading that output would assume.
    """
    # An explicitly empty mapping means "this child inherits nothing", which is not the
    # same as passing no mapping at all -- `env or os.environ` would conflate the two.
    source = os.environ if env is None else env
    configured = source.get("PYTHONIOENCODING")
    if configured:
        return configured.split(":", 1)[0] or locale.getpreferredencoding(False)
    return locale.getpreferredencoding(False)


def run_cli(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    """Run the CLI as a real process and return its captured output as text.

    Both streams are always captured and always `str`; an empty string means the command
    wrote nothing, never that the harness lost it. That distinction is the point of doing
    the decoding here rather than through ``text=True``.

    ``text=True`` hands decoding to `subprocess`, which on Windows does it on a reader
    thread whose `UnicodeDecodeError` is swallowed: the thread dies, the stream comes
    back as `None`, and the first thing to touch it fails somewhere far away with
    "expected string or bytes-like object, got 'NoneType'". Reading bytes and decoding
    them here keeps a mis-encoded byte a visible detail of the output instead of a lost
    stream and a misleading traceback.

    The decode uses the encoding the child was told to write in, so mojibake still shows
    up as broken content and breaks the assertion that reads it. `errors="replace"` keeps
    a stray byte from hiding the rest of the output; a test that needs to prove the CLI
    emits *strictly* valid bytes should read them itself rather than through this helper.

    A failing command is reported through ``returncode`` rather than an exception, so its
    (normalized) output is available to the assertion that reads it.
    """
    if "stderr" in kwargs or "stdout" in kwargs or "capture_output" in kwargs:
        raise TypeError(
            "run_cli captures both streams itself; a caller that needs a different "
            "stream policy (merging stderr into stdout, say) should call subprocess "
            "directly rather than weaken this helper's contract."
        )

    result = subprocess.run(args, capture_output=True, check=False, **kwargs)
    encoding = child_stream_encoding(kwargs.get("env"))

    def decoded(stream: bytes) -> str:
        return strip_ansi(stream.decode(encoding, errors="replace"))

    # `capture_output=True` without `text` always yields bytes for both streams; assert
    # it rather than trusting it, so a future change cannot quietly reintroduce `None`.
    assert result.stdout is not None and result.stderr is not None, (
        f"subprocess did not capture both streams: {result!r}"
    )
    return subprocess.CompletedProcess(
        args=result.args,
        returncode=result.returncode,
        stdout=decoded(result.stdout),
        stderr=decoded(result.stderr),
    )


def run_cli_ok(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    """Run the CLI and require it to succeed, showing its own diagnostics if it does not.

    The `check=True` equivalent for this helper. `subprocess`'s version raises a
    `CalledProcessError` whose message says only that the exit code was non-zero, leaving
    the part that explains why -- the command's stderr -- to be dug out of the exception.
    Asserting here puts it straight in the failure.
    """
    result = run_cli(args, **kwargs)
    assert result.returncode == 0, (
        f"{' '.join(str(argument) for argument in args)} exited {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    return result


class AnsiFreeCliRunner(CliRunner):
    """A :class:`~typer.testing.CliRunner` whose captured output carries no ANSI escapes.

    Assertions against ``result.stdout``, ``result.stderr``, and ``result.output`` then
    hold whether or not the surrounding environment asked Rich for color.
    """

    def invoke(self, *args: Any, **kwargs: Any) -> Result:
        """Invoke the app, then normalize the captured streams.

        The streams are normalized at the byte level, before ``Result`` decodes them, so
        that every accessor stays consistent. Escape sequences are pure ASCII, so this
        cannot disturb the surrounding UTF-8.
        """
        result = super().invoke(*args, **kwargs)
        result.stdout_bytes = _ANSI_ESCAPE_BYTES.sub(b"", result.stdout_bytes)
        result.stderr_bytes = _ANSI_ESCAPE_BYTES.sub(b"", result.stderr_bytes)
        result.output_bytes = _ANSI_ESCAPE_BYTES.sub(b"", result.output_bytes)
        return result

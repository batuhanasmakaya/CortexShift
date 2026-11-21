"""Read explicit Task input and restore terminal access after a stdin objective."""

import stat
import sys
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import BinaryIO

from cortexshift.domain.errors import TaskStartupError, TerminalRequiredError


def read_objective(file: Path | None, from_stdin: bool, inline: str | None) -> str | None:
    if file is None and not from_stdin:
        return inline

    source = "the objective file" if file is not None else "standard input"
    try:
        if file is not None:
            if not stat.S_ISREG(file.stat().st_mode):
                raise TaskStartupError("The objective path must point to a regular file.")
            data = file.read_bytes()
        else:
            if sys.stdin.isatty():
                raise TaskStartupError("--stdin requires piped or redirected UTF-8 input.")
            data = sys.stdin.buffer.read()
        objective = data.decode("utf-8")
    except UnicodeDecodeError as err:
        raise TaskStartupError(f"Expected valid UTF-8 in {source}.") from err
    except FileNotFoundError as err:
        raise TaskStartupError(f"Could not read {source}. The path does not exist.") from err
    except (OSError, ValueError, AttributeError) as err:
        raise TaskStartupError(f"Could not read {source}.") from err
    if not objective or objective.isspace():
        raise TaskStartupError("The Task objective is empty.")
    return objective


@contextmanager
def interactive_stdin(*, piped: bool) -> Iterator[BinaryIO | None]:
    """Pass a real terminal directly to the child when stdin held the objective."""
    if not piped:
        yield None
        return

    required = "Piped startup requires an attached terminal for the native provider."
    if not sys.stdout.isatty():
        raise TerminalRequiredError(required)

    device = "CONIN$" if sys.platform == "win32" else "/dev/tty"
    with ExitStack() as stack:
        try:
            terminal = stack.enter_context(open(device, "rb", buffering=0))
        except OSError as err:
            raise TerminalRequiredError(required) from err
        if not terminal.isatty():
            raise TerminalRequiredError(required)
        yield terminal

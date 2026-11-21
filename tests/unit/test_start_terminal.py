"""Terminal behavior for redirected objectives without requiring a CI console."""

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from cortexshift.adapters.process_runner import SubprocessInteractiveProcessRunner
from cortexshift.cli import start_input
from cortexshift.domain.errors import TaskStartupError, TerminalRequiredError


class AttachedTerminal(BytesIO):
    def isatty(self) -> bool:
        return True


def test_stdin_mode_refuses_to_read_from_interactive_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    terminal = Mock(**{"isatty.return_value": True})
    monkeypatch.setattr(start_input, "sys", SimpleNamespace(stdin=terminal))
    with pytest.raises(TaskStartupError, match="piped or redirected"):
        start_input.read_objective(None, True, None)
    terminal.buffer.read.assert_not_called()


@pytest.mark.parametrize(("platform", "device"), [("win32", "CONIN$"), ("darwin", "/dev/tty")])
def test_piped_startup_passes_attached_terminal_to_the_native_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: str, device: str
) -> None:
    terminal = AttachedTerminal()
    open_terminal = Mock(return_value=terminal)
    monkeypatch.setattr(start_input, "open", open_terminal, raising=False)
    monkeypatch.setattr(
        start_input,
        "sys",
        SimpleNamespace(platform=platform, stdout=Mock(**{"isatty.return_value": True})),
    )
    popen = Mock(**{"return_value.wait.return_value": 0})
    monkeypatch.setattr("cortexshift.adapters.process_runner.subprocess.Popen", popen)

    with start_input.interactive_stdin(piped=True) as stream:
        process = SubprocessInteractiveProcessRunner(terminal_stdin=stream)
        assert process.run_interactive(["fake-provider"], tmp_path) == 0

    open_terminal.assert_called_once_with(device, "rb", buffering=0)
    assert popen.call_args.kwargs["stdin"] is stream is terminal
    assert popen.call_args.kwargs["stdout"] is None
    assert popen.call_args.kwargs["stderr"] is None
    assert popen.call_args.kwargs["shell"] is False
    assert terminal.closed


def test_piped_startup_without_attached_terminal_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        start_input, "sys", SimpleNamespace(stdout=Mock(**{"isatty.return_value": False}))
    )
    with (
        pytest.raises(TerminalRequiredError, match="attached terminal"),
        start_input.interactive_stdin(piped=True),
    ):
        pytest.fail("Startup entered without an attached terminal")

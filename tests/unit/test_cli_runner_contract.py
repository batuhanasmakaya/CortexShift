"""What `run_cli` promises about the streams it hands back.

The fourth public CI run failed on Windows with `TypeError: expected string or bytes-like
object, got 'NoneType'` in every `run_cli` test. `None` was `CompletedProcess.stdout`,
and it was `None` because `subprocess.run(text=True)` decodes on a reader thread on
Windows: the CLI wrote a byte the platform decoder could not read, the thread died with a
swallowed `UnicodeDecodeError`, and the stream came back missing rather than wrong.

So the contract is now explicit and enforced here: both streams are captured, both are
`str`, and `""` means the command wrote nothing -- never that the harness lost it.
"""

import locale
import os
import subprocess
import sys

import pytest

from tests.cli_runner import child_stream_encoding, run_cli, strip_ansi

# Emits styled text on stdout, styled text on stderr, and a chosen exit code, so one
# script can stand in for every stream shape a caller cares about.
_EMITTER = (
    "import sys;"
    " sys.stdout.write('\\x1b[1;36mOUT\\x1b[0m hello');"
    " sys.stderr.write('\\x1b[31mERR\\x1b[0m trouble');"
    " sys.exit(int(sys.argv[1]))"
)
_SILENT = "import sys; sys.exit(0)"


def _python(script: str, *script_args: str) -> list[str]:
    return [sys.executable, "-c", script, *script_args]


def test_both_streams_are_captured_and_normalized() -> None:
    """Stdout and stderr each come back as text, with styling removed from both."""
    result = run_cli(_python(_EMITTER, "0"))

    assert result.returncode == 0
    assert result.stdout == "OUT hello"
    assert result.stderr == "ERR trouble"


def test_ansi_on_stdout_is_stripped_without_touching_the_text() -> None:
    result = run_cli(_python(_EMITTER, "0"))

    assert "\x1b" not in result.stdout
    assert "OUT" in result.stdout


def test_ansi_on_stderr_is_stripped_without_touching_the_text() -> None:
    result = run_cli(_python(_EMITTER, "0"))

    assert "\x1b" not in result.stderr
    assert "ERR" in result.stderr


def test_a_silent_command_yields_empty_strings_not_none() -> None:
    """The distinction the Windows failure destroyed.

    `""` has to mean "the command wrote nothing". If a lost stream could also arrive as
    `""`, a test could pass while the CLI printed nothing at all.
    """
    result = run_cli(_python(_SILENT))

    assert result.stdout == ""
    assert result.stderr == ""
    assert result.stdout is not None and result.stderr is not None


def test_a_failing_command_still_returns_readable_diagnostics() -> None:
    """A non-zero exit is reported through `returncode`, with its output intact."""
    result = run_cli(_python(_EMITTER, "3"))

    assert result.returncode == 3
    assert "trouble" in result.stderr
    assert "hello" in result.stdout


def test_merging_the_streams_is_refused_rather_than_faked() -> None:
    """`stderr=STDOUT` genuinely leaves `stderr` as `None` in subprocess's own contract.

    Rather than fabricate an empty string for it, the helper says so: a caller who wants
    merged streams wants `subprocess`, not a helper that promises two of them.
    """
    with pytest.raises(TypeError, match="captures both streams itself"):
        run_cli(_python(_SILENT), stderr=subprocess.STDOUT)

    with pytest.raises(TypeError, match="captures both streams itself"):
        run_cli(_python(_SILENT), capture_output=False)


def test_subprocess_text_mode_is_what_used_to_lose_the_stream() -> None:
    """Pins the diagnosis, so nobody swaps the byte capture back for `text=True`.

    A child writing UTF-8 into a stream the parent decodes as cp1252 is exactly the
    Windows arrangement: `\\u2500` is three bytes, one of which cp1252 has no mapping
    for. Decoding it strictly is an error wherever it happens -- the Windows-specific
    part was only that `subprocess` hid the error and dropped the stream.
    """
    box_drawing = "\\u2500\\u2510"
    emit_utf8 = (
        f"import sys; sys.stdout.reconfigure(encoding='utf-8'); sys.stdout.write('{box_drawing}')"
    )
    raw = subprocess.run(_python(emit_utf8), capture_output=True, check=False).stdout

    with pytest.raises(UnicodeDecodeError):
        raw.decode("cp1252")

    # The helper reads bytes, so the same output arrives as text either way.
    assert run_cli(_python(emit_utf8)).stdout != ""


@pytest.mark.parametrize("encoding", ["cp1252", "utf-8"])
def test_output_is_decoded_with_the_encoding_the_child_was_told_to_use(encoding: str) -> None:
    """Mojibake stays visible: the helper does not guess a friendlier encoding.

    Decoding with the encoding the child actually wrote in is what a consuming program
    does, so a CLI that emitted the wrong encoding still breaks the assertion that reads
    it instead of being quietly repaired here.
    """
    env = {**os.environ, "PYTHONIOENCODING": encoding}
    assert child_stream_encoding(env) == encoding

    emit = "import sys; sys.stdout.write('caf\\u00e9 \\u2014 ok')"
    result = run_cli(_python(emit), env=env)

    # `é` and `—` both exist in cp1252 and in UTF-8, so the text survives either way.
    assert "café" in result.stdout
    assert "ok" in result.stdout


def test_child_stream_encoding_follows_pythonioencoding_then_the_platform() -> None:
    """The helper reads the encoding from the same place the child does."""
    assert child_stream_encoding({"PYTHONIOENCODING": "cp1252"}) == "cp1252"
    # PYTHONIOENCODING accepts an `encoding:errors` form; only the encoding is ours.
    assert child_stream_encoding({"PYTHONIOENCODING": "cp1252:replace"}) == "cp1252"
    assert child_stream_encoding({}) == locale.getpreferredencoding(False)


@pytest.mark.parametrize("encoding", ["cp1252", "ascii"])
def test_help_stays_strictly_decodable_in_the_encoding_it_declared(encoding: str) -> None:
    """The production invariant the Windows run actually broke.

    Read as raw bytes on purpose, not through `run_cli`: the helper decodes leniently so
    that a test can still read a damaged stream, which would hide exactly this. Whatever
    the CLI writes has to be valid in the encoding its stream declared, or the next
    program to read it -- `subprocess.run(text=True)`, a shell redirect, an editor --
    gets a decode error for output that was never meant for it.
    """
    raw = subprocess.run(
        _python("import runpy; runpy.run_module('cortexshift', run_name='__main__')", "--help"),
        capture_output=True,
        check=False,
        env={**os.environ, "PYTHONIOENCODING": encoding},
    )

    assert raw.returncode == 0, raw.stderr
    decoded = raw.stdout.decode(encoding)  # strict: a mis-encoded byte fails here
    assert "Switch agents. Keep the context." in strip_ansi(decoded)

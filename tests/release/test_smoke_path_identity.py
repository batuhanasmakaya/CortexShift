"""One directory, two spellings, and the smoke assertion that has to see through it.

The clean-room smoke driver launches the installed CortexShift in a temporary workspace
and asserts the provider is launched *in that directory*. It learned the workspace by the
spelling it was handed; CortexShift hands the provider the project root it resolved. On a
GitHub Windows runner those disagree -- `C:\\Users\\RUNNER~1\\...` is the 8.3 short form of
`C:\\Users\\runneradmin\\...` -- so comparing `Path` objects compared spelling and failed on
two names for one place.

The contract is location, so these test location. Symlinks stand in for the Windows
aliases that cannot be created here; they are the same question asked of the filesystem,
not a claim that Windows was validated.
"""

import importlib
import os
from pathlib import Path

import pytest

# The clean-room driver is a standalone script, copied on its own next to an installed
# wheel: it duck-types fake ports against whatever it finds there and is deliberately
# not a library. Importing it by name keeps it out of the type-checked import graph
# while still exercising the real helper rather than a copy of it.
installed_smoke = importlib.import_module("scripts.installed_smoke")
is_same_directory = installed_smoke.is_same_directory


def test_a_directory_is_the_same_as_itself(tmp_path: Path) -> None:
    assert is_same_directory(tmp_path, tmp_path)
    assert is_same_directory(str(tmp_path), tmp_path)


def test_two_different_directories_are_not_confused(tmp_path: Path) -> None:
    """The assertion still has to fail when the provider runs somewhere else.

    Without this, a helper that always said yes would look like a fix.
    """
    left = tmp_path / "wheel workspace"
    right = tmp_path / "sdist workspace"
    left.mkdir()
    right.mkdir()

    assert not is_same_directory(left, right)


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
def test_an_aliased_spelling_is_recognised_as_the_same_place(tmp_path: Path) -> None:
    """The property the Windows short path needs, exercised the way this OS can.

    `Path == Path` is spelling equality and says no; filesystem identity says yes.
    """
    real = tmp_path / "wheel workspace"
    real.mkdir()
    alias = tmp_path / "aliased workspace"
    alias.symlink_to(real, target_is_directory=True)

    assert Path(alias) != Path(real), "the alias is the same spelling, so this proves nothing"
    assert is_same_directory(alias, real)


def test_a_trailing_separator_or_dot_segment_is_the_same_place(tmp_path: Path) -> None:
    """Spellings that differ only in punctuation still name one directory."""
    workspace = tmp_path / "wheel workspace"
    workspace.mkdir()

    assert is_same_directory(f"{workspace}{os.sep}", workspace)
    assert is_same_directory(workspace / ".", workspace)


def test_a_path_with_spaces_is_handled_unchanged(tmp_path: Path) -> None:
    """Phase 10 deliberately uses temporary paths containing spaces; keep that working."""
    spaced = tmp_path / "cortexshift release abc" / "wheel workspace"
    spaced.mkdir(parents=True)

    assert " " in str(spaced)
    assert is_same_directory(spaced, spaced.resolve())


def test_a_missing_path_falls_back_instead_of_raising(tmp_path: Path) -> None:
    """`samefile` needs both paths to exist; a vanished one must not crash the check.

    The comparison then answers on normalized spelling, which is the most that can be
    known, and the caller's assertion still fails -- with its own message rather than an
    `OSError` from inside the helper.
    """
    missing = tmp_path / "never created"

    assert not is_same_directory(missing, tmp_path)
    assert is_same_directory(missing, missing)


def test_the_installed_smoke_driver_compares_locations_not_spellings() -> None:
    """Guards the driver itself against a regression to `Path(cwd) == ROOT`.

    The driver is copied alone into the clean room and cannot import test helpers, so its
    own source is what there is to check.
    """
    source = (Path(__file__).resolve().parents[2] / "scripts/installed_smoke.py").read_text(
        encoding="utf-8"
    )

    assert "Path(cwd) == ROOT" not in source
    assert source.count("is_same_directory(cwd, ROOT)") == 2, (
        "both the interactive and the headless runner must still assert the working directory"
    )


def test_the_driver_imports_nothing_from_the_checkout() -> None:
    """It runs beside an installed wheel, so it may only import CortexShift and stdlib."""
    driver = installed_smoke.__file__
    assert driver is not None
    imports = [
        line
        for line in Path(driver).read_text(encoding="utf-8").splitlines()
        if line.startswith(("import ", "from "))
    ]

    assert imports, "the driver has no imports, so this proves nothing"
    for line in imports:
        assert not line.startswith(
            ("from tests", "import tests", "from scripts", "import scripts")
        ), f"the clean-room driver may not import from the checkout: {line}"

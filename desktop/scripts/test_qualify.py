"""Regression fixtures for Windows installer qualification."""

import sys
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest
import qualify
from PyInstaller.archive.writers import CArchiveWriter


def pe(machine: int = 0x8664) -> bytes:
    return (
        b"MZ" + bytes(58) + (64).to_bytes(4, "little") + b"PE\0\0" + machine.to_bytes(2, "little")
    )


def frozen_archive(tmp_path: Path, extra: dict[str, bytes]) -> Path:
    entries = {
        "base_library.zip": b"PK\x03\x04",
        "python312.dll": pe(),
        "pydantic_core/_pydantic_core.cp312-win_amd64.pyd": pe(),
        "_sqlite3.pyd": pe(),
    } | extra
    entry = tmp_path / "bridge_entry.py"
    entry.write_text("pass\n", encoding="utf-8")
    toc = [("bridge_entry", str(entry), True, "s")]
    for index, (name, data) in enumerate(entries.items()):
        member = tmp_path / f"member-{index}"
        member.write_bytes(data)
        # PyInstaller 6.17 labels ordinary Windows data and native files alike as 'b'.
        toc.append((name, str(member), True, "b"))
    bridge = tmp_path / "bridge.pkg"
    CArchiveWriter(str(bridge), toc, "python312.dll")
    return bridge


@pytest.fixture(autouse=True)
def windows_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(qualify, "MAC", False)


def test_accepts_data_marked_as_binary(tmp_path: Path) -> None:
    bridge = frozen_archive(tmp_path, {r"package\schema.json": b"{}", "license.txt": b"MIT"})
    qualify.inspect_bridge(bridge, scratch=tmp_path, team=None)


@pytest.mark.parametrize(
    ("name", "content", "message"),
    [
        ("runtime.dll", b"invalid", r"Missing Windows executable header: runtime\.dll"),
        ("python312.dll", b"invalid", r"Missing Windows executable header: python312\.dll"),
        ("plugin.pyd", b"MZ", r"Invalid PE header: plugin\.pyd"),
        ("extension", pe(0xAA64), "Expected x64 binary: extension"),
        ("plugin.dll", pe(0x14C), r"Expected x64 binary: plugin\.dll"),
        (r"package\.env", b"fixture", r"Forbidden archive member: package\\\.env"),
    ],
)
def test_retains_format_architecture_and_archive_checks(
    tmp_path: Path, name: str, content: bytes, message: str
) -> None:
    bridge = frozen_archive(tmp_path, {name: content})
    with pytest.raises(AssertionError, match=message):
        qualify.inspect_bridge(bridge, scratch=tmp_path, team=None)


@pytest.mark.parametrize("leave_bridge_locked", [False, True])
def test_uninstall_waits_in_place_and_checks_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, leave_bridge_locked: bool
) -> None:
    target = tmp_path / "install directory"
    target.mkdir()
    executable = target / "cortexshift-desktop.exe"
    bridge = target / "cortexshift-bridge.exe"
    uninstaller = target / "uninstall.exe"
    for path in (executable, bridge, uninstaller):
        path.touch()

    def run(command: str, *, check: bool, timeout: int) -> None:
        assert command == f'"{uninstaller}" /S _?={target}'
        assert check and timeout == 120
        executable.unlink()
        if not leave_bridge_locked:
            bridge.unlink()

    monkeypatch.setattr(qualify.subprocess, "run", run)
    if leave_bridge_locked:
        with pytest.raises(AssertionError, match="Uninstaller left cortexshift-bridge.exe behind"):
            qualify.uninstall_windows(target)
        assert uninstaller.is_file()
    else:
        qualify.uninstall_windows(target)
        assert not uninstaller.exists()


@pytest.mark.parametrize("fail_inspection", [False, True])
def test_install_qualification_and_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail_inspection: bool
) -> None:
    installer = tmp_path / "CortexShift-setup.exe"
    installer.touch()
    calls: list[str] = []

    def install(_installer: Path, target: Path, *, team: str | None) -> None:
        assert team is None
        calls.append("install")
        target.mkdir(exist_ok=True)
        for filename in ("cortexshift-desktop.exe", "cortexshift-bridge.exe", "uninstall.exe"):
            (target / filename).write_bytes(pe())

    def inspect(*_args: object, **_kwargs: object) -> None:
        calls.append("inspect")
        if fail_inspection:
            raise AssertionError("fixture archive rejected")

    def roundtrip(bridge: Path, workspace: Path, project_id: str, env: dict[str, str]) -> None:
        assert bridge.is_file() and (workspace / ".cortexshift").is_dir() and project_id
        assert Path(env["WEBVIEW2_USER_DATA_FOLDER"]).is_relative_to(workspace.parent)
        calls.append("bridge")

    def uninstall(target: Path) -> None:
        assert (target / "uninstall.exe").is_file()
        calls.append("uninstall")

    monkeypatch.setattr(sys, "argv", ["qualify.py", str(installer)])
    monkeypatch.setenv("SYSTEMROOT", str(tmp_path))
    monkeypatch.setattr(qualify, "installation", install)
    monkeypatch.setattr(qualify, "inspect_bridge", inspect)
    monkeypatch.setattr(qualify, "uninstall_windows", uninstall)
    monkeypatch.setattr(qualify, "frozen_roundtrip", roundtrip)
    monkeypatch.setattr(qualify, "launch", lambda *_: calls.append("launch"))
    if fail_inspection:
        with pytest.raises(AssertionError, match="fixture archive rejected"):
            qualify.main()
        assert calls == ["install", "inspect", "uninstall"]
    else:
        qualify.main()
        assert calls == ["install", "inspect", "bridge", "launch", "install", "bridge", "uninstall"]


@pytest.mark.parametrize("show_window", [True, False])
def test_windows_launch_requires_owned_visible_window(
    monkeypatch: pytest.MonkeyPatch, show_window: bool
) -> None:
    posted: list[tuple[int, ...]] = []

    def enumerate_windows(record: Callable[[int, list[int]], bool], windows: list[int]) -> None:
        for hwnd in (1, 2, 3, 4):
            assert record(hwnd, windows) is True

    def wait(*, timeout: int) -> int:
        assert timeout == 10
        return 0

    monkeypatch.setitem(
        sys.modules,
        "win32gui",
        SimpleNamespace(
            EnumWindows=enumerate_windows,
            IsWindowVisible=lambda hwnd: hwnd != 2,
            GetWindowText=lambda hwnd: "CortexShift" if hwnd != 3 and show_window else "Other",
            PostMessage=lambda *args: posted.append(args),
        ),
    )
    monkeypatch.setitem(sys.modules, "win32con", SimpleNamespace(WM_CLOSE=16))
    monkeypatch.setitem(
        sys.modules,
        "win32process",
        SimpleNamespace(GetWindowThreadProcessId=lambda hwnd: (100, 200 if hwnd != 1 else 300)),
    )
    times = iter((0, 0, 21))
    monkeypatch.setattr(
        qualify, "time", SimpleNamespace(monotonic=lambda: next(times), sleep=lambda _: None)
    )
    process = SimpleNamespace(pid=200, poll=lambda: None, wait=wait)
    if show_window:
        qualify.exercise_windows_window(process)
        assert posted == [(4, 16, 0, 0)]
    else:
        with pytest.raises(AssertionError, match="did not create a visible CortexShift window"):
            qualify.exercise_windows_window(process)
        assert posted == []

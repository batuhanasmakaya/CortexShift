"""Exercise native installer and frozen bridge against an isolated workspace."""

import argparse
import json
import os
import plistlib
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

from PyInstaller.archive.readers import CArchiveReader

from cortexshift import __version__
from cortexshift.application.init_service import ProjectInitializationService
from cortexshift.application.task_service import TaskService
from cortexshift.application.workspace import open_workspace
from cortexshift.domain.session import Session, SessionStatus
from release import preflight

MAC = sys.platform == "darwin"
VERSION = "14.0"


def checked(*args: str) -> str:
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT, timeout=120).strip()


def native_binary(path: Path, *, team: str | None) -> None:
    if MAC:
        assert path.stat().st_mode & 0o111, f"Missing executable permissions: {path.name}"
        assert checked("lipo", "-archs", str(path)) == "arm64", (
            f"Unexpected architecture: {path.name}"
        )
        version = re.search(r"\bminos\s+(\d+\.\d+)", checked("otool", "-l", str(path)))
        if version:
            assert tuple(map(int, version[1].split("."))) <= tuple(map(int, VERSION.split("."))), (
                f"{path.name} requires macOS {version[1]}"
            )
        if team:
            checked("codesign", "--verify", "--strict", str(path))
            signed = checked("codesign", "-dv", "--verbose=4", str(path))
            assert f"TeamIdentifier={team}" in signed.splitlines(), (
                f"Wrong signing team: {path.name}"
            )
            assert "(runtime)" in signed, f"Hardened runtime disabled: {path.name}"
    else:
        with path.open("rb") as exe:
            assert exe.read(2) == b"MZ", f"Missing Windows executable header: {path.name}"
            exe.seek(60)
            offset = struct.unpack("<I", exe.read(4))[0]
            exe.seek(offset)
            assert exe.read(6) == b"PE\0\0\x64\x86", f"Expected x64 binary: {path.name}"


def inspect_bridge(path: Path, *, scratch: Path, team: str | None) -> None:
    archive = CArchiveReader(str(path))
    names = list(archive.toc)
    assert "bridge_entry" in names and "base_library.zip" in names
    natives = [name for name in names if archive.toc[name][-1] == "b"]
    assert any(re.search(r"(?:libpython3\.12\.dylib|python312\.dll)$", name) for name in natives)
    assert any(Path(name).name.startswith("_pydantic_core.") for name in natives)
    if not MAC:
        assert any(Path(name).name == "_sqlite3.pyd" for name in natives)
    assert not any(
        re.search(
            r"(^|[/\\])(?:\.env|\.cortexshift|\.git|node_modules|__tests__)([/\\]|$)|\.(p8|p12|pfx|key)$",
            name,
            re.I,
        )
        for name in names
    )
    for index, name in enumerate(natives):
        extracted = scratch / f"binary-{index}"
        extracted.write_bytes(archive.extract(name))
        extracted.chmod(0o700)
        native_binary(extracted, team=team)
        if MAC and not team:
            checked("codesign", "--verify", "--strict", str(extracted))
        extracted.unlink()
    print(f"Validated {len(natives)} embedded native runtime components", flush=True)


def verify_application(app: Path, version: str, *, team: str | None) -> Path:
    if MAC:
        with (app / "Contents/Info.plist").open("rb") as plist:
            info = plistlib.load(plist)
        assert info["CFBundleIdentifier"] == "com.cortexshift.desktop"
        assert info["CFBundleShortVersionString"] == version
        assert info["LSMinimumSystemVersion"] == VERSION
        assert (app / "Contents/Resources/icon.icns").is_file()
        if team:
            checked("codesign", "--verify", "--deep", "--strict", str(app))
            checked("spctl", "--assess", "--type", "execute", str(app))
            checked("xcrun", "stapler", "validate", str(app))
        return app / "Contents/MacOS/cortexshift-desktop"
    return app / "cortexshift-desktop.exe"


def installation(installer: Path, target: Path, *, team: str | None) -> None:
    if MAC:
        if team:
            checked("codesign", "--verify", "--strict", str(installer))
            checked("xcrun", "stapler", "validate", str(installer))
        mount = target.parent / "mounted"
        checked(
            "hdiutil", "attach", "-readonly", "-nobrowse", "-mountpoint", str(mount), str(installer)
        )
        try:
            # A fresh temporary destination models Finder's Replace semantics.
            if target.exists():
                shutil.rmtree(target)
            checked("ditto", str(mount / "CortexShift.app"), str(target))
        finally:
            checked("hdiutil", "detach", str(mount))
    else:
        # NSIS requires /D to be last and unquoted, including when the path contains spaces.
        subprocess.run(f'"{installer}" /S /D={target}', check=True, timeout=180)


def frozen_roundtrip(bridge: Path, root: Path, project_id: str, env: dict[str, str]) -> None:
    binding = {"root": str(root), "project_id": project_id}
    requests = [
        ("hello", {}),
        ("verify", {"root": str(root)}),
        ("overview", binding),
        ("tasks", binding),
        ("sessions", binding),
        ("verify", {"root": str(root / "missing")}),
    ]
    data = b"".join(
        json.dumps({"version": 1, "id": i, "op": op, **fields}).encode() + b"\n"
        for i, (op, fields) in enumerate(requests, 1)
    )
    process = subprocess.run(
        [str(bridge)],
        cwd=root.parent,
        env=env,
        input=data,
        capture_output=True,
        check=True,
        timeout=45,
    )
    hello, verified, overview, tasks, sessions, missing = map(
        json.loads, process.stdout.splitlines()
    )
    assert hello["result"] == {"protocol": 1, "core_version": __version__}
    assert verified["result"]["project_id"] == project_id
    assert overview["result"]["schema_version"] == 6
    assert tasks["result"]["items"][0]["title"] == "Distribution qualification"
    assert sessions["result"]["items"][0]["provider_id"] == "codex"
    assert "error" in missing


def launch(executable: Path, base: Path, env: dict[str, str]) -> None:
    with (
        tempfile.TemporaryFile() as log,
        subprocess.Popen(
            [str(executable)], cwd=base, env=env, stdout=subprocess.DEVNULL, stderr=log
        ) as process,
    ):
        try:
            exit_code = process.wait(timeout=6)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        else:
            log.seek(0)
            details = log.read(4096).decode(errors="replace")
            raise AssertionError(
                f"Installed app exited during startup with status {exit_code}\n{details}"
            )


def verify_windows(path: Path, subject: str) -> None:
    subprocess.run(
        [
            "pwsh",
            "-NoProfile",
            "-File",
            str(Path(__file__).with_name("verify_windows.ps1")),
            str(path),
            subject,
        ],
        check=True,
        timeout=60,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("installer", type=Path)
    parser.add_argument("--team")
    parser.add_argument("--windows-signer-subject")
    previous = parser.add_mutually_exclusive_group()
    previous.add_argument(
        "--previous-app", type=Path, help="macOS prior application for replacement test"
    )
    previous.add_argument(
        "--previous-installer", type=Path, help="Windows prior installer for upgrade test"
    )
    parser.add_argument(
        "--skip-gui",
        action="store_true",
        help="Run artifact and bridge checks without window launch",
    )
    args = parser.parse_args()
    installer = args.installer.resolve(strict=True)
    version = preflight()
    if args.windows_signer_subject:
        verify_windows(installer, args.windows_signer_subject)
    with tempfile.TemporaryDirectory(prefix="cortexshift qualification ") as temporary:
        base = Path(temporary).resolve()
        target = base / ("CortexShift.app" if MAC else "CortexShift")
        root = base / "fixture workspace"
        root.mkdir()
        project = ProjectInitializationService().initialize(root).project
        _, store = open_workspace(root)
        with store:
            task = TaskService(store).start_task(
                project.id, "Distribution qualification", "Read fixture."
            )
            store.save_session(
                Session(task_id=task.id, provider_id="codex", status=SessionStatus.COMPLETED)
            )
        home = base / "test profile"
        home.mkdir()
        local, roaming = home / "Local", home / "Roaming"
        local.mkdir()
        roaming.mkdir()
        env = {
            key: value
            for key, value in os.environ.items()
            if key
            in {
                "SYSTEMROOT",
                "WINDIR",
                "TMP",
                "TEMP",
                "TMPDIR",
                "COMSPEC",
                "SYSTEMDRIVE",
                "PATHEXT",
            }
        }
        env |= {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "LOCALAPPDATA": str(local),
            "APPDATA": str(roaming),
            "PATH": "/usr/bin:/bin" if MAC else str(Path(os.environ["SYSTEMROOT"]) / "System32"),
        }
        if args.previous_app:
            assert MAC
            checked("ditto", str(args.previous_app.resolve(strict=True)), str(target))
            frozen_roundtrip(target / "Contents/MacOS/cortexshift-bridge", root, project.id, env)
            print("Previous macOS bundle read the upgrade fixture", flush=True)
        if args.previous_installer:
            assert not MAC
            installation(args.previous_installer.resolve(strict=True), target, team=None)
            frozen_roundtrip(target / "cortexshift-bridge.exe", root, project.id, env)
        installation(installer, target, team=args.team)
        executable = verify_application(target, version, team=args.team)
        bridge = executable.parent / ("cortexshift-bridge" if MAC else "cortexshift-bridge.exe")
        if args.windows_signer_subject:
            for binary in (executable, bridge, target / "uninstall.exe"):
                verify_windows(binary, args.windows_signer_subject)
        native_binary(executable, team=args.team)
        native_binary(bridge, team=args.team)
        inspect_bridge(bridge, scratch=base, team=args.team)
        frozen_roundtrip(bridge, root, project.id, env)
        if not args.skip_gui:
            launch(executable, base, env)
        if args.previous_app:
            shutil.rmtree(target)
            checked("ditto", str(args.previous_app.resolve(strict=True)), str(target))
            frozen_roundtrip(bridge, root, project.id, env)
            print("Previous macOS bundle read the workspace again after rollback", flush=True)
        if not MAC:
            try:
                installation(installer, target, team=None)
                frozen_roundtrip(bridge, root, project.id, env)
            finally:
                uninstaller = target / "uninstall.exe"
                if uninstaller.exists():
                    subprocess.run(f'"{uninstaller}" /S _?={target}', check=True, timeout=120)
            assert not executable.exists() and not bridge.exists()
        assert (root / ".cortexshift").is_dir()
        print("Desktop installer and isolated frozen bridge smoke passed", flush=True)


if __name__ == "__main__":
    main()

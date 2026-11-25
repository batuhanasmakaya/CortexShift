"""Build the native one-file bridge for Tauri's current host target."""

import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

DESKTOP = Path(__file__).resolve().parents[1]
TAURI = DESKTOP / "src-tauri"
TARGETS = {
    ("darwin", "arm64"): "aarch64-apple-darwin",
    ("win32", "amd64"): "x86_64-pc-windows-msvc",
}


def main() -> None:
    details = subprocess.check_output(["rustc", "-vV"], text=True)
    host = next(
        line.removeprefix("host: ") for line in details.splitlines() if line.startswith("host: ")
    )
    target = TARGETS.get((sys.platform, platform.machine().lower()))
    if host != target:
        raise SystemExit("Build with native Apple Silicon or Windows x64 Rust and Python.")
    suffix = ".exe" if sys.platform == "win32" else ""
    output = TAURI / "binaries"
    output.mkdir(parents=True, exist_ok=True)
    # Keep build scratch outside Vite's frontend output directory.
    build = TAURI / "target" / "bridge-build"
    build.mkdir(parents=True, exist_ok=True)
    signing: list[str] = []
    if sys.platform == "darwin":
        signing = ["--target-arch", "arm64"]
        if identity := os.environ.get("APPLE_SIGNING_IDENTITY"):
            # Sign the rewritten Mach-O payloads before PyInstaller embeds them.
            signing += ["--codesign-identity", identity]
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--onefile",
            "--console",
            "--noupx",
            "--name",
            "cortexshift-bridge",
            "--paths",
            str(DESKTOP.parent / "src"),
            "--workpath",
            str(build / "work"),
            "--specpath",
            str(build),
            "--distpath",
            str(build / "out"),
            *signing,
            str(Path(__file__).with_name("bridge_entry.py")),
        ],
        check=True,
        env=os.environ | {"PYINSTALLER_CONFIG_DIR": str(build / "cache")},
    )
    destination = output / f"cortexshift-bridge-{host}{suffix}"
    shutil.copy2(build / "out" / f"cortexshift-bridge{suffix}", destination)
    reply = subprocess.run(
        [str(destination)],
        input=b'{"version":1,"id":1,"op":"hello"}\n',
        capture_output=True,
        timeout=20,
        check=True,
    )
    frame = json.loads(reply.stdout)
    if frame.get("id") != 1 or frame.get("result", {}).get("protocol") != 1:
        raise SystemExit("Frozen bridge handshake failed.")
    print(f"Built and smoke-tested {destination.name}")


if __name__ == "__main__":
    main()

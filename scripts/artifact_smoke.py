"""Isolated wheel/sdist/pipx release tests. Requires uv; never publishes."""

import argparse
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(args: list[str], cwd: Path, env: dict[str, str]) -> None:
    subprocess.run(args, cwd=cwd, env=env, check=True, timeout=600)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    parser.add_argument("--skip-pipx", action="store_true", help="Only for matrix duplication")
    args = parser.parse_args()
    dist = args.dist.resolve()
    (wheel,) = dist.glob("*.whl")
    (sdist,) = dist.glob("*.tar.gz")
    uv = shutil.which("uv")
    assert uv, "uv is required"
    with tempfile.TemporaryDirectory(prefix="cortexshift release ") as temporary:
        base = Path(temporary)
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in {"PYTHONPATH", "VIRTUAL_ENV", "PYTHONHOME"}
            and not k.startswith("CORTEXSHIFT_")
        }
        user_dir = base / "isolated user"
        user_dir.mkdir()
        # Child-only home isolation; never changes the caller's shell environment.
        env.update(
            HOME=str(user_dir),
            USERPROFILE=str(user_dir),
            PYTHONNOUSERSITE="1",
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL=os.devnull,
        )
        driver = base / "installed_smoke.py"
        shutil.copyfile(ROOT / "scripts/installed_smoke.py", driver)
        extracted = base / "source"
        extracted.mkdir()
        with tarfile.open(sdist) as archive:
            archive.extractall(extracted, filter="data")
        (source,) = extracted.iterdir()
        rebuilt = base / "rebuilt"
        run([uv, "build", "--wheel", "--out-dir", str(rebuilt)], source, env)
        (derived,) = rebuilt.glob("*.whl")
        for label, artifact in [("wheel", wheel), ("sdist", derived)]:
            venv = base / f"{label} environment"
            run([uv, "venv", "--python", sys.executable, str(venv)], base, env)
            python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            run([uv, "pip", "install", "--python", str(python), str(artifact)], base, env)
            workspace = base / f"{label} workspace"
            workspace.mkdir()
            run([str(python), str(driver)], workspace, env)
            print(f"{label} clean-room: PASS", flush=True)
        # Source archive must also support downstream contributors, with no checkout imports.
        run([uv, "sync", "--locked", "--python", sys.executable], source, env)
        run(
            [
                uv,
                "run",
                "--locked",
                "pytest",
                "tests/unit/test_version.py",
                "tests/unit/test_sqlite_v6_migrations.py",
                "--no-cov",
            ],
            source,
            env,
        )
        if not args.skip_pipx:
            env.update(
                PIPX_HOME=str(base / "pipx home"),
                PIPX_BIN_DIR=str(base / "pipx bin"),
                PIPX_MAN_DIR=str(base / "pipx man"),
                PIPX_DEFAULT_PYTHON=sys.executable,
            )
            pipx = [uv, "tool", "run", "--from", "pipx==1.11.0", "pipx"]
            run([*pipx, "install", "--python", sys.executable, str(wheel)], base, env)
            command = base / "pipx bin" / ("cortexshift.exe" if os.name == "nt" else "cortexshift")
            run([str(command), "--version"], base, env)
            run([str(command), "--help"], base, env)
            run([*pipx, "uninstall", "cortexshift"], base, env)
            print("Isolated pipx install/run/uninstall: PASS", flush=True)


if __name__ == "__main__":
    main()

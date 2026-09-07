"""Offline release validation; never mutates Git or publishes artifacts."""

import argparse
import configparser
import hashlib
import re
import subprocess
import tarfile
import tomllib
import zipfile
from email.parser import BytesParser
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CANONICAL_REPOSITORY = "https://github.com/batuhanasmakaya/CortexShift"
REQUIRED_URLS = {
    "Homepage": CANONICAL_REPOSITORY,
    "Repository": CANONICAL_REPOSITORY,
    "Issues": f"{CANONICAL_REPOSITORY}/issues",
    "Changelog": f"{CANONICAL_REPOSITORY}/blob/main/CHANGELOG.md",
    "Security": f"{CANONICAL_REPOSITORY}/security",
}
FORBIDDEN = {
    ".cortexshift",
    ".venv",
    "__pycache__",
    ".DS_Store",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "state.sqlite3",
    "agent.lock",
}


def validate_tag(tag: str, version: str) -> None:
    if tag != f"v{version}" or not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        raise ValueError(f"Release tag {tag!r} does not match v{version}")


def validate_names(names: list[str]) -> None:
    for name in names:
        parts = Path(name).parts
        if FORBIDDEN.intersection(parts) or name.endswith((".pyc", ".sqlite3", ".log")):
            raise ValueError(f"Forbidden release content: {name}")
        if name.startswith("/") or ".." in parts:
            raise ValueError(f"Unsafe archive path: {name}")


def validate_artifacts(dist: Path, project: dict[str, Any]) -> list[Path]:
    version = project["version"]
    wheel = dist / f"cortexshift-{version}-py3-none-any.whl"
    sdist = dist / f"cortexshift-{version}.tar.gz"
    if set(dist.glob("*.whl")) != {wheel} or set(dist.glob("*.tar.gz")) != {sdist}:
        raise ValueError("Expected exactly one matching wheel and sdist")
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        validate_names(names)
        assert "cortexshift/tui/cortexshift.tcss" in names, "Missing installed stylesheet"
        prefix = f"cortexshift-{version}.dist-info/"
        metadata = BytesParser().parsebytes(archive.read(prefix + "METADATA"))
        for key, expected in {
            "Name": "cortexshift",
            "Version": version,
            "Requires-Python": project["requires-python"],
            "License-Expression": "MIT",
            "Description-Content-Type": "text/markdown",
        }.items():
            assert metadata[key] == expected, f"Incorrect metadata: {key}"
        assert "LICENSE" in metadata.get_all("License-File", []), "Missing license metadata"
        assert set(metadata.get_all("Project-URL", [])) == {
            f"{label}, {url}" for label, url in project["urls"].items()
        }, "Project URL drift between pyproject and built metadata"
        assert archive.read(prefix + "licenses/LICENSE") == (ROOT / "LICENSE").read_bytes()
        assert "Switch agents. Keep the context." in metadata.get_payload()
        runtime = [r for r in metadata.get_all("Requires-Dist", []) if "extra ==" not in r]

        def normalize(value: str) -> str:
            return re.sub(r"\s+", "", value).lower()

        # Backends may reorder comma-separated version bounds.
        from packaging.requirements import Requirement

        assert {str(Requirement(normalize(r))) for r in runtime} == {
            str(Requirement(normalize(r))) for r in project["dependencies"]
        }, "Runtime dependency drift"
        entries = configparser.ConfigParser()
        entries.read_string(archive.read(prefix + "entry_points.txt").decode())
        assert dict(entries["console_scripts"]) == {"cortexshift": "cortexshift.cli.app:app"}, (
            "Entrypoint drift"
        )
    with tarfile.open(sdist) as source_archive:
        names = source_archive.getnames()
        validate_names(names)
        prefix = f"cortexshift-{version}/"
        for path in [
            "src/cortexshift/tui/cortexshift.tcss",
            "LICENSE",
            "pyproject.toml",
            "uv.lock",
            "tests/unit/test_version.py",
            "scripts/artifact_smoke.py",
        ]:
            assert prefix + path in names, f"Missing sdist content: {path}"
        assert all(m.isfile() or m.isdir() for m in source_archive.getmembers()), (
            "Archive links rejected"
        )
    return [wheel, sdist]


def check(
    root: Path, *, tag: str | None = None, clean: bool = False, dist: Path | None = None
) -> list[Path]:
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    version = project["version"]
    assert project["name"] == "cortexshift"
    assert project["requires-python"] == ">=3.12"
    assert project["license"] == "MIT"
    assert project["urls"] == REQUIRED_URLS, "Canonical project URLs are missing or altered"
    assert re.search(rf"^## .*\b{re.escape(version)}\b", (root / "CHANGELOG.md").read_text(), re.M)
    if tag:
        validate_tag(tag, version)
    if (root / ".git").exists():
        names = (
            subprocess.check_output(
                ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=root
            )
            .decode()
            .split("\0")
        )
        validate_names([n for n in names if n and (root / n).exists()])
        if clean:
            status = subprocess.check_output(["git", "status", "--porcelain"], cwd=root)
            assert not status.strip(), "Release requires a clean committed source tree"
    elif clean:
        raise ValueError("Clean Git check requires a checkout")
    return validate_artifacts(dist, project) if dist else []


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag")
    parser.add_argument("--require-clean", action="store_true")
    parser.add_argument("--dist", type=Path)
    parser.add_argument("--checksums", action="store_true")
    args = parser.parse_args()
    artifacts = check(ROOT, tag=args.tag, clean=args.require_clean, dist=args.dist)
    if args.checksums:
        assert args.dist and artifacts, "--checksums requires --dist"
        lines = [f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in artifacts]
        (args.dist / "SHA256SUMS").write_text("".join(lines), encoding="utf-8")
    print("Release checks passed (local validation; external publication is separate).")


if __name__ == "__main__":
    main()

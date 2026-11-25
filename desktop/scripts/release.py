"""Validate Desktop release metadata and stage bytes for a separate publication approval."""

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

DESKTOP = Path(__file__).resolve().parents[1]
ROOT = DESKTOP.parent
REPOSITORY = "batuhanasmakaya/CortexShift"
FEED = "https://batuhanasmakaya.github.io/CortexShift/desktop/stable.json"
VERSION = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
PLATFORMS = {
    "darwin-aarch64": "macos-arm64.app.tar.gz",
    "windows-x86_64": "windows-x64-setup.exe",
}
TARGETS = ("aarch64-apple-darwin", "x86_64-pc-windows-msvc")
REVIEWED_RUSTSEC = {
    ("RUSTSEC-2026-0543", "chrono", "0.4.45", "unmaintained"),
    ("RUSTSEC-2024-0370", "proc-macro-error", "1.0.4", "unmaintained"),
    ("RUSTSEC-2026-0371", "zbus_names", "4.3.5", "unmaintained"),
    ("RUSTSEC-2026-0370", "zvariant", "5.15.1", "unmaintained"),
    ("RUSTSEC-2026-0372", "zvariant_derive", "5.15.1", "unmaintained"),
    ("RUSTSEC-2024-0429", "glib", "0.18.5", "unsound"),
}


def tag_for(version: str) -> str:
    if not re.fullmatch(VERSION, version) or any(int(part) > 65535 for part in version.split(".")):
        raise ValueError("Desktop version must be stable X.Y.Z and fit Windows version fields")
    return f"desktop-v{version}"


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def preflight(tag: str | None = None, *, clean: bool = False, tagged: bool = False) -> str:
    tauri = DESKTOP / "src-tauri"
    config = json.loads((tauri / "tauri.conf.json").read_text(encoding="utf-8"))
    version = config["version"]
    manifest = json.loads((DESKTOP / "package.json").read_text(encoding="utf-8"))
    npm = json.loads((DESKTOP / "package-lock.json").read_text(encoding="utf-8"))
    rust = tomllib.loads((tauri / "Cargo.toml").read_text(encoding="utf-8"))["package"]
    lock = tomllib.loads((tauri / "Cargo.lock").read_text(encoding="utf-8"))["package"]
    (locked_rust,) = [package["version"] for package in lock if package["name"] == rust["name"]]
    if any(
        value != version
        for value in (
            manifest["version"],
            npm["version"],
            npm["packages"][""]["version"],
            rust["version"],
            locked_rust,
        )
    ):
        raise ValueError("Desktop version differs across Tauri, npm or Cargo metadata")
    expected = tag_for(version)
    if tag is not None and tag != expected:
        raise ValueError(f"Expected Desktop tag {expected}")
    if clean and git("status", "--porcelain", "--untracked-files=all"):
        raise ValueError("Desktop distribution requires a clean revision")
    if tagged and (
        tag is None
        or git("rev-parse", f"refs/tags/{expected}^{{commit}}") != git("rev-parse", "HEAD")
    ):
        raise ValueError("Desktop tag must identify the exact checked-out commit")
    if "updater" in config.get("plugins", {}) or config["bundle"].get("createUpdaterArtifacts"):
        raise ValueError("Base configuration must leave the production updater disabled")
    for name in config["bundle"]["icon"]:
        if not (tauri / name).is_file():
            raise ValueError(f"Missing configured app icon {name}")
    return version


def audit_gate(path: Path) -> None:
    audit = json.loads(path.read_text(encoding="utf-8"))
    if audit["vulnerabilities"]["count"] or audit["vulnerabilities"]["list"]:
        raise ValueError("Resolve the RustSec vulnerabilities before distribution")
    for category, warnings in audit["warnings"].items():
        for warning in warnings:
            item = (
                warning["advisory"]["id"],
                warning["package"]["name"],
                warning["package"]["version"],
                category,
            )
            if item not in REVIEWED_RUSTSEC:
                raise ValueError(f"Review new RustSec warning before distribution: {item}")


def verify_reviewed_warnings_are_outside_release_targets() -> None:
    reviewed = {(name, f"v{version}") for _, name, version, _ in REVIEWED_RUSTSEC}
    for target in TARGETS:
        tree = subprocess.check_output(
            [
                "cargo",
                "tree",
                "--locked",
                "--target",
                target,
                "-e",
                "normal,build",
                "--prefix",
                "none",
                "--format",
                "{p}",
            ],
            cwd=DESKTOP / "src-tauri",
            text=True,
        )
        selected = {tuple(line.split()[:2]) for line in tree.splitlines()}
        if exposed := reviewed & selected:
            raise ValueError(f"Reviewed RustSec dependencies now selected for {target}: {exposed}")


def signing_config(path: Path, *, windows_signer: str | None) -> None:
    key = os.environ.get("DESKTOP_UPDATER_PUBLIC_KEY", "").strip()
    try:
        public = base64.b64decode(key, validate=True).decode("ascii").splitlines()
        if (
            len(public) != 2
            or not public[0].startswith("untrusted comment: ")
            or len(base64.b64decode(public[1], validate=True)) != 42
        ):
            raise ValueError
    except ValueError:
        raise ValueError("Set DESKTOP_UPDATER_PUBLIC_KEY to the Tauri .pub contents") from None
    bundle: dict[str, object] = {"createUpdaterArtifacts": True}
    if windows_signer:
        bundle["windows"] = {
            "signCommand": {"cmd": "pwsh", "args": ["-NoProfile", "-File", windows_signer, "%1"]}
        }
    path.write_text(
        json.dumps(
            {
                "bundle": bundle,
                "plugins": {
                    "updater": {"endpoints": [FEED], "pubkey": key, "requireSignedVersion": True}
                },
            }
        ),
        encoding="utf-8",
    )


def asset_name(version: str, suffix: str) -> str:
    return f"CortexShift-Desktop-{version}-{suffix}"


def checksum_file(directory: Path) -> None:
    lines = []
    for artifact in sorted(directory.iterdir()):
        if not artifact.is_file() or artifact.name == "SHA256SUMS":
            continue
        with artifact.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        lines.append(f"{digest}  {artifact.name}\n")
    (directory / "SHA256SUMS").write_text("".join(lines), encoding="ascii", newline="\n")


def stage_native(version: str, bundle: Path, out: Path, *, mac: bool, signing: Path | None) -> None:
    out.mkdir(parents=True, exist_ok=False)
    suffix = "macos-arm64.dmg" if mac else "windows-x64-setup.exe"
    matches = bundle.glob("dmg/*.dmg") if mac else bundle.glob("nsis/*-setup.exe")
    (installer,) = matches
    stage = out / asset_name(version, suffix if signing else f"unsigned-{suffix}")
    shutil.copyfile(installer, stage)
    if signing:
        update = bundle / "macos/CortexShift.app.tar.gz" if mac else installer
        name = asset_name(version, PLATFORMS["darwin-aarch64"] if mac else suffix)
        if mac:
            shutil.copyfile(update, out / name)
        shutil.copyfile(update.with_name(update.name + ".sig"), out / (name + ".sig"))
        config = json.loads(signing.read_text(encoding="utf-8"))
        key = config["plugins"]["updater"]["pubkey"].encode("ascii")
        (out / "PUBLIC-KEY.sha256").write_text(
            hashlib.sha256(key).hexdigest() + "\n", encoding="ascii", newline="\n"
        )
    checksum_file(out)


def manifest(version: str, directory: Path, notes: str) -> dict[str, object]:
    tag = tag_for(version)
    dmg = directory / asset_name(version, "macos-arm64.dmg")
    if not dmg.is_file() or not dmg.stat().st_size:
        raise ValueError("Missing macOS installer")
    platforms = {}
    for platform, suffix in PLATFORMS.items():
        name = asset_name(version, suffix)
        asset = directory / name
        if not asset.is_file() or not asset.stat().st_size:
            raise ValueError(f"Missing Desktop asset {name}")
        signature = (directory / (name + ".sig")).read_text(encoding="ascii").strip()
        decoded = base64.b64decode(signature, validate=True).decode("ascii").splitlines()
        if len(decoded) != 4 or f"version:{version}" not in decoded[2].split("\t"):
            raise ValueError(f"Missing version-bound Tauri signature for {name}")
        platforms[platform] = {
            "signature": signature,
            "url": f"https://github.com/{REPOSITORY}/releases/download/{tag}/{name}",
        }
    return {"version": version, "notes": notes, "platforms": platforms}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag")
    parser.add_argument("--require-clean", action="store_true")
    parser.add_argument("--require-tag", action="store_true")
    parser.add_argument("--audit", type=Path)
    parser.add_argument("--verify-target-graphs", action="store_true")
    parser.add_argument("--signing-config", type=Path)
    parser.add_argument("--windows-signer")
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--mac", action="store_true")
    parser.add_argument("--trusted-config", type=Path)
    parser.add_argument("--assemble", type=Path)
    parser.add_argument("--notes", type=Path)
    args = parser.parse_args()
    try:
        version = preflight(args.tag, clean=args.require_clean, tagged=args.require_tag)
        if args.audit:
            audit_gate(args.audit)
        if args.verify_target_graphs:
            verify_reviewed_warnings_are_outside_release_targets()
        if args.signing_config:
            signing_config(args.signing_config, windows_signer=args.windows_signer)
        if args.bundle:
            if not args.out:
                parser.error("--bundle requires --out")
            stage_native(version, args.bundle, args.out, mac=args.mac, signing=args.trusted_config)
        if args.assemble:
            if not args.notes:
                parser.error("--assemble requires --notes")
            result = manifest(version, args.assemble, args.notes.read_text(encoding="utf-8"))
            (args.assemble / "stable.json").write_text(
                json.dumps(result, indent=2) + "\n", encoding="utf-8"
            )
            checksum_file(args.assemble)
        print(tag_for(version))
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Desktop release check failed: {error}\n")


if __name__ == "__main__":
    main()

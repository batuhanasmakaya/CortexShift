import base64
import json
import shutil
from pathlib import Path

import pytest

import release


@pytest.mark.parametrize(
    "version", ["01.2.3", "0.2.0-rc.1", "0.2.0+private", "0.65536.0", "v1.2.3"]
)
def test_reject_noncanonical_version(version: str) -> None:
    with pytest.raises(ValueError, match="stable X.Y.Z"):
        release.tag_for(version)


def test_preflight_reads_locked_versions_and_rejects_python_tag() -> None:
    version = release.preflight()
    assert release.preflight(f"desktop-v{version}") == version
    with pytest.raises(ValueError, match="Expected Desktop tag"):
        release.preflight(f"v{version}")
    python = release.ROOT / ".github/workflows/release.yml"
    assert "startsWith(github.event.release.tag_name, 'v')" in python.read_text(encoding="utf-8")


def test_preflight_rejects_lockfile_drift(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for filename in (
        "package.json",
        "package-lock.json",
        "src-tauri/Cargo.toml",
        "src-tauri/Cargo.lock",
        "src-tauri/tauri.conf.json",
    ):
        path = tmp_path / filename
        path.parent.mkdir(exist_ok=True)
        shutil.copyfile(release.DESKTOP / filename, path)
    npm = tmp_path / "package-lock.json"
    metadata = json.loads(npm.read_text(encoding="utf-8"))
    metadata["packages"][""]["version"] = "99.0.0"
    npm.write_text(json.dumps(metadata), encoding="utf-8")
    monkeypatch.setattr(release, "DESKTOP", tmp_path)
    with pytest.raises(ValueError, match="version differs"):
        release.preflight()


def test_manifest_requires_both_installers_and_version_bound_signatures(tmp_path: Path) -> None:
    version = "1.2.3"
    with pytest.raises(ValueError, match="Missing macOS installer"):
        release.manifest(version, tmp_path, "Notes")
    (tmp_path / release.asset_name(version, "macos-arm64.dmg")).write_bytes(b"disk image")
    for suffix in release.PLATFORMS.values():
        basename = release.asset_name(version, suffix)
        with pytest.raises(ValueError, match="Missing Desktop asset"):
            release.manifest(version, tmp_path, "Notes")
        (tmp_path / basename).write_bytes(b"fixture")
        signature = tmp_path / f"{basename}.sig"
        with pytest.raises(FileNotFoundError):
            release.manifest(version, tmp_path, "Notes")
        # Layout tests only. The native verifier tests cryptographic validity separately.
        signature.write_bytes(base64.b64encode(b"a\nb\ntimestamp:1\tversion:9.9.9\nd"))
        with pytest.raises(ValueError, match="version-bound"):
            release.manifest(version, tmp_path, "Notes")
        signature.write_bytes(base64.b64encode(b"a\nb\ntimestamp:1\tversion:1.2.3\nd"))
    platforms = release.manifest(version, tmp_path, "Notes")["platforms"]
    assert isinstance(platforms, dict)
    assert set(platforms) == set(release.PLATFORMS)
    for platform, item in platforms.items():
        filename = release.asset_name(version, release.PLATFORMS[platform])
        assert (
            item["url"]
            == f"https://github.com/{release.REPOSITORY}/releases/download/desktop-v{version}/{filename}"
        )
        assert (tmp_path / filename).is_file()


def test_signing_config_fails_closed_without_public_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DESKTOP_UPDATER_PUBLIC_KEY", raising=False)
    config = tmp_path / "signing.json"
    with pytest.raises(ValueError, match="Tauri .pub contents"):
        release.signing_config(config, windows_signer=None)
    assert not config.exists()


def test_audit_gate_rejects_new_warnings_and_all_vulnerabilities(tmp_path: Path) -> None:
    report = tmp_path / "rustsec.json"
    data: dict[str, object] = {"vulnerabilities": {"count": 0, "list": []}, "warnings": {}}
    report.write_text(json.dumps(data), encoding="utf-8")
    release.audit_gate(report)
    data["warnings"] = {
        "unmaintained": [
            {
                "advisory": {"id": "RUSTSEC-0000-0000"},
                "package": {"name": "crate", "version": "1.2.3"},
            }
        ]
    }
    report.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="Review new RustSec warning"):
        release.audit_gate(report)
    data["vulnerabilities"] = {"count": 1, "list": [{}]}
    report.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="Resolve the RustSec vulnerabilities"):
        release.audit_gate(report)


def test_signing_and_staging_are_scoped_to_approved_manual_distribution() -> None:
    workflow = (release.ROOT / ".github/workflows/desktop-release.yml").read_text(encoding="utf-8")
    verification = (release.ROOT / ".github/workflows/desktop.yml").read_text(encoding="utf-8")
    assert "on:\n  workflow_dispatch:" in workflow and "pull_request_target" not in workflow
    assert "contents: write" not in workflow
    assert workflow.count("github.ref_protected") == 2
    assert (
        "environment: desktop-sign-macos" in workflow
        and "environment: desktop-sign-windows" in workflow
    )
    assert "secrets." not in verification and "id-token: write" not in verification

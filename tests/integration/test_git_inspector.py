"""Integration tests for GitRepositoryInspector using isolated temporary Git repositories."""

import subprocess
from pathlib import Path

from cortexshift.adapters.git.inspector import GitRepositoryInspector
from cortexshift.domain.git import RepositoryInspectionStatus


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Helper to run git commands in a temporary test directory."""
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )


def test_git_inspector_non_git_directory(tmp_path: Path) -> None:
    """Verify inspection on a directory that is not a Git repository."""
    inspector = GitRepositoryInspector()
    inspection = inspector.inspect(tmp_path, "proj_test")
    assert inspection.status == RepositoryInspectionStatus.NOT_GIT_REPOSITORY
    assert inspection.git_available is True
    assert inspection.snapshot is None
    assert "not inside a Git repository" in (inspection.diagnostic or "")


def test_git_inspector_clean_repository(tmp_path: Path) -> None:
    """Verify clean repository has dirty=False and correct branch/HEAD."""
    _run_git(["init", "-b", "main"], cwd=tmp_path)
    _run_git(["config", "user.name", "CortexShift Test"], cwd=tmp_path)
    _run_git(["config", "user.email", "test@example.invalid"], cwd=tmp_path)

    (tmp_path / "file.txt").write_text("initial content\n")
    _run_git(["add", "file.txt"], cwd=tmp_path)
    _run_git(["commit", "-m", "Initial commit"], cwd=tmp_path)

    head_sha = _run_git(["rev-parse", "HEAD"], cwd=tmp_path).stdout.strip()

    inspector = GitRepositoryInspector()
    inspection = inspector.inspect(tmp_path, "proj_clean")
    assert inspection.status == RepositoryInspectionStatus.READY
    assert inspection.is_ready is True
    assert inspection.snapshot is not None

    snap = inspection.snapshot
    assert snap.branch == "main"
    assert snap.head_sha == head_sha
    assert snap.detached_head is False
    assert snap.dirty is False
    assert snap.staged_files == []
    assert snap.modified_files == []
    assert snap.untracked_files == []
    assert snap.conflicted_files == []


def test_git_inspector_tracked_modified_file(tmp_path: Path) -> None:
    """Verify modified tracked file is captured in modified_files and dirty=True."""
    _run_git(["init", "-b", "main"], cwd=tmp_path)
    _run_git(["config", "user.name", "CortexShift Test"], cwd=tmp_path)
    _run_git(["config", "user.email", "test@example.invalid"], cwd=tmp_path)

    (tmp_path / "app.py").write_text("print('v1')\n")
    _run_git(["add", "app.py"], cwd=tmp_path)
    _run_git(["commit", "-m", "add app.py"], cwd=tmp_path)

    (tmp_path / "app.py").write_text("print('v2')\n")

    inspector = GitRepositoryInspector()
    inspection = inspector.inspect(tmp_path, "proj_mod")
    assert inspection.snapshot is not None
    snap = inspection.snapshot
    assert snap.dirty is True
    assert snap.modified_files == ["app.py"]
    assert snap.staged_files == []
    assert snap.untracked_files == []
    assert snap.working_tree_diff_summary is not None
    assert "1 file changed" in snap.working_tree_diff_summary


def test_git_inspector_staged_and_untracked_files(tmp_path: Path) -> None:
    """Verify staged and untracked files are correctly partitioned."""
    _run_git(["init", "-b", "main"], cwd=tmp_path)
    _run_git(["config", "user.name", "CortexShift Test"], cwd=tmp_path)
    _run_git(["config", "user.email", "test@example.invalid"], cwd=tmp_path)

    (tmp_path / "init.txt").write_text("init")
    _run_git(["add", "init.txt"], cwd=tmp_path)
    _run_git(["commit", "-m", "init"], cwd=tmp_path)

    (tmp_path / "staged.py").write_text("staged")
    _run_git(["add", "staged.py"], cwd=tmp_path)

    (tmp_path / "untracked.py").write_text("untracked")

    inspector = GitRepositoryInspector()
    inspection = inspector.inspect(tmp_path, "proj_staged")
    assert inspection.snapshot is not None
    snap = inspection.snapshot
    assert snap.dirty is True
    assert snap.staged_files == ["staged.py"]
    assert snap.untracked_files == ["untracked.py"]
    assert snap.modified_files == []
    assert snap.staged_diff_summary is not None
    assert "1 file changed" in snap.staged_diff_summary


def test_git_inspector_mixed_index_and_worktree_state(tmp_path: Path) -> None:
    """Verify file with both staged changes and working tree modifications appears in both."""
    _run_git(["init", "-b", "main"], cwd=tmp_path)
    _run_git(["config", "user.name", "CortexShift Test"], cwd=tmp_path)
    _run_git(["config", "user.email", "test@example.invalid"], cwd=tmp_path)

    (tmp_path / "app.py").write_text("v1\n")
    _run_git(["add", "app.py"], cwd=tmp_path)
    _run_git(["commit", "-m", "c1"], cwd=tmp_path)

    # Stage modification
    (tmp_path / "app.py").write_text("v2 staged\n")
    _run_git(["add", "app.py"], cwd=tmp_path)

    # Further modify working tree without staging
    (tmp_path / "app.py").write_text("v3 worktree\n")

    inspector = GitRepositoryInspector()
    inspection = inspector.inspect(tmp_path, "proj_mixed")
    assert inspection.snapshot is not None
    snap = inspection.snapshot
    assert "app.py" in snap.staged_files
    assert "app.py" in snap.modified_files
    assert snap.dirty is True


def test_git_inspector_unborn_repository(tmp_path: Path) -> None:
    """Verify an initialized repository without commits is not treated as broken."""
    _run_git(["init", "-b", "main"], cwd=tmp_path)

    inspector = GitRepositoryInspector()
    inspection = inspector.inspect(tmp_path, "proj_unborn")
    assert inspection.status == RepositoryInspectionStatus.READY
    assert inspection.snapshot is not None
    snap = inspection.snapshot
    assert snap.branch == "main"
    assert snap.head_sha is None
    assert snap.detached_head is False
    assert snap.dirty is False


def test_git_inspector_detached_head(tmp_path: Path) -> None:
    """Verify detached HEAD reports branch=None and detached_head=True."""
    _run_git(["init", "-b", "main"], cwd=tmp_path)
    _run_git(["config", "user.name", "CortexShift Test"], cwd=tmp_path)
    _run_git(["config", "user.email", "test@example.invalid"], cwd=tmp_path)

    (tmp_path / "file.txt").write_text("c1")
    _run_git(["add", "file.txt"], cwd=tmp_path)
    _run_git(["commit", "-m", "c1"], cwd=tmp_path)

    sha = _run_git(["rev-parse", "HEAD"], cwd=tmp_path).stdout.strip()
    _run_git(["checkout", sha], cwd=tmp_path)

    inspector = GitRepositoryInspector()
    inspection = inspector.inspect(tmp_path, "proj_detached")
    assert inspection.snapshot is not None
    snap = inspection.snapshot
    assert snap.branch is None
    assert snap.detached_head is True
    assert snap.head_sha == sha


def test_git_inspector_monorepo_project_scoping(tmp_path: Path) -> None:
    """Verify nested CortexShift project only captures files in its subtree, ignoring siblings."""
    # Setup monorepo structure:
    # repo/
    # ├── .git/
    # ├── service-a/
    # │   └── changed-a.txt
    # └── service-b/
    #     ├── inside.txt
    #     └── sub/
    #         └── deep.txt
    _run_git(["init", "-b", "main"], cwd=tmp_path)
    _run_git(["config", "user.name", "CortexShift Test"], cwd=tmp_path)
    _run_git(["config", "user.email", "test@example.invalid"], cwd=tmp_path)

    service_a = tmp_path / "service-a"
    service_a.mkdir()
    service_b = tmp_path / "service-b"
    service_b.mkdir()
    (service_b / "sub").mkdir()

    (service_a / "base-a.txt").write_text("base a")
    (service_b / "base-b.txt").write_text("base b")
    _run_git(["add", "."], cwd=tmp_path)
    _run_git(["commit", "-m", "base commit"], cwd=tmp_path)

    # Modify sibling service-a
    (service_a / "changed-a.txt").write_text("unrelated sibling change")
    (service_a / "base-a.txt").write_text("modified sibling")

    # Modify target service-b
    (service_b / "base-b.txt").write_text("modified in target")
    (service_b / "inside.txt").write_text("untracked in target")
    (service_b / "sub" / "deep.txt").write_text("nested untracked")

    # Inspect from service-b
    inspector = GitRepositoryInspector()
    inspection = inspector.inspect(service_b, "proj_service_b")
    assert inspection.snapshot is not None
    snap = inspection.snapshot

    # Git root is top-level monorepo
    assert snap.git_root == str(tmp_path.resolve())
    assert snap.project_root == str(service_b.resolve())

    # Only service-b files should be captured, normalized relative to service-b
    assert snap.modified_files == ["base-b.txt"]
    assert sorted(snap.untracked_files) == ["inside.txt", "sub/deep.txt"]

    # Sibling files MUST NOT appear anywhere
    all_captured = (
        snap.staged_files + snap.modified_files + snap.untracked_files + snap.conflicted_files
    )
    for f in all_captured:
        assert "service-a" not in f
        assert "changed-a" not in f

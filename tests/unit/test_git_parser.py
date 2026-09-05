"""Unit tests for Git porcelain status parser."""

from cortexshift.adapters.git.parser import parse_porcelain_status


def test_parse_clean_repository() -> None:
    result = parse_porcelain_status("")
    assert result.staged_files == []
    assert result.modified_files == []
    assert result.untracked_files == []
    assert result.conflicted_files == []
    assert result.renames == {}


def test_parse_staged_modified_file() -> None:
    raw = "M  src/app.py\x00"
    result = parse_porcelain_status(raw)
    assert result.staged_files == ["src/app.py"]
    assert result.modified_files == []
    assert result.untracked_files == []
    assert result.conflicted_files == []


def test_parse_unstaged_modified_file() -> None:
    raw = " M src/app.py\x00"
    result = parse_porcelain_status(raw)
    assert result.staged_files == []
    assert result.modified_files == ["src/app.py"]
    assert result.untracked_files == []
    assert result.conflicted_files == []


def test_parse_staged_and_unstaged_same_file() -> None:
    # MM means modified in index and further modified in working tree
    raw = "MM src/app.py\x00"
    result = parse_porcelain_status(raw)
    assert result.staged_files == ["src/app.py"]
    assert result.modified_files == ["src/app.py"]
    assert result.untracked_files == []
    assert result.conflicted_files == []


def test_parse_untracked_file() -> None:
    raw = "?? new_file.txt\x00"
    result = parse_porcelain_status(raw)
    assert result.staged_files == []
    assert result.modified_files == []
    assert result.untracked_files == ["new_file.txt"]
    assert result.conflicted_files == []


def test_parse_deleted_files() -> None:
    # Staged deletion and unstaged deletion
    raw = "D  staged_del.txt\x00 D worktree_del.txt\x00"
    result = parse_porcelain_status(raw)
    assert result.staged_files == ["staged_del.txt"]
    assert result.modified_files == ["worktree_del.txt"]


def test_parse_renames() -> None:
    # In porcelain v1 -z, rename is 'R  <new_path>\0<old_path>\0'
    raw = "R  dest.txt\x00orig.txt\x00"
    result = parse_porcelain_status(raw)
    assert result.staged_files == ["dest.txt"]
    assert result.renames == {"dest.txt": "orig.txt"}


def test_parse_conflicts() -> None:
    # Both modified (UU), both added (AA), added by us (AU)
    raw = "UU conflict1.txt\x00AA conflict2.txt\x00AU conflict3.txt\x00"
    result = parse_porcelain_status(raw)
    assert result.conflicted_files == ["conflict1.txt", "conflict2.txt", "conflict3.txt"]
    assert result.staged_files == []
    assert result.modified_files == []


def test_parse_unusual_filenames() -> None:
    # Spaces, tab, newline, and Unicode in filenames
    raw = (
        "?? file with spaces.txt\x00"
        "?? file\twith\ttab.txt\x00"
        "?? file\nwith\nnewline.txt\x00"
        "?? café_crème.py\x00"
    )
    result = parse_porcelain_status(raw)
    assert result.untracked_files == [
        "café_crème.py",
        "file\twith\ttab.txt",
        "file\nwith\nnewline.txt",
        "file with spaces.txt",
    ]


def test_parse_project_prefix_scoping() -> None:
    # In a monorepo with prefix "service-b"
    raw = (
        " M service-a/ignored.txt\x00"
        "?? service-a/untracked.txt\x00"
        " M service-b/src/app.py\x00"
        "?? service-b/new.txt\x00"
        "R  service-b/renamed.txt\x00service-b/old.txt\x00"
    )
    result = parse_porcelain_status(raw, project_prefix="service-b/")
    assert result.modified_files == ["src/app.py"]
    assert result.untracked_files == ["new.txt"]
    assert result.staged_files == ["renamed.txt"]
    assert result.renames == {"renamed.txt": "old.txt"}
    # Sibling files in service-a must be excluded
    assert not any("service-a" in f for f in result.modified_files)
    assert not any("service-a" in f for f in result.untracked_files)

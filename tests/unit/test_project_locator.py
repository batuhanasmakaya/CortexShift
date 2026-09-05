"""Unit tests for ProjectLocator ancestor discovery."""

from pathlib import Path

from cortexshift.application.locator import ProjectLocator


def test_locator_uninitialized_directory(tmp_path: Path) -> None:
    """Verify that an uninitialized directory returns None."""
    assert ProjectLocator.find_project_root(tmp_path) is None
    assert ProjectLocator.is_initialized(tmp_path) is False


def test_locator_at_project_root(tmp_path: Path) -> None:
    """Verify discovering initialized root directly."""
    state_dir = tmp_path / ".cortexshift"
    state_dir.mkdir()
    db_file = state_dir / "state.sqlite3"
    db_file.touch()

    assert ProjectLocator.is_initialized(tmp_path) is True
    assert ProjectLocator.find_project_root(tmp_path) == tmp_path.resolve()


def test_locator_from_nested_subdirectories(tmp_path: Path) -> None:
    """Verify discovering project root from arbitrary deep descendant directories."""
    state_dir = tmp_path / ".cortexshift"
    state_dir.mkdir()
    db_file = state_dir / "state.sqlite3"
    db_file.touch()

    deep_path = tmp_path / "src" / "runtime" / "nested" / "package"
    deep_path.mkdir(parents=True)

    found = ProjectLocator.find_project_root(deep_path)
    assert found == tmp_path.resolve()


def test_locator_nearest_initialized_ancestor_wins(tmp_path: Path) -> None:
    """Verify that if an outer and inner project both have state, nearest ancestor wins."""
    outer_project = tmp_path / "outer"
    outer_state = outer_project / ".cortexshift"
    outer_state.mkdir(parents=True)
    (outer_state / "state.sqlite3").touch()

    inner_project = outer_project / "packages" / "inner"
    inner_state = inner_project / ".cortexshift"
    inner_state.mkdir(parents=True)
    (inner_state / "state.sqlite3").touch()

    deep_nested = inner_project / "src" / "code"
    deep_nested.mkdir(parents=True)

    found = ProjectLocator.find_project_root(deep_nested)
    assert found == inner_project.resolve()

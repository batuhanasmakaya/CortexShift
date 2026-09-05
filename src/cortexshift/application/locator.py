"""Project locator service for discovering initialized CortexShift workspaces."""

from pathlib import Path

STATE_DIR_NAME = ".cortexshift"
DATABASE_FILE_NAME = "state.sqlite3"


class ProjectLocator:
    """Discovers project roots by walking ancestor directories looking for CortexShift state."""

    @staticmethod
    def get_state_dir(project_root: Path) -> Path:
        """Return the .cortexshift state directory path for a given project root."""
        return project_root / STATE_DIR_NAME

    @staticmethod
    def get_database_path(project_root: Path) -> Path:
        """Return the state.sqlite3 database path for a given project root."""
        return project_root / STATE_DIR_NAME / DATABASE_FILE_NAME

    @classmethod
    def is_initialized(cls, path: Path) -> bool:
        """Check whether the given exact directory contains initialized CortexShift state."""
        return cls.get_database_path(path).is_file()

    @classmethod
    def find_project_root(cls, start_path: Path | None = None) -> Path | None:
        """Find the nearest ancestor directory containing an initialized CortexShift state.

        Walks up the directory tree starting from start_path (or current working directory).
        Nearest initialized ancestor wins. Does not search outside the ancestor chain and
        does not invoke Git.

        Args:
            start_path: Starting directory path. Defaults to Path.cwd().

        Returns:
            Resolved Path of the nearest initialized project root, or None if not found.
        """
        resolved_start = (start_path or Path.cwd()).resolve()

        # Check start_path and all parent directories
        for candidate in [resolved_start, *resolved_start.parents]:
            if cls.is_initialized(candidate):
                return candidate

        return None

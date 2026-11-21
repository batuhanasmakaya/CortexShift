"""Bind workspace operations to one verified root and its project-local state."""

import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.adapters.workspace_lease import LOCK_FILE_NAME, PROVIDER_LOCK_FILE_NAME
from cortexshift.application.locator import ProjectLocator
from cortexshift.domain.errors import ProjectConflictError, ProjectNotInitializedError
from cortexshift.domain.project import Project


def _layout_error(root: Path, detail: str) -> ProjectConflictError:
    return ProjectConflictError(
        f"Workspace identity mismatch at '{root}'. {detail} "
        "CortexShift blocked the operation to protect workspace state. "
        "Check the workspace location and its .cortexshift directory."
    )


def project_database_path(root: Path, locator: type[ProjectLocator] = ProjectLocator) -> Path:
    """Require the private directory, database and SQLite sidecars to be local."""
    path = locator.get_database_path(root)
    for entry in (
        path.parent,
        path,
        path.parent / LOCK_FILE_NAME,
        path.parent / PROVIDER_LOCK_FILE_NAME,
        *(path.with_name(path.name + suffix) for suffix in ("-wal", "-shm", "-journal")),
    ):
        try:
            info = entry.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or entry.is_junction():
            raise _layout_error(root, f"The state path '{entry}' redirects elsewhere.")
        if stat.S_ISREG(info.st_mode) and info.st_nlink > 1:
            raise _layout_error(root, f"The state file '{entry}' has multiple hard links.")
    return path


def _file_identity(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return stat.st_dev, stat.st_ino


@dataclass(frozen=True)
class WorkspaceIdentity:
    """The resolved root and Project pinned to a particular directory and database."""

    root: Path
    project: Project
    database_path: Path
    _directory_id: tuple[int, int]
    _database_id: tuple[int, int]

    def revalidate(self) -> None:
        """Detect a move, removal or replacement before acting on this workspace again."""
        try:
            if (
                self.root.resolve(strict=True) == self.root
                and project_database_path(self.root) == self.database_path
                and _file_identity(self.root) == self._directory_id
                and _file_identity(self.database_path) == self._database_id
            ):
                return
        except (OSError, RuntimeError):
            pass
        raise _layout_error(self.root, "The selected root or its state database has changed.")

    def validate_launch_root(self, cwd: Path) -> None:
        """Require the provider launch specification to use the pinned root."""
        self.revalidate()
        if cwd != self.root:
            raise _layout_error(self.root, f"The provider launch would use '{cwd}'.")


type WorkspaceStart = Path | str | WorkspaceIdentity | None


def _check_recorded_path(root: Path, project: Project) -> None:
    recorded = Path(project.repo_path)
    try:
        if recorded.is_absolute() and root.samefile(recorded.resolve(strict=True)):
            return
    except (OSError, RuntimeError, ValueError):
        pass
    raise ProjectConflictError(
        "Workspace identity mismatch. "
        f"The discovered workspace is '{root}', but its state records '{project.repo_path}'. "
        "CortexShift blocked the operation to prevent work in the wrong directory. "
        "Check whether the workspace was moved or copied and use the recorded workspace "
        "if that is where you intend to work."
    )


def open_project_state(
    root: Path,
    *,
    locator: type[ProjectLocator] = ProjectLocator,
    auto_migrate: bool = False,
    allow_empty_database: bool = False,
    store_override: SQLiteStateStore | None = None,
    expected: WorkspaceIdentity | None = None,
) -> tuple[WorkspaceIdentity | None, SQLiteStateStore]:
    """Verify the exact root before permitting writes or running pending migrations."""
    db_path = project_database_path(root, locator)
    if not db_path.is_file():
        raise ProjectNotInitializedError()
    try:
        directory_id = _file_identity(root)
        database_id = _file_identity(db_path)
    except FileNotFoundError as exc:
        raise ProjectNotInitializedError() from exc

    identity: WorkspaceIdentity | None = None

    def validate(store: SQLiteStateStore) -> None:
        nonlocal identity
        if expected is not None:
            expected.revalidate()
        if store.db_path != db_path:
            raise _layout_error(root, f"The open database is '{store.db_path}'.")
        project = store.get_default_project(allow_empty_database=allow_empty_database)
        if project is None:
            if not allow_empty_database:
                raise ProjectNotInitializedError()
            return
        _check_recorded_path(root, project)
        if expected is not None and expected.project.id != project.id:
            raise _layout_error(root, "The selected Project record has changed.")
        # Downstream builders get the verified spelling without changing the stored Project.
        identity = WorkspaceIdentity(
            root,
            project.model_copy(update={"repo_path": str(root)}),
            db_path,
            directory_id,
            database_id,
        )
        identity.revalidate()

    if store_override is not None:
        validate(store_override)
        return identity, store_override
    store = SQLiteStateStore.open_existing(db_path, validate, auto_migrate=auto_migrate)
    return identity, store


def open_workspace(
    start: WorkspaceStart = None,
    *,
    exact_root: bool = False,
    auto_migrate: bool = False,
    locator: type[ProjectLocator] = ProjectLocator,
    store_override: SQLiteStateStore | None = None,
) -> tuple[WorkspaceIdentity, SQLiteStateStore]:
    """Open verified state. A previously bound identity never repeats ancestor discovery."""
    root: Path | None
    if isinstance(start, WorkspaceIdentity):
        start.revalidate()
        root = start.root
    else:
        path = Path(start) if start is not None else Path.cwd()
        if not path.is_dir():
            raise ProjectNotInitializedError()
        root = path.resolve() if exact_root else locator.find_project_root(path)
        if root is None:
            raise ProjectNotInitializedError()

    identity, store = open_project_state(
        root,
        locator=locator,
        auto_migrate=auto_migrate,
        store_override=store_override,
        expected=start if isinstance(start, WorkspaceIdentity) else None,
    )
    assert identity is not None
    return identity, store


def resolve_workspace(
    start: WorkspaceStart = None, *, exact_root: bool = False
) -> WorkspaceIdentity:
    """Bind a caller to one workspace without retaining a database connection."""
    identity, store = open_workspace(start, exact_root=exact_root)
    store.close()
    return identity


@contextmanager
def read_workspace(
    path: Path,
    *,
    expected_project_id: str | None = None,
) -> Iterator[tuple[WorkspaceIdentity, SQLiteStateStore]]:
    """Open the exact selected root in an observation-only transaction."""
    if not path.is_absolute() or not path.is_dir():
        raise ProjectNotInitializedError()
    root = path.resolve(strict=True)
    db_path = project_database_path(root)
    if not db_path.is_file():
        raise ProjectNotInitializedError()
    directory_id = _file_identity(root)
    database_id = _file_identity(db_path)
    with SQLiteStateStore.read_existing(db_path) as store:
        workspace, _ = open_project_state(root, store_override=store)
        assert workspace is not None
        if (directory_id, database_id) != (workspace._directory_id, workspace._database_id):
            raise _layout_error(root, "The selected root or its database changed during opening.")
        if expected_project_id is not None and (
            path != workspace.root or workspace.project.id != expected_project_id
        ):
            raise _layout_error(root, "The registered Project record or root has changed.")
        yield workspace, store

"""Domain and application exception hierarchy for CortexShift."""


class CortexShiftError(Exception):
    """Base exception for all CortexShift domain and application errors."""


class ProjectNotInitializedError(CortexShiftError):
    """Raised when an operation requires an initialized CortexShift project but none was found."""

    def __init__(self, message: str = "CortexShift is not initialized here.") -> None:
        super().__init__(message)


class ProjectAlreadyInitializedError(CortexShiftError):
    """Raised when initialization is attempted on an already initialized project."""

    def __init__(self, project_name: str, path: str) -> None:
        super().__init__(f"CortexShift is already initialized for {project_name} at {path}.")
        self.project_name = project_name
        self.path = path


class ProjectConflictError(CortexShiftError):
    """Raised when existing project state conflicts with the target directory."""


class TaskNotFoundError(CortexShiftError):
    """Raised when a task with the specified identifier cannot be found."""

    def __init__(self, task_id: str) -> None:
        super().__init__(f"Task '{task_id}' was not found.")
        self.task_id = task_id


class NoActiveTaskError(CortexShiftError):
    """Raised when an active task operation is requested but no task is currently active."""

    def __init__(self, message: str = "No active task found for this project.") -> None:
        super().__init__(message)


class TaskNotActivatableError(CortexShiftError):
    """Raised when attempting to activate a task in an ineligible or terminal state."""


class TaskAlreadyCompletedError(CortexShiftError):
    """Raised when attempting to complete a task that is already completed."""

    def __init__(self, task_id: str) -> None:
        super().__init__(f"Task '{task_id}' is already completed.")
        self.task_id = task_id


class DatabaseStateError(CortexShiftError):
    """Base exception for persistent storage failures."""


class UnsupportedSchemaVersionError(DatabaseStateError):
    """Raised when database schema version is newer than supported by this CortexShift version."""

    def __init__(self, current_version: int, max_supported_version: int) -> None:
        super().__init__(
            f"This CortexShift state was created by a newer incompatible version "
            f"(schema v{current_version}, supported up to v{max_supported_version})."
        )
        self.current_version = current_version
        self.max_supported_version = max_supported_version


class StateCorruptionError(DatabaseStateError):
    """Raised when database state is corrupted, malformed, or unreadable."""

    def __init__(self, detail: str) -> None:
        super().__init__(f"CortexShift state could not be opened: {detail}")
        self.detail = detail

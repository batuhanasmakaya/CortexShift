"""Domain and application exception hierarchy for CortexShift."""

from pathlib import Path


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


class RepositoryInspectionError(CortexShiftError):
    """Base exception for repository inspection failures."""


class GitNotInstalledError(RepositoryInspectionError):
    """Raised when Git is required but not installed or found in PATH."""

    def __init__(self, message: str = "Git executable was not found in PATH.") -> None:
        super().__init__(message)


class NotAGitRepositoryError(RepositoryInspectionError):
    """Raised when the project is not inside a Git repository."""

    def __init__(
        self,
        message: str = "This CortexShift project is not inside a Git repository.",
    ) -> None:
        super().__init__(message)


class GitProbeTimeoutError(RepositoryInspectionError):
    """Raised when a Git command times out during inspection."""

    def __init__(self, message: str = "Git inspection timed out.") -> None:
        super().__init__(message)


class GitProbeError(RepositoryInspectionError):
    """Raised when a Git inspection command fails unexpectedly."""

    def __init__(self, message: str = "Git repository inspection failed.") -> None:
        super().__init__(message)


class SnapshotNotFoundError(CortexShiftError):
    """Raised when a repository snapshot with the specified identifier cannot be found."""

    def __init__(self, snapshot_id: str) -> None:
        super().__init__(f"Snapshot '{snapshot_id}' was not found.")
        self.snapshot_id = snapshot_id


class ProviderNotFoundError(CortexShiftError):
    """Raised when a requested provider executable is not found in PATH."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class UnknownProviderError(CortexShiftError, ValueError):
    """Raised when an unrecognized provider ID is requested."""

    def __init__(self, provider_id: str, supported: list[str]) -> None:
        self.provider_id = provider_id
        self.supported = supported
        super().__init__(
            f"Unknown provider '{provider_id}'. Supported providers: {', '.join(supported)}"
        )


class UnsupportedPromptError(CortexShiftError):
    """Raised when an initial prompt is provided to a provider that does not support it."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class WorkspaceLockedError(CortexShiftError):
    """Raised when an exclusive workspace lease cannot be acquired."""

    def __init__(
        self,
        message: str | None = None,
        lock_path: Path | None = None,
    ) -> None:
        self.lock_path = lock_path
        if message is None:
            message = (
                "Another CortexShift agent session is already active for this project.\n\n"
                "Lock file: .cortexshift/agent.lock\n\n"
                "Wait for that session to finish and try again."
            )
        super().__init__(message)


class TerminalRequiredError(CortexShiftError):
    """Raised when an interactive provider session is attempted without a usable TTY."""

    def __init__(
        self,
        message: str = (
            "Interactive provider launch requires a terminal (TTY).\n\n"
            "To simulate provider launch non-interactively, use: "
            "cortexshift run <provider> --dry-run"
        ),
    ) -> None:
        super().__init__(message)


class SessionNotFoundError(CortexShiftError):
    """Raised when a session with the specified identifier cannot be found."""

    def __init__(self, session_id: str) -> None:
        super().__init__(f"Session '{session_id}' was not found.")
        self.session_id = session_id

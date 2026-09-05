"""Port defining repository and Git state inspection capabilities."""

from typing import Protocol, runtime_checkable

from cortexshift.domain.git import GitSnapshot


@runtime_checkable
class RepositoryInspector(Protocol):
    """Abstract port for inspecting the repository filesystem and Git status.

    Extracts empirical repository state without embedding shell or git specifics
    into core domain logic.
    """

    def get_snapshot(self, repo_path: str) -> GitSnapshot:
        """Capture a point-in-time Git snapshot of the repository.

        Args:
            repo_path: Absolute path to the repository root directory.

        Returns:
            A populated GitSnapshot domain model.
        """
        ...

    def is_clean(self, repo_path: str) -> bool:
        """Return True if there are no untracked, modified, or staged files."""
        ...

    def get_diff_summary(self, repo_path: str) -> str:
        """Return a concise summary or diff stat of changes in the working tree."""
        ...

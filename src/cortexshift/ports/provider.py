"""Port defining the interface for AI coding agent provider adapters."""

from typing import Protocol, runtime_checkable

from cortexshift.domain.handoff import Handoff
from cortexshift.domain.provider import ProviderCapabilities, ProviderId
from cortexshift.domain.task import Task


@runtime_checkable
class ProviderAdapter(Protocol):
    """Abstract port for orchestrating an AI coding agent CLI.

    Concrete implementations wrap native provider CLIs (e.g. Claude Code,
    Codex, Antigravity) without polluting the core domain with vendor specifics.
    """

    @property
    def provider_id(self) -> ProviderId:
        """The stable unique identifier for this provider."""
        ...

    def probe(self) -> bool:
        """Check whether the native CLI is installed, licensed, and executable."""
        ...

    def get_capabilities(self) -> ProviderCapabilities:
        """Return the declared operational capabilities of this provider."""
        ...

    def launch_interactive(
        self,
        task: Task,
        handoff: Handoff | None = None,
    ) -> int:
        """Launch an interactive terminal session with this provider on the given task.

        Args:
            task: The persistent task being worked on.
            handoff: Advisory handoff payload if transitioning from another agent.

        Returns:
            Exit code of the process.
        """
        ...

    def run_headless(
        self,
        task: Task,
        instruction: str,
        handoff: Handoff | None = None,
    ) -> int:
        """Run a non-interactive single-command session if supported.

        Args:
            task: The persistent task being worked on.
            instruction: Prompt or command to execute.
            handoff: Advisory handoff payload if available.

        Returns:
            Exit code of the process.
        """
        ...

    def resume_native_session(
        self,
        task: Task,
        native_session_id: str,
    ) -> int:
        """Resume a previous provider-native session if supported.

        Args:
            task: The persistent task being worked on.
            native_session_id: Provider's internal thread or session ID.

        Returns:
            Exit code of the process.
        """
        ...

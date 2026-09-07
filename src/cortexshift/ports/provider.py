"""Port defining the interface for AI coding agent provider adapters."""

from pathlib import Path
from typing import Protocol, runtime_checkable

from cortexshift.domain.handoff import HandoffRecord
from cortexshift.domain.launch import LaunchSpecification
from cortexshift.domain.mcp_binding import McpSessionBinding
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
        handoff: HandoffRecord | None = None,
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
        handoff: HandoffRecord | None = None,
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


@runtime_checkable
class ProviderRuntimeAdapter(Protocol):
    """Port for building provider launch specifications without invoking processes directly."""

    @property
    def provider_id(self) -> ProviderId:
        """Canonical provider identifier."""
        ...

    @property
    def display_name(self) -> str:
        """Human-readable display name."""
        ...

    @property
    def executable(self) -> str:
        """Base name of the provider CLI executable."""
        ...

    def get_capabilities(self) -> ProviderCapabilities:
        """Return declared operational capabilities of this provider."""
        ...

    def build_launch_spec(
        self,
        project_root: Path,
        executable_path: str,
        prompt: str | None = None,
    ) -> LaunchSpecification:
        """Build concrete launch specification for this provider.

        Args:
            project_root: Absolute project root directory to serve as cwd.
            executable_path: Resolved absolute or PATH path to the executable.
            prompt: Optional initial prompt.

        Returns:
            A validated LaunchSpecification.

        Raises:
            UnsupportedPromptError: If prompt is provided but not supported for interactive launch.
        """
        ...


@runtime_checkable
class ManagedMcpBinder(Protocol):
    """Optional port for providers that configure the CortexShift MCP server per launch.

    A provider CLI spawns the MCP server itself, so CortexShift cannot rely on its own
    process environment reaching that grandchild: Codex, for one, sanitizes it. Providers
    implementing this port restate the trusted binding inside the MCP configuration they
    were launched with. Providers configured out of band, such as Antigravity's workspace
    file, implement nothing and keep their existing behaviour.
    """

    def bind_managed_mcp(
        self,
        launch_spec: LaunchSpecification,
        binding: McpSessionBinding,
    ) -> LaunchSpecification:
        """Return the launch specification with the managed binding declared to MCP.

        Implementations must be total: a specification carrying no CortexShift MCP
        configuration, or one belonging to another provider, is returned unchanged rather
        than rejected.
        """
        ...

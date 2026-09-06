"""Primary dashboard sections.

Each section is a self-contained view over presentation models supplied by the app. A
section never reaches into persistence, never runs Git, and never constructs provider
arguments; it renders what `TuiFacade` produced and raises intent back to the app.
"""

from enum import StrEnum

from textual.containers import Vertical

from cortexshift.tui.models import (
    TuiMcpStatus,
    TuiProviderStatus,
    TuiRepositoryModel,
    TuiStateSnapshot,
    WorkspaceActivity,
)


class TuiSection(StrEnum):
    """The primary navigable sections of the control center."""

    OVERVIEW = "overview"
    TASK = "task"
    REPOSITORY = "repository"
    SESSIONS = "sessions"
    CHECKPOINTS = "checkpoints"
    HANDOFFS = "handoffs"
    PROVIDERS = "providers"

    @property
    def label(self) -> str:
        """Display name shown in the sidebar and status line."""
        return self.value.capitalize()

    @property
    def shortcut(self) -> str:
        """The number key that jumps directly to this section."""
        return str(SECTION_ORDER.index(self) + 1)


SECTION_ORDER: tuple[TuiSection, ...] = (
    TuiSection.OVERVIEW,
    TuiSection.TASK,
    TuiSection.REPOSITORY,
    TuiSection.SESSIONS,
    TuiSection.CHECKPOINTS,
    TuiSection.HANDOFFS,
    TuiSection.PROVIDERS,
)


class SectionView(Vertical):
    """Base class for a primary section of the dashboard."""

    DEFAULT_CLASSES = "section"

    section: TuiSection

    def update_state(self, snapshot: TuiStateSnapshot) -> None:
        """Render newly loaded persisted state."""

    def update_repository(self, repository: TuiRepositoryModel | None) -> None:
        """Render the result of a live repository inspection."""

    def update_providers(
        self,
        providers: tuple[TuiProviderStatus, ...],
        mcp: TuiMcpStatus | None,
    ) -> None:
        """Render provider discovery and MCP integration status."""

    def update_activity(self, activity: WorkspaceActivity) -> None:
        """Render the observed workspace lease state."""

    def on_section_shown(self) -> None:
        """Called when this section becomes the visible section."""


__all__ = ["SECTION_ORDER", "SectionView", "TuiSection"]

"""The Providers section: passive discovery status and MCP integration.

Discovery is passive: no model prompts, no headless runs, no credential files, and no
account identifiers. Rendering MCP status never starts an MCP server.
"""

from rich.text import Text
from textual.app import ComposeResult
from textual.widgets import DataTable, Static

from cortexshift.tui.models import (
    TuiMcpStatus,
    TuiProviderStatus,
    WorkspaceActivity,
)
from cortexshift.tui.screens import SectionView, TuiSection
from cortexshift.tui.widgets import FieldList, Panel, marked


class ProvidersSection(SectionView):
    """Renders provider CLI availability and CortexShift MCP integration modes."""

    section = TuiSection.PROVIDERS

    def __init__(self, id: str | None = None) -> None:
        super().__init__(id=id)
        self._providers: tuple[TuiProviderStatus, ...] = ()
        self._mcp: TuiMcpStatus | None = None

    def compose(self) -> ComposeResult:
        """Build the providers layout."""
        yield Static(Text("Providers", style="bold"), classes="section-title")
        yield Static("", id="providers-heading")
        yield DataTable(id="providers-table", cursor_type="row")

        with Panel("Provider detail"):
            yield FieldList(id="providers-detail")

        with Panel("CortexShift MCP"):
            yield FieldList(id="providers-mcp")

        yield Static(
            Text(
                "X opens the provider action palette (run · resume · switch). "
                "G configures workspace MCP for Antigravity.\n"
                "No account identifiers, credential paths, or tokens are read or shown.",
                style="dim italic",
            ),
        )

    def on_mount(self) -> None:
        """Configure provider table columns."""
        table = self.query_one("#providers-table", DataTable)
        table.add_columns("Provider", "Installed", "Version", "Auth", "Native resume", "MCP")

    def update_providers(
        self,
        providers: tuple[TuiProviderStatus, ...],
        mcp: TuiMcpStatus | None,
    ) -> None:
        """Render discovery results and MCP integration status."""
        self._providers = providers
        self._mcp = mcp

        heading = self.query_one("#providers-heading", Static)
        table = self.query_one("#providers-table", DataTable)

        if not providers:
            heading.update(Text("Probing installed provider CLIs…", style="dim italic"))
        else:
            available = sum(1 for provider in providers if provider.available)
            heading.update(
                Text(f"{available} of {len(providers)} provider CLIs available on PATH.")
            )

        table.clear()
        for provider in providers:
            state = "available" if provider.installed else "unavailable"
            if provider.supports_exact_resume:
                resume = Text("✓ exact native resume", style="green")
            elif provider.supports_native_resume:
                resume = Text("· partial", style="dim")
            else:
                resume = Text("— not supported", style="dim")
            table.add_row(
                Text(provider.display_name),
                marked(state, state),
                Text(provider.version or "—"),
                Text(provider.authentication),
                resume,
                Text(provider.mcp_integration),
                key=provider.provider_id,
            )

        self._render_detail()
        self._render_mcp()

    def on_data_table_row_highlighted(self, _event: DataTable.RowHighlighted) -> None:
        """Render the highlighted provider's diagnostics."""
        self._render_detail()

    def _render_detail(self) -> None:
        detail = self.query_one("#providers-detail", FieldList)
        provider = self.selected_provider
        if provider is None:
            detail.set_fields([])
            return

        diagnostics = "; ".join(provider.diagnostics) if provider.diagnostics else "—"
        detail.set_fields(
            [
                ("Provider", provider.display_name),
                ("Provider ID", provider.provider_id),
                ("Executable", provider.executable),
                ("Installed", "yes" if provider.installed else "no"),
                ("Version", provider.version or "—"),
                ("Authentication", provider.authentication),
                ("Native resume", "yes" if provider.supports_native_resume else "no"),
                ("Exact native resume", "yes" if provider.supports_exact_resume else "no"),
                ("MCP integration", provider.mcp_integration),
                ("Diagnostics", diagnostics),
            ]
        )

    def _render_mcp(self) -> None:
        widget = self.query_one("#providers-mcp", FieldList)
        mcp = self._mcp
        if mcp is None:
            widget.set_fields([("MCP", Text("Loading…", style="dim italic"))])
            return

        antigravity = (
            Text("✓ configured", style="green")
            if mcp.antigravity_configured
            else Text("! not configured — press G to configure", style="yellow")
        )
        widget.set_fields(
            [
                ("MCP server", "cortexshift (not running from this dashboard)"),
                ("SDK", f"{'available' if mcp.sdk_available else 'unavailable'} {mcp.sdk_version}"),
                ("Transport", mcp.transport),
                ("Claude Code", mcp.claude_integration),
                ("OpenAI Codex", mcp.codex_integration),
                ("Antigravity", antigravity),
                ("Config path", mcp.antigravity_config_path or "—"),
                ("Read tools", f"{len(mcp.read_tools)}: {', '.join(mcp.read_tools)}"),
                ("Write tools", f"{len(mcp.write_tools)}: {', '.join(mcp.write_tools)}"),
                ("Resources", ", ".join(mcp.resources)),
            ]
        )

    @property
    def selected_provider(self) -> TuiProviderStatus | None:
        """The provider row under the cursor, or None."""
        table = self.query_one("#providers-table", DataTable)
        if table.row_count == 0:
            return None
        try:
            row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        except Exception:
            return None
        return next((p for p in self._providers if p.provider_id == row_key.value), None)

    def update_activity(self, activity: WorkspaceActivity) -> None:
        """Workspace lease state is surfaced by the app's status line."""

    def on_section_shown(self) -> None:
        """Focus the table so provider selection is keyboard-driven."""
        self.query_one("#providers-table", DataTable).focus()

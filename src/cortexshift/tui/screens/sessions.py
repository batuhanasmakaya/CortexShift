"""The Sessions section: CortexShift orchestration history.

Only orchestration metadata is shown. Provider transcripts, prompts, reasoning, and
terminal history are never read, stored, or rendered by CortexShift.
"""

from rich.text import Text
from textual.app import ComposeResult
from textual.widgets import DataTable, Static

from cortexshift.tui.models import (
    AUTHORITY_LABELS,
    TuiSessionRow,
    TuiStateSnapshot,
    format_relative,
    format_timestamp,
)
from cortexshift.tui.screens import SectionView, TuiSection
from cortexshift.tui.widgets import (
    EmptyState,
    FieldList,
    Panel,
    current_row_key,
    marked,
    restore_cursor,
)


class SessionsSection(SectionView):
    """Lists CortexShift sessions newest first with native resume metadata."""

    section = TuiSection.SESSIONS

    def __init__(self, id: str | None = None) -> None:
        super().__init__(id=id)
        self._rows: dict[str, TuiSessionRow] = {}

    def compose(self) -> ComposeResult:
        """Build the sessions layout."""
        yield Static(Text("Sessions", style="bold"), classes="section-title")
        yield EmptyState(
            "No sessions yet.",
            "Start one with:\n  cortexshift run PROVIDER\n\n"
            "Or press X here to choose a provider action.",
            id="sessions-empty",
        )
        yield DataTable(id="sessions-table", cursor_type="row")
        with Panel("Session detail", id="sessions-detail-panel"):
            yield FieldList(id="sessions-detail")
        yield Static(
            Text(
                "CortexShift orchestration metadata only — no provider transcripts, "
                "prompts, or reasoning are ever read or displayed.",
                style="dim italic",
            ),
        )

    def on_mount(self) -> None:
        """Configure session table columns."""
        table = self.query_one("#sessions-table", DataTable)
        table.add_columns(
            "Session", "Provider", "Status", "Native resume", "Started", "Ended", "Task"
        )

    def update_state(self, snapshot: TuiStateSnapshot) -> None:
        """Refresh the sessions table, preserving the operator's selection."""
        table = self.query_one("#sessions-table", DataTable)
        empty = self.query_one("#sessions-empty", EmptyState)
        detail_panel = self.query_one("#sessions-detail-panel", Panel)

        rows = snapshot.sessions
        self._rows = {row.id: row for row in rows}

        empty.display = not rows
        table.display = bool(rows)
        detail_panel.display = bool(rows)

        selected = current_row_key(table)
        table.clear()
        for row in rows:
            if row.native_resumable:
                resume = Text("✓ exact", style="green")
            elif row.native_session_id:
                resume = Text("· recorded", style="dim")
            else:
                resume = Text("— none", style="dim")

            ended = format_relative(row.ended_at)
            if row.ended_at is None and row.reconciled_at is not None:
                ended = f"reconciled {format_relative(row.reconciled_at)}"

            table.add_row(
                Text(row.short_id),
                Text(row.provider_id),
                marked(row.status, row.status),
                resume,
                Text(format_relative(row.started_at)),
                Text(ended),
                Text(row.task_id[:12]),
                key=row.id,
            )
        restore_cursor(table, selected)
        self._render_detail()

    def on_data_table_row_highlighted(self, _event: DataTable.RowHighlighted) -> None:
        """Show full canonical detail for the highlighted session."""
        self._render_detail()

    def _render_detail(self) -> None:
        table = self.query_one("#sessions-table", DataTable)
        detail = self.query_one("#sessions-detail", FieldList)
        key = current_row_key(table)
        row = self._rows.get(key) if key else None
        if row is None:
            detail.set_fields([])
            return

        detail.set_fields(
            [
                ("Session ID", row.id),
                ("Task ID", row.task_id),
                ("Provider", row.provider_id),
                ("Native session ID", row.native_session_id or "— (not recorded)"),
                ("Native resumable", "yes" if row.native_resumable else "no"),
                ("Resumed from", row.resumed_from_session_id or "—"),
                ("Status", row.status),
                ("Exit reason", row.exit_reason or "—"),
                ("Exit code", "—" if row.exit_code is None else str(row.exit_code)),
                ("Started at", format_timestamp(row.started_at)),
                ("Ended at", format_timestamp(row.ended_at)),
                ("Reconciled at", format_timestamp(row.reconciled_at)),
                ("Authority", AUTHORITY_LABELS[row.authority]),
            ]
        )

    @property
    def selected_session(self) -> TuiSessionRow | None:
        """The session row under the cursor, or None."""
        key = current_row_key(self.query_one("#sessions-table", DataTable))
        return self._rows.get(key) if key else None

    def on_section_shown(self) -> None:
        """Focus the table so the keyboard drives selection immediately."""
        table = self.query_one("#sessions-table", DataTable)
        if table.display:
            table.focus()

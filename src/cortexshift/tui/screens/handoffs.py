"""The Handoffs section: canonical cross-provider handoff history.

Rendered provider prompts are never persisted by CortexShift, so none are shown here.
What is displayed is the canonical payload the handoff was derived from.
"""

from rich.text import Text
from textual.app import ComposeResult
from textual.widgets import DataTable, Static

from cortexshift.tui.models import (
    AUTHORITY_LABELS,
    DataAuthority,
    TuiHandoffRow,
    TuiStateSnapshot,
    format_relative,
    format_timestamp,
)
from cortexshift.tui.screens import SectionView, TuiSection
from cortexshift.tui.widgets import (
    BulletList,
    EmptyState,
    FieldList,
    Panel,
    current_row_key,
    marked,
    restore_cursor,
)


class HandoffsSection(SectionView):
    """Lists handoffs newest first and renders the selected canonical payload."""

    section = TuiSection.HANDOFFS

    def __init__(self, id: str | None = None) -> None:
        super().__init__(id=id)
        self._rows: dict[str, TuiHandoffRow] = {}

    def compose(self) -> ComposeResult:
        """Build the handoffs layout."""
        yield Static(Text("Handoffs", style="bold"), classes="section-title")
        yield EmptyState(
            "No handoffs yet.",
            "Start with:\n  cortexshift run PROVIDER\n\n"
            "Then press X to switch the task to another agent, or preview a handoff "
            "without launching anything.",
            id="handoffs-empty",
        )
        yield DataTable(id="handoffs-table", cursor_type="row")

        with Panel(
            "Handoff detail",
            authority=DataAuthority.HISTORICAL,
            id="handoffs-detail-panel",
        ):
            yield FieldList(id="handoffs-detail")
            yield Static(Text("Completed", style="bold"), classes="panel-title")
            yield BulletList(id="handoffs-completed")
            yield Static(Text("Remaining", style="bold"), classes="panel-title")
            yield BulletList(id="handoffs-remaining")
            yield Static(Text("Files touched", style="bold"), classes="panel-title")
            yield BulletList(id="handoffs-files")

        yield Static(
            Text(
                "Press P to preview a fresh canonical handoff for a target provider. "
                "Previews persist nothing and consume no model quota.",
                style="dim italic",
            ),
        )

    def on_mount(self) -> None:
        """Configure handoff table columns."""
        table = self.query_one("#handoffs-table", DataTable)
        table.add_columns("Handoff", "From", "To", "Status", "Checkpoint", "Created")

    def update_state(self, snapshot: TuiStateSnapshot) -> None:
        """Refresh the handoffs table, preserving the operator's selection."""
        table = self.query_one("#handoffs-table", DataTable)
        empty = self.query_one("#handoffs-empty", EmptyState)
        detail_panel = self.query_one("#handoffs-detail-panel", Panel)

        rows = snapshot.handoffs
        self._rows = {row.id: row for row in rows}

        empty.display = not rows
        table.display = bool(rows)
        detail_panel.display = bool(rows)

        selected = current_row_key(table)
        table.clear()
        for row in rows:
            table.add_row(
                Text(row.short_id),
                Text(row.source_provider_id),
                Text(row.target_provider_id),
                marked(row.status, row.status),
                Text(row.checkpoint_id[:12] if row.checkpoint_id else "—"),
                Text(format_relative(row.created_at)),
                key=row.id,
            )
        restore_cursor(table, selected)
        self._render_detail()

    def on_data_table_row_highlighted(self, _event: DataTable.RowHighlighted) -> None:
        """Render the highlighted handoff in full."""
        self._render_detail()

    def _render_detail(self) -> None:
        key = current_row_key(self.query_one("#handoffs-table", DataTable))
        row = self._rows.get(key) if key else None

        detail = self.query_one("#handoffs-detail", FieldList)
        completed = self.query_one("#handoffs-completed", BulletList)
        remaining = self.query_one("#handoffs-remaining", BulletList)
        files = self.query_one("#handoffs-files", BulletList)

        if row is None:
            detail.set_fields([])
            completed.set_items([])
            remaining.set_items([])
            files.set_items([])
            return

        record = row.record
        payload = record.payload
        git = payload.git_state
        test_line = (
            Text(f"{payload.test_status.summary}  (Reported / unverified)", style="yellow")
            if payload.test_status.known
            else Text(payload.test_status.summary)
        )

        detail.set_fields(
            [
                ("Handoff ID", record.id),
                ("Protocol", f"v{record.protocol_version}"),
                ("Task", f"{payload.task_title} ({record.task_id})"),
                ("Source session", record.source_session_id),
                ("Source provider", str(record.source_provider_id)),
                ("Target session", record.target_session_id or "— (not launched)"),
                ("Target provider", str(record.target_provider_id)),
                ("Source checkpoint", record.source_checkpoint_id or "—"),
                ("Git snapshot", record.git_snapshot_id or "—"),
                ("Delivery status", record.status.value),
                (
                    "Failure",
                    record.failure_code.value if record.failure_code else "—",
                ),
                ("Created at", format_timestamp(record.created_at)),
                ("Delivered at", format_timestamp(record.delivered_at)),
                ("Objective", payload.original_objective),
                ("Current work", payload.current_work or "—"),
                ("Test status", test_line),
                (
                    "Git state",
                    f"{git.status.value} · {git.branch or '(detached)'} · "
                    f"{'dirty' if git.dirty else 'clean'}",
                ),
                ("Next action", payload.recommended_next_action),
                ("Authority", AUTHORITY_LABELS[DataAuthority.HISTORICAL]),
            ]
        )

        completed.set_items(list(payload.completed), empty="Nothing recorded as completed.")
        remaining.set_items(list(payload.remaining), empty="No remaining items recorded.")
        files.set_items(list(payload.files_touched), limit=15, empty="No files recorded.")

    @property
    def selected_handoff(self) -> TuiHandoffRow | None:
        """The handoff row under the cursor, or None."""
        key = current_row_key(self.query_one("#handoffs-table", DataTable))
        return self._rows.get(key) if key else None

    def on_section_shown(self) -> None:
        """Focus the table when there is anything to select."""
        table = self.query_one("#handoffs-table", DataTable)
        if table.display:
            table.focus()

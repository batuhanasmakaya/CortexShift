"""The Checkpoints section: immutable historical observations.

A checkpoint records what was true when it was captured. It is never presented as
current truth, and a reported test summary is never presented as a verified result.
"""

from rich.text import Text
from textual.app import ComposeResult
from textual.widgets import DataTable, Static

from cortexshift.domain.checkpoint import CheckpointTestProvenance
from cortexshift.tui.models import (
    AUTHORITY_LABELS,
    DataAuthority,
    TuiCheckpointRow,
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
    restore_cursor,
)


class CheckpointsSection(SectionView):
    """Lists checkpoints newest first and renders the selected one in full."""

    section = TuiSection.CHECKPOINTS

    def __init__(self, id: str | None = None) -> None:
        super().__init__(id=id)
        self._rows: dict[str, TuiCheckpointRow] = {}

    def compose(self) -> ComposeResult:
        """Build the checkpoints layout."""
        yield Static(Text("Checkpoints", style="bold"), classes="section-title")
        yield EmptyState(
            "No checkpoints yet.",
            "Create one with:\n  c\n\nCheckpoints capture task and repository state "
            "without holding the workspace lease.",
            id="checkpoints-empty",
        )
        yield DataTable(id="checkpoints-table", cursor_type="row")

        with Panel(
            "Checkpoint detail",
            authority=DataAuthority.HISTORICAL,
            id="checkpoints-detail-panel",
        ):
            yield FieldList(id="checkpoints-detail")
            yield Static(Text("Task snapshot", style="bold"), classes="panel-title")
            yield FieldList(id="checkpoints-task")
            yield Static(Text("Decisions", style="bold"), classes="panel-title")
            yield BulletList(id="checkpoints-decisions")
            yield Static(Text("Files touched", style="bold"), classes="panel-title")
            yield BulletList(id="checkpoints-files")

    def on_mount(self) -> None:
        """Configure checkpoint table columns."""
        table = self.query_one("#checkpoints-table", DataTable)
        table.add_columns("Checkpoint", "Kind", "Session", "Created", "Git state")

    def update_state(self, snapshot: TuiStateSnapshot) -> None:
        """Refresh the checkpoints table, preserving the operator's selection."""
        table = self.query_one("#checkpoints-table", DataTable)
        empty = self.query_one("#checkpoints-empty", EmptyState)
        detail_panel = self.query_one("#checkpoints-detail-panel", Panel)

        rows = snapshot.checkpoints
        self._rows = {row.id: row for row in rows}

        empty.display = not rows
        table.display = bool(rows)
        detail_panel.display = bool(rows)

        selected = current_row_key(table)
        table.clear()
        for row in rows:
            session_label = row.session_id[:12] if row.session_id else "—"
            table.add_row(
                Text(row.short_id),
                Text(row.kind),
                Text(session_label),
                Text(format_relative(row.created_at)),
                Text(row.git_summary),
                key=row.id,
            )
        restore_cursor(table, selected)
        self._render_detail()

    def on_data_table_row_highlighted(self, _event: DataTable.RowHighlighted) -> None:
        """Render the highlighted checkpoint in full."""
        self._render_detail()

    def _render_detail(self) -> None:
        key = current_row_key(self.query_one("#checkpoints-table", DataTable))
        row = self._rows.get(key) if key else None

        detail = self.query_one("#checkpoints-detail", FieldList)
        task_fields = self.query_one("#checkpoints-task", FieldList)
        decisions = self.query_one("#checkpoints-decisions", BulletList)
        files = self.query_one("#checkpoints-files", BulletList)

        if row is None:
            detail.set_fields([])
            task_fields.set_fields([])
            decisions.set_items([])
            files.set_items([])
            return

        payload = row.record.payload
        test_status = payload.test_status
        if not test_status.known:
            test_line: str | Text = "No test result recorded."
        elif test_status.provenance == CheckpointTestProvenance.VERIFIED:
            test_line = Text(f"{test_status.summary}  (verified)", style="green")
        else:
            test_line = Text(
                f"{test_status.summary}  (Reported / unverified)",
                style="yellow",
            )

        git = payload.git_state
        detail.set_fields(
            [
                ("Checkpoint ID", row.record.id),
                ("Kind", row.record.kind.value),
                ("Protocol", f"v{row.record.protocol_version}"),
                ("Created at", format_timestamp(row.record.created_at)),
                ("Session ID", row.record.session_id or "—"),
                ("Git snapshot ID", row.record.git_snapshot_id or "—"),
                ("Git state", f"{git.status.value} · {git.note}"),
                ("Branch", git.branch or "—"),
                ("HEAD", git.head_sha or "—"),
                ("Dirty", "yes" if git.dirty else "no"),
                (
                    "Changed counts",
                    f"staged {git.staged_count} · modified {git.modified_count} · "
                    f"untracked {git.untracked_count} · conflicted {git.conflicted_count}",
                ),
                ("Test status", test_line),
                ("Operator note", payload.operator_note or "—"),
                ("Authority", AUTHORITY_LABELS[DataAuthority.HISTORICAL]),
            ]
        )

        task = payload.task
        task_fields.set_fields(
            [
                ("Task ID", task.task_id),
                ("Title", task.task_title),
                ("Status", task.task_status),
                ("Current work", task.current_work or "—"),
                (
                    "Progress",
                    f"{len(task.completed)} completed · {len(task.remaining)} remaining · "
                    f"{len(task.known_issues)} issue(s)",
                ),
            ]
        )

        decisions.set_items(
            list(payload.decisions),
            empty="No decisions recorded in this checkpoint.",
        )
        files.set_items(
            list(payload.files_touched),
            limit=15,
            empty="No files recorded as touched.",
        )

    @property
    def selected_checkpoint(self) -> TuiCheckpointRow | None:
        """The checkpoint row under the cursor, or None."""
        key = current_row_key(self.query_one("#checkpoints-table", DataTable))
        return self._rows.get(key) if key else None

    def on_section_shown(self) -> None:
        """Focus the table when there is anything to select."""
        table = self.query_one("#checkpoints-table", DataTable)
        if table.display:
            table.focus()

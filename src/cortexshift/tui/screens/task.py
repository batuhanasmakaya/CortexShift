"""The Task section: complete canonical Task state, progress controls, and activation."""

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Static

from cortexshift.tui.models import (
    TuiStateSnapshot,
    TuiTaskRow,
    format_timestamp,
    truncate,
)
from cortexshift.tui.screens import SectionView, TuiSection
from cortexshift.tui.widgets import (
    BulletList,
    FieldList,
    Panel,
    current_row_key,
    marked,
    render_progress_bar,
    restore_cursor,
)


class TaskSection(SectionView):
    """Renders the active Task in full and lists every Task in the project."""

    section = TuiSection.TASK

    def __init__(self, id: str | None = None) -> None:
        super().__init__(id=id)
        self._rows: dict[str, TuiTaskRow] = {}

    def compose(self) -> ComposeResult:
        """Build the task layout."""
        yield Static(Text("Task", style="bold"), classes="section-title")
        yield Static("", id="task-heading")
        yield Static("", id="task-progress")

        # Identity spans the full width so canonical IDs and timestamps stay on one line
        # and remain copy-friendly.
        with Panel("Identity"):
            yield FieldList(id="task-identity")

        with Horizontal(classes="columns"):
            with Vertical(classes="column"):
                with Panel("Objective"):
                    yield Static("", id="task-objective")
                with Panel("Current work"):
                    yield Static("", id="task-current-work")
                with Panel("Requirements"):
                    yield BulletList(id="task-requirements")
                with Panel("Constraints"):
                    yield BulletList(id="task-constraints")
            with Vertical(classes="column"):
                with Panel("Completed"):
                    yield BulletList(id="task-completed")
                with Panel("Remaining"):
                    yield BulletList(id="task-remaining")
                with Panel("Known issues"):
                    yield BulletList(id="task-issues")

        with Panel("All tasks"):
            yield Static(
                Text(
                    "Enter or A activates the selected task · W set current work · "
                    "M mark completed · N add remaining · I record issue",
                    style="dim",
                ),
            )
            yield DataTable(id="task-table", cursor_type="row", zebra_stripes=False)

    def on_mount(self) -> None:
        """Configure the task table columns."""
        table = self.query_one("#task-table", DataTable)
        table.add_columns("Task", "Title", "Status", "Done", "Left", "Active")

    def update_state(self, snapshot: TuiStateSnapshot) -> None:
        """Render the active task in full and refresh the task table."""
        self._render_active(snapshot)
        self._render_table(snapshot)

    def _render_active(self, snapshot: TuiStateSnapshot) -> None:
        heading = self.query_one("#task-heading", Static)
        progress = self.query_one("#task-progress", Static)
        task = snapshot.active_task

        if task is None:
            heading.update(Text("No active task.", style="bold"))
            progress.update(
                Text(
                    "Activate a task from the table below, or create one with "
                    "`cortexshift task start`.",
                    style="dim",
                )
            )
            self.query_one("#task-identity", FieldList).set_fields([])
            self.query_one("#task-objective", Static).update(Text("—", style="dim"))
            self.query_one("#task-current-work", Static).update(Text("—", style="dim"))
            for widget_id in (
                "#task-requirements",
                "#task-constraints",
                "#task-completed",
                "#task-remaining",
                "#task-issues",
            ):
                self.query_one(widget_id, BulletList).set_items([])
            return

        title = Text()
        title.append(task.title, style="bold")
        title.append_text(Text("   "))
        title.append_text(marked(task.status, task.status))
        heading.update(title)
        progress.update(render_progress_bar(task.progress, width=28))

        self.query_one("#task-identity", FieldList).set_fields(
            [
                ("Task ID", task.id),
                ("Status", task.status),
                ("Created", format_timestamp(task.created_at)),
                ("Updated", format_timestamp(task.updated_at)),
            ]
        )
        self.query_one("#task-objective", Static).update(Text(task.objective))
        self.query_one("#task-current-work", Static).update(
            Text(task.current_work) if task.current_work else Text("—", style="dim italic")
        )
        self.query_one("#task-requirements", BulletList).set_items(
            list(task.requirements), empty="No requirements recorded."
        )
        self.query_one("#task-constraints", BulletList).set_items(
            list(task.constraints), empty="No constraints recorded."
        )
        self.query_one("#task-completed", BulletList).set_items(
            list(task.completed), empty="Nothing completed yet.", marker="✓"
        )
        self.query_one("#task-remaining", BulletList).set_items(
            list(task.remaining), empty="No remaining items recorded.", marker="□"
        )
        self.query_one("#task-issues", BulletList).set_items(
            list(task.known_issues), empty="No known issues recorded.", marker="!"
        )

    def _render_table(self, snapshot: TuiStateSnapshot) -> None:
        table = self.query_one("#task-table", DataTable)
        selected = current_row_key(table)

        self._rows = {row.id: row for row in snapshot.tasks}
        table.clear()
        for row in snapshot.tasks:
            table.add_row(
                Text(row.short_id),
                Text(truncate(row.title, 44)),
                marked(row.status, row.status),
                Text(str(row.completed_count)),
                Text(str(row.remaining_count)),
                Text("active" if row.is_active else "", style="bold green"),
                key=row.id,
            )
        restore_cursor(table, selected)

    # -- selection ---------------------------------------------------------

    @property
    def selected_task(self) -> TuiTaskRow | None:
        """The task row under the cursor, or None when the table is empty."""
        key = current_row_key(self.query_one("#task-table", DataTable))
        return self._rows.get(key) if key else None

    def on_section_shown(self) -> None:
        """Focus the task table so activation is reachable from the keyboard."""
        self.query_one("#task-table", DataTable).focus()

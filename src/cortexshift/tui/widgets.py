"""Reusable presentation widgets for the CortexShift control center.

Meaning is never carried by colour alone: every status marker pairs a glyph with its
styling so the dashboard stays readable on limited-colour terminals.
"""

from collections.abc import Sequence

from rich.table import Table
from rich.text import Text
from textual.containers import Vertical
from textual.widgets import DataTable, Static

from cortexshift.tui.models import (
    AUTHORITY_LABELS,
    DataAuthority,
    TuiTaskProgress,
)

MARK_OK = "✓"
MARK_WARN = "!"
MARK_FAIL = "×"
MARK_IDLE = "·"
MARK_UNKNOWN = "?"

PROGRESS_FILLED = "█"
PROGRESS_EMPTY = "░"


def status_marker(state: str) -> tuple[str, str]:
    """Return a (glyph, css class) pair for a status keyword.

    The glyph carries the meaning; the class only adds emphasis where colour exists.
    """
    normalized = state.strip().lower()
    if normalized in ("ok", "ready", "available", "completed", "delivered", "clean", "free"):
        return MARK_OK, "state-ok"
    if normalized in ("warn", "warning", "dirty", "interrupted", "unknown", "pending", "busy"):
        return MARK_WARN, "state-warn"
    if normalized in ("fail", "failed", "error", "unavailable", "cancelled"):
        return MARK_FAIL, "state-fail"
    if normalized in ("running", "initializing", "in_progress", "prepared"):
        return MARK_IDLE, "state-active"
    return MARK_UNKNOWN, "state-muted"


def marked(state: str, label: str) -> Text:
    """Render a glyph-prefixed status label."""
    glyph, css = status_marker(state)
    text = Text()
    text.append(f"{glyph} ", style=_STYLE_FOR_CLASS.get(css, ""))
    text.append(label)
    return text


_STYLE_FOR_CLASS = {
    "state-ok": "bold green",
    "state-warn": "bold yellow",
    "state-fail": "bold red",
    "state-active": "bold cyan",
    "state-muted": "dim",
}


def authority_label(authority: DataAuthority) -> str:
    """Human wording for a data authority level."""
    return AUTHORITY_LABELS[authority]


def render_progress_bar(progress: TuiTaskProgress, width: int = 20) -> Text:
    """Render structured task progress.

    When no structured items exist, CortexShift states that plainly rather than
    fabricating a completion percentage out of Git state or session exit codes.
    """
    fraction = progress.fraction
    if fraction is None:
        return Text("No structured progress yet", style="dim italic")

    filled = int(round(fraction * width))
    bar = Text()
    bar.append(PROGRESS_FILLED * filled, style="bold green")
    bar.append(PROGRESS_EMPTY * (width - filled), style="dim")
    bar.append(f"  {progress.completed_count}/{progress.total} · {progress.percent}%")
    return bar


class Panel(Vertical):
    """A titled content block used to group related dashboard facts.

    The heading rides the container's top border, so it always precedes the panel's
    content regardless of how children are composed. The authority label sits on the
    right of the same border, keeping "what this is" and "how much you can trust it"
    adjacent.
    """

    DEFAULT_CLASSES = "panel"

    def __init__(
        self,
        title: str,
        *,
        authority: DataAuthority | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self.border_title = title.upper()
        if authority is not None:
            self.border_subtitle = authority_label(authority)


class FieldList(Static):
    """A copy-friendly label/value list.

    Values render as plain selectable terminal text so identifiers stay inspectable and
    copyable, and wrap inside their own aligned column rather than running back under
    the labels.
    """

    def __init__(self, id: str | None = None, classes: str | None = None) -> None:
        super().__init__("", id=id, classes=f"field-list {classes}" if classes else "field-list")

    def set_fields(self, fields: Sequence[tuple[str, str | Text]]) -> None:
        """Replace the rendered fields."""
        if not fields:
            self.update(Text(""))
            return

        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="bold cyan", justify="left", no_wrap=True)
        grid.add_column(justify="left", overflow="fold")
        for label, value in fields:
            grid.add_row(label, value if isinstance(value, Text) else Text(str(value)))
        self.update(grid)


class BulletList(Static):
    """A bounded bullet list with an honest overflow notice."""

    def __init__(self, id: str | None = None, classes: str | None = None) -> None:
        super().__init__("", id=id, classes=f"bullets {classes}" if classes else "bullets")

    def set_items(
        self,
        items: Sequence[str],
        *,
        limit: int = 12,
        empty: str = "None recorded.",
        marker: str = "•",
    ) -> None:
        """Render up to `limit` items, stating how many were not shown."""
        if not items:
            self.update(Text(empty, style="dim italic"))
            return

        body = Text()
        for index, item in enumerate(items[:limit]):
            if index:
                body.append("\n")
            body.append(f"  {marker} ", style="dim")
            body.append(" ".join(item.split()))

        omitted = len(items) - limit
        if omitted > 0:
            body.append(
                f"\n  … {omitted} more item(s) not shown here; canonical state is complete.",
                style="dim italic",
            )
        self.update(body)


class EmptyState(Static):
    """A useful empty state that tells the operator what to do next."""

    DEFAULT_CLASSES = "empty-state"

    def __init__(self, message: str, hint: str | None = None, id: str | None = None) -> None:
        body = Text(message, style="bold")
        if hint:
            body.append("\n\n")
            body.append(hint, style="dim")
        super().__init__(body, id=id)


def restore_cursor(table: DataTable[Text], key: str | None) -> None:
    """Move a table's cursor back to a row key when that row still exists.

    Refreshing data must not throw the operator's selection around, so selection is
    preserved whenever the underlying row survives the refresh.
    """
    if key is None:
        return
    try:
        row_index = table.get_row_index(key)
    except Exception:
        return
    table.move_cursor(row=row_index, animate=False)


def current_row_key(table: DataTable[Text]) -> str | None:
    """Return the row key under the cursor, or None when the table is empty."""
    if table.row_count == 0:
        return None
    try:
        row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
    except Exception:
        return None
    return None if row_key.value is None else str(row_key.value)

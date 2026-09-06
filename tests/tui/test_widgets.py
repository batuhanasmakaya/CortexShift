"""Presentation widgets: glyphs carry meaning, and bounds are stated honestly."""

from cortexshift.tui.models import DataAuthority, TuiTaskProgress
from cortexshift.tui.widgets import (
    MARK_FAIL,
    MARK_OK,
    MARK_WARN,
    authority_label,
    marked,
    render_progress_bar,
    status_marker,
)


def test_status_meaning_never_depends_on_colour_alone() -> None:
    """On a limited-colour terminal the glyph still says what happened."""
    assert status_marker("available")[0] == MARK_OK
    assert status_marker("dirty")[0] == MARK_WARN
    assert status_marker("failed")[0] == MARK_FAIL

    for state in ("available", "dirty", "failed", "running", "anything-else"):
        glyph, _ = status_marker(state)
        assert glyph.strip(), f"{state} rendered without a glyph"
        assert marked(state, state).plain.startswith(f"{glyph} ")


def test_unknown_states_are_marked_unknown_rather_than_guessed() -> None:
    glyph, css = status_marker("something CortexShift has never seen")
    assert glyph == "?"
    assert css == "state-muted"


def test_progress_states_absence_instead_of_inventing_a_percentage() -> None:
    empty = render_progress_bar(TuiTaskProgress())
    assert empty.plain == "No structured progress yet"
    assert "%" not in empty.plain


def test_progress_renders_the_canonical_ratio() -> None:
    bar = render_progress_bar(TuiTaskProgress(completed_count=7, remaining_count=3), width=10)
    assert "7/10" in bar.plain
    assert "70%" in bar.plain
    assert bar.plain.startswith("█" * 7 + "░" * 3)


def test_a_fully_complete_task_renders_a_full_bar() -> None:
    bar = render_progress_bar(TuiTaskProgress(completed_count=4, remaining_count=0), width=8)
    assert "4/4" in bar.plain
    assert "100%" in bar.plain
    assert "░" not in bar.plain


def test_authority_labels_are_explicit() -> None:
    assert authority_label(DataAuthority.LIVE) == "Live"
    assert authority_label(DataAuthority.HISTORICAL) == "Historical observation"
    assert authority_label(DataAuthority.REPORTED) == "Reported / unverified"
    assert authority_label(DataAuthority.LAST_KNOWN) == "Last-known"

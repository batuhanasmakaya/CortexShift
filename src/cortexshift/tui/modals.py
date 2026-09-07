"""Modal dialogs for intentional, confirmed dashboard actions.

Every modal here is inert until explicitly confirmed: opening one never mutates state,
never launches a provider, and never consumes model quota. Each returns a plain value to
the app, which then calls the appropriate application service.
"""

from dataclasses import dataclass

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, OptionList, Static
from textual.widgets.option_list import Option

from cortexshift.domain.checkpoint import (
    MAX_DECISION_CHARS,
    MAX_OPERATOR_NOTE_CHARS,
    MAX_TEST_SUMMARY_CHARS,
)
from cortexshift.domain.task import MAX_CURRENT_WORK_CHARS, MAX_ITEM_CHARS
from cortexshift.tui.actions import TuiExitAction
from cortexshift.tui.models import TuiHandoffPreview, TuiRecoveryPreview, TuiSwitchPreview


@dataclass(frozen=True, slots=True)
class CurrentWorkInput:
    """A confirmed current-work entry.

    `value=None` means the operator deliberately cleared the entry. Cancelling the dialog
    returns nothing at all, so a cancel can never be mistaken for a clear.
    """

    value: str | None


@dataclass(frozen=True, slots=True)
class CheckpointInput:
    """Operator-supplied content for a MANUAL checkpoint."""

    decision: str | None
    test_summary: str | None
    note: str | None


@dataclass(frozen=True, slots=True)
class ProviderActionOption:
    """One concrete, pre-validated provider action offered to the operator."""

    action: TuiExitAction
    provider: str
    label: str
    detail: str
    selected_session_id: str | None = None
    force_new_session: bool = False
    enabled: bool = True
    disabled_reason: str | None = None

    @property
    def key(self) -> str:
        """Stable option identifier."""
        suffix = "new" if self.force_new_session else (self.selected_session_id or "default")
        return f"{self.action.value}:{self.provider}:{suffix}"


class _ModalBase(ModalScreen[object]):
    """Shared chrome and dismissal behaviour for CortexShift modals.

    Modals declare their initial focus with `AUTO_FOCUS` rather than querying for a widget
    in `on_mount`. A screen receives `Mount` before its `compose` has finished mounting the
    subtree, so `query_one` there is a race: on a slow machine the widget does not exist
    yet and opening the dialog raises `NoMatches` instead of showing it. Textual applies
    `AUTO_FOCUS` once the screen is composed, which is the same intent without the race.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
    ]

    def action_cancel(self) -> None:
        """Dismiss without performing the action."""
        self.dismiss(None)


class TextEntryModal(_ModalBase):
    """A single-field text entry dialog with explicit confirmation.

    Empty input is rejected unless the dialog explicitly allows clearing a value.
    """

    AUTO_FOCUS = "#entry-input"

    def __init__(
        self,
        title: str,
        *,
        label: str,
        placeholder: str = "",
        initial: str = "",
        help_text: str = "",
        max_length: int = MAX_ITEM_CHARS,
        allow_empty: bool = False,
        confirm_label: str = "Save",
    ) -> None:
        super().__init__()
        self._title = title
        self._label = label
        self._placeholder = placeholder
        self._initial = initial
        self._help_text = help_text
        self._max_length = max_length
        self._allow_empty = allow_empty
        self._confirm_label = confirm_label

    def compose(self) -> ComposeResult:
        """Build the entry dialog."""
        with Vertical(classes="modal"):
            yield Static(Text(self._title, style="bold"), classes="modal-title")
            if self._help_text:
                yield Static(Text(self._help_text, style="dim"), classes="modal-help")
            yield Static(Text(self._label), classes="field-label")
            yield Input(
                value=self._initial,
                placeholder=self._placeholder,
                max_length=self._max_length,
                id="entry-input",
            )
            yield Static("", id="entry-error", classes="modal-error")
            with Horizontal(classes="modal-buttons"):
                yield Button("Cancel", id="entry-cancel")
                yield Button(self._confirm_label, variant="primary", id="entry-confirm")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Confirm on Enter."""
        event.stop()
        self._confirm()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Confirm or cancel from the button row."""
        event.stop()
        if event.button.id == "entry-confirm":
            self._confirm()
        else:
            self.dismiss(None)

    def _confirm(self) -> None:
        value = self.query_one("#entry-input", Input).value.strip()
        if not value and not self._allow_empty:
            self.query_one("#entry-error", Static).update(
                Text("A value is required.", style="bold red")
            )
            return
        if len(value) > self._max_length:
            self.query_one("#entry-error", Static).update(
                Text(
                    f"Value exceeds the maximum of {self._max_length} characters.",
                    style="bold red",
                )
            )
            return
        self.dismiss(self.build_result(value))

    def build_result(self, value: str) -> object:
        """Convert confirmed text into the value handed back to the app."""
        return value


class SetCurrentWorkModal(TextEntryModal):
    """Replace the active Task's in-flight work description."""

    def __init__(self, initial: str = "") -> None:
        super().__init__(
            "Set current work",
            label="What is being worked on right now?",
            placeholder="Wiring the token refresh path",
            initial=initial,
            help_text="Confirm with an empty field to clear the current work entry.",
            max_length=MAX_CURRENT_WORK_CHARS,
            allow_empty=True,
        )

    def build_result(self, value: str) -> object:
        """Wrap the entry so a deliberate clear is distinct from a cancel."""
        return CurrentWorkInput(value or None)


class AddRemainingModal(TextEntryModal):
    """Add a newly discovered remaining item to the active Task."""

    def __init__(self) -> None:
        super().__init__(
            "Add remaining item",
            label="Remaining work item",
            placeholder="Wire token refresh into the CLI",
            help_text="Items are appended to the canonical Task and deduplicated.",
            confirm_label="Add",
        )


class RecordIssueModal(TextEntryModal):
    """Record a known issue or blocker on the active Task."""

    def __init__(self) -> None:
        super().__init__(
            "Record issue",
            label="Known issue or blocker",
            placeholder="Refresh tokens expire earlier than documented",
            help_text="Issues are appended to the canonical Task and deduplicated.",
            confirm_label="Record",
        )


class MarkCompletedModal(_ModalBase):
    """Mark one item completed on the active Task.

    Uses the canonical rule: the item is appended to completed (deduplicated) and any
    exactly matching remaining entry is removed. The Task itself is never completed.
    """

    AUTO_FOCUS = "#entry-input"

    def __init__(self, remaining: tuple[str, ...]) -> None:
        super().__init__()
        self._remaining = remaining

    def compose(self) -> ComposeResult:
        """Build the mark-completed dialog."""
        with Vertical(classes="modal"):
            yield Static(Text("Mark completed", style="bold"), classes="modal-title")
            yield Static(
                Text(
                    "Marks one item as completed and removes it from remaining. "
                    "This never completes the Task itself.",
                    style="dim",
                ),
                classes="modal-help",
            )
            if self._remaining:
                yield Static(Text("Remaining items"), classes="field-label")
                # Built with its options rather than filled in `on_mount`, so the list is
                # complete the moment it exists.
                yield OptionList(
                    *[Option(item, id=str(index)) for index, item in enumerate(self._remaining)],
                    id="completed-options",
                )
            yield Static(Text("Item to mark completed"), classes="field-label")
            yield Input(
                value=self._remaining[0] if self._remaining else "",
                placeholder="Implement parser",
                max_length=MAX_ITEM_CHARS,
                id="entry-input",
            )
            yield Static("", id="entry-error", classes="modal-error")
            with Horizontal(classes="modal-buttons"):
                yield Button("Cancel", id="entry-cancel")
                yield Button("Mark completed", variant="primary", id="entry-confirm")

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        """Mirror the highlighted remaining item into the editable input."""
        event.stop()
        self.query_one("#entry-input", Input).value = self._remaining[event.option_index]

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Selecting an option fills the input; confirmation is still explicit."""
        event.stop()
        entry = self.query_one("#entry-input", Input)
        entry.value = self._remaining[event.option_index]
        entry.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Confirm on Enter."""
        event.stop()
        self._confirm()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Confirm or cancel from the button row."""
        event.stop()
        if event.button.id == "entry-confirm":
            self._confirm()
        else:
            self.dismiss(None)

    def _confirm(self) -> None:
        value = self.query_one("#entry-input", Input).value.strip()
        if not value:
            self.query_one("#entry-error", Static).update(
                Text("Select or type an item to mark completed.", style="bold red")
            )
            return
        self.dismiss(value)


class CheckpointModal(_ModalBase):
    """Capture a cooperative MANUAL checkpoint."""

    AUTO_FOCUS = "#checkpoint-decision"

    def compose(self) -> ComposeResult:
        """Build the checkpoint dialog."""
        with Vertical(classes="modal"):
            yield Static(Text("Create checkpoint", style="bold"), classes="modal-title")
            yield Static(
                Text(
                    "Captures canonical Task state and a live repository observation. "
                    "No workspace lease is taken and no provider is contacted.",
                    style="dim",
                ),
                classes="modal-help",
            )
            yield Static(Text("Decision (optional)"), classes="field-label")
            yield Input(
                placeholder="Chose exact native resume over transcript replay",
                max_length=MAX_DECISION_CHARS,
                id="checkpoint-decision",
            )
            yield Static(Text("Test summary (optional)"), classes="field-label")
            yield Input(
                placeholder="572 passed",
                max_length=MAX_TEST_SUMMARY_CHARS,
                id="checkpoint-tests",
            )
            yield Static(
                Text(
                    "Recorded as Reported / unverified. CortexShift never converts a "
                    "claim into a verified result.",
                    style="dim italic",
                ),
            )
            yield Static(Text("Operator note (optional)"), classes="field-label")
            yield Input(
                placeholder="Stopping before the refactor lands",
                max_length=MAX_OPERATOR_NOTE_CHARS,
                id="checkpoint-note",
            )
            yield Static("", id="checkpoint-error", classes="modal-error")
            with Horizontal(classes="modal-buttons"):
                yield Button("Cancel", id="checkpoint-cancel")
                yield Button("Create checkpoint", variant="primary", id="checkpoint-confirm")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Confirm on Enter from any field."""
        event.stop()
        self._confirm()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Confirm or cancel from the button row."""
        event.stop()
        if event.button.id == "checkpoint-confirm":
            self._confirm()
        else:
            self.dismiss(None)

    def _confirm(self) -> None:
        decision = self.query_one("#checkpoint-decision", Input).value.strip()
        tests = self.query_one("#checkpoint-tests", Input).value.strip()
        note = self.query_one("#checkpoint-note", Input).value.strip()
        self.dismiss(
            CheckpointInput(
                decision=decision or None,
                test_summary=tests or None,
                note=note or None,
            )
        )


class ConfirmModal(_ModalBase):
    """A generic confirmation dialog rendering a prepared summary."""

    AUTO_FOCUS = "#confirm-cancel"

    def __init__(
        self,
        title: str,
        body: Text | str,
        *,
        confirm_label: str = "Confirm",
        confirm_variant: str = "primary",
    ) -> None:
        super().__init__()
        self._title = title
        self._body = body
        self._confirm_label = confirm_label
        self._confirm_variant = confirm_variant

    def compose(self) -> ComposeResult:
        """Build the confirmation dialog."""
        with Vertical(classes="modal"):
            yield Static(Text(self._title, style="bold"), classes="modal-title")
            with VerticalScroll(classes="modal-body"):
                yield Static(self._body, id="confirm-body")
            with Horizontal(classes="modal-buttons"):
                yield Button("Cancel", id="confirm-cancel")
                yield Button(
                    self._confirm_label,
                    variant=self._confirm_variant,  # type: ignore[arg-type]
                    id="confirm-ok",
                )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Return the operator's decision."""
        event.stop()
        self.dismiss(event.button.id == "confirm-ok")


class InfoModal(_ModalBase):
    """A read-only panel for bounded previews and error detail."""

    AUTO_FOCUS = "#info-close"

    BINDINGS = [
        Binding("escape", "cancel", "Close", show=True),
        Binding("q", "cancel", "Close", show=False),
    ]

    def __init__(self, title: str, body: Text | str) -> None:
        super().__init__()
        self._title = title
        self._body = body

    def compose(self) -> ComposeResult:
        """Build the information panel."""
        with Vertical(classes="modal"):
            yield Static(Text(self._title, style="bold"), classes="modal-title")
            with VerticalScroll(classes="modal-body"):
                yield Static(self._body, id="info-body")
            with Horizontal(classes="modal-buttons"):
                yield Button("Close", variant="primary", id="info-close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Close the panel."""
        event.stop()
        self.dismiss(None)


class ProviderActionModal(_ModalBase):
    """Choose a provider action.

    Only actions that are currently valid can be selected. Invalid ones remain visible
    but disabled with the reason stated, so the operator learns why.
    """

    AUTO_FOCUS = "#provider-actions"

    def __init__(self, options: tuple[ProviderActionOption, ...]) -> None:
        super().__init__()
        self._options = options

    def compose(self) -> ComposeResult:
        """Build the provider action palette."""
        with Vertical(classes="modal"):
            yield Static(Text("Provider action", style="bold"), classes="modal-title")
            yield Static(
                Text(
                    "CortexShift closes this dashboard and restores the terminal before "
                    "the native provider starts. Provider TUIs are never embedded.",
                    style="dim",
                ),
                classes="modal-help",
            )
            # Built with its options rather than filled in `on_mount`, so the palette
            # is complete the moment it exists.
            yield OptionList(*self._option_rows(), id="provider-actions")
            with Horizontal(classes="modal-buttons"):
                yield Button("Cancel", id="provider-cancel")

    def _option_rows(self) -> list[Option]:
        """Render each provider action, stating why a disabled one is unavailable."""
        rows: list[Option] = []
        for index, option in enumerate(self._options):
            prompt = Text()
            prompt.append(option.label, style="bold" if option.enabled else "dim")
            detail = option.detail if option.enabled else (option.disabled_reason or "unavailable")
            prompt.append(f"\n    {detail}", style="dim")
            rows.append(Option(prompt, id=str(index), disabled=not option.enabled))
        return rows

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Return the chosen provider action."""
        event.stop()
        self.dismiss(self._options[event.option_index])

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Cancel without choosing an action."""
        event.stop()
        self.dismiss(None)


def render_switch_preview(preview: TuiSwitchPreview) -> Text:
    """Render a switch confirmation summary, built without any model call."""
    body = Text()
    rows: list[tuple[str, str]] = [
        ("From", f"{preview.source_provider_id} ({preview.source_session_id})"),
        ("Target", f"{preview.target_provider_name} ({preview.target_provider_id})"),
        ("Task", f"{preview.task_title} ({preview.task_id})"),
        ("Target native mode", preview.target_native_mode),
        (
            "Prior target session",
            preview.selected_prior_target_session_id or "— (new native conversation)",
        ),
        (
            "Git state",
            f"{preview.git_status} · {preview.git_branch or '(detached)'} · "
            f"{'dirty' if preview.git_dirty else 'clean'}",
        ),
        ("Checkpoint enrichment", preview.checkpoint_enrichment),
        ("Delivery", preview.delivery_strategy),
        (
            "Bootstrap model turn",
            "yes — one read-only planning turn will run"
            if preview.bootstrap_model_turn_required
            else "no",
        ),
        (
            "Context size",
            f"{preview.context_characters} / {preview.context_max_characters} characters"
            + (" (truncated)" if preview.context_truncated else ""),
        ),
    ]
    for index, (label, value) in enumerate(rows):
        if index:
            body.append("\n")
        body.append(f"{label:<24}", style="bold cyan")
        body.append(value)

    body.append(
        "\n\nNothing has been persisted or launched yet. Confirming closes the dashboard, "
        "restores the terminal, and then performs the switch.",
        style="dim italic",
    )
    return body


def render_recovery_preview(preview: TuiRecoveryPreview) -> Text:
    """Render a recovery confirmation summary from a non-mutating dry run."""
    body = Text()
    body.append("Task", style="bold cyan")
    body.append(f"        {preview.task_title} ({preview.task_id})\n")
    body.append("Repository", style="bold cyan")
    body.append(
        f"  {preview.repository_status} · "
        f"{'dirty' if preview.dirty else 'clean'} · "
        f"{len(preview.files_touched)} changed file(s)\n\n"
    )

    if preview.stale_count == 0:
        body.append("No unfinalized sessions were found. Recovery would change nothing.")
        return body

    body.append(
        f"{preview.stale_count} unfinalized session(s) would be reconciled:\n", style="bold"
    )
    for session_id in preview.stale_session_ids:
        body.append(f"  • {session_id}\n")

    body.append(
        "\nEach is recorded as status=interrupted with "
        "exit_reason=unexpected_termination and a reconciled_at timestamp. "
        "CortexShift never fabricates an unobserved process end time, so ended_at stays "
        "empty. A RECOVERY checkpoint is captured from live repository state.\n\n",
        style="dim",
    )
    body.append(
        "Recovery requires the exclusive workspace lease. If another agent currently "
        "owns the workspace, it will be refused safely and nothing will change.",
        style="dim italic",
    )
    return body


def render_handoff_preview(preview: TuiHandoffPreview) -> Text:
    """Render a bounded canonical handoff preview."""
    body = Text()
    body.append(f"Target      {preview.target_provider_name}\n", style="bold cyan")
    body.append(f"Delivery    {preview.delivery_strategy}\n")
    body.append(
        "Bootstrap   "
        + (
            "one read-only planning turn on switch\n"
            if preview.bootstrap_model_turn_required
            else "none\n"
        )
    )
    body.append(
        f"Context     {preview.context_characters} / {preview.context_max_characters} characters"
        + (" (truncated)\n" if preview.context_truncated else "\n")
    )
    body.append(
        "\nPreview only — nothing was persisted and no model quota was used.\n\n",
        style="dim italic",
    )
    body.append(preview.rendered_context)
    return body

"""The Help modal: the keyboard contract, in one place."""

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static

HELP_SECTIONS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    (
        "Navigation",
        (
            ("1", "Overview"),
            ("2", "Task"),
            ("3", "Repository"),
            ("4", "Sessions"),
            ("5", "Checkpoints"),
            ("6", "Handoffs"),
            ("7", "Providers"),
            ("tab / shift+tab", "Move focus between widgets"),
            ("↑ ↓ / page up / page down", "Move within tables and lists"),
        ),
    ),
    (
        "Refresh",
        (
            ("r", "Refresh everything, including live Git and provider discovery"),
            ("", "Persisted state also refreshes on a lightweight timer (~2s)"),
            ("", "Git runs on entry to Repository and on explicit refresh only"),
        ),
    ),
    (
        "Task",
        (
            ("w", "Set current work"),
            ("m", "Mark a remaining item completed"),
            ("n", "Add a remaining item"),
            ("i", "Record a known issue"),
            ("a / enter", "Activate the selected task (Task screen)"),
        ),
    ),
    (
        "Checkpoints & recovery",
        (
            ("c", "Create a MANUAL checkpoint (no workspace lease held)"),
            ("shift+r", "Review and run crash recovery"),
        ),
    ),
    (
        "Providers",
        (
            ("x", "Provider action palette — run, resume, or switch"),
            ("p", "Preview a canonical handoff (persists nothing, no model call)"),
            ("g", "Configure workspace MCP for Antigravity"),
        ),
    ),
    (
        "General",
        (
            ("ctrl+p", "Command palette"),
            ("?", "This help"),
            ("q", "Quit"),
        ),
    ),
)

TERMINAL_NOTE = (
    "Provider-native TUIs are never embedded. When you choose run, resume, or switch, "
    "CortexShift closes this dashboard, restores the terminal, and only then launches "
    "Claude Code, Codex, or Antigravity so the agent owns the terminal directly."
)


class HelpScreen(ModalScreen[None]):
    """A concise reference for the dashboard's keyboard contract."""

    BINDINGS = [
        Binding("escape", "dismiss_help", "Close", show=True),
        Binding("question_mark", "dismiss_help", "Close", show=False),
        Binding("q", "dismiss_help", "Close", show=False),
    ]

    def compose(self) -> ComposeResult:
        """Build the help modal."""
        with Vertical(classes="modal", id="help-modal"):
            yield Static(
                Text("CortexShift — Keyboard Contract", style="bold"),
                classes="modal-title",
            )
            with VerticalScroll(classes="modal-body"):
                yield Static(self._render_body(), id="help-body")
            yield Static(Text(TERMINAL_NOTE, style="dim italic"))
            with Vertical(classes="modal-buttons"):
                yield Button("Close", variant="primary", id="help-close")

    @staticmethod
    def _render_body() -> Text:
        body = Text()
        for index, (title, entries) in enumerate(HELP_SECTIONS):
            if index:
                body.append("\n\n")
            body.append(title.upper(), style="bold cyan")
            for key, description in entries:
                body.append("\n")
                body.append(f"  {key:<26}", style="bold")
                body.append(description)
        return body

    def action_dismiss_help(self) -> None:
        """Close the help modal."""
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Close on button activation."""
        event.stop()
        self.dismiss(None)

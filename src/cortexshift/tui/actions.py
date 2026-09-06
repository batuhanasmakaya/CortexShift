"""The structured boundary between the dashboard and native provider launch.

CortexShift never embeds a provider's terminal UI. When the operator chooses to run,
resume, or switch, the Textual application exits and *returns* a `TuiExitRequest`. Only
after `App.run()` has returned — and Textual has restored the terminal — does the
coordinator invoke `RunService`, `ResumeService`, or `SwitchService`, handing raw terminal
ownership to the native provider process.

This module deliberately depends on nothing from Textual so the contract can be
constructed, asserted, and dispatched without a running application.
"""

from dataclasses import dataclass
from enum import StrEnum


class TuiExitAction(StrEnum):
    """A provider action the dashboard defers to the terminal coordinator."""

    RUN = "run"
    RESUME = "resume"
    SWITCH = "switch"


@dataclass(frozen=True, slots=True)
class TuiExitRequest:
    """A request to launch a native provider after the dashboard has exited.

    Constructing one launches nothing. It is a value returned out of the Textual
    application, describing what the coordinator should do once the terminal is free.
    """

    action: TuiExitAction
    provider: str
    selected_session_id: str | None = None
    force_new_session: bool = False
    note: str | None = None

    def __post_init__(self) -> None:
        provider = self.provider.strip().lower()
        if not provider:
            raise ValueError("A provider identifier is required for a TUI exit request.")
        object.__setattr__(self, "provider", provider)

        if self.force_new_session and self.selected_session_id is not None:
            raise ValueError("force_new_session and selected_session_id are mutually exclusive.")
        if self.action is TuiExitAction.RUN and self.selected_session_id is not None:
            raise ValueError("A run request never targets an existing session.")

    @property
    def description(self) -> str:
        """Human-readable summary used in coordinator output and confirmations."""
        verb = {
            TuiExitAction.RUN: "Run",
            TuiExitAction.RESUME: "Resume",
            TuiExitAction.SWITCH: "Switch to",
        }[self.action]
        return f"{verb} {self.provider}"

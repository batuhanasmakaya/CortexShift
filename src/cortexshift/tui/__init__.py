"""CortexShift interactive terminal control center (Textual).

The TUI is an adapter over the existing application services — the same services the CLI
and the MCP server use. It owns presentation only: no SQL, no SQLite handles, no Git
subprocesses, no provider argv construction, and no duplicated task, recovery, or handoff
rules.
"""

from cortexshift.tui.actions import TuiExitAction, TuiExitRequest
from cortexshift.tui.app import CortexShiftApp, build_app
from cortexshift.tui.coordinator import TuiCoordinator, TuiSession
from cortexshift.tui.facade import TuiFacade
from cortexshift.tui.screens import TuiSection

__all__ = [
    "CortexShiftApp",
    "TuiCoordinator",
    "TuiExitAction",
    "TuiExitRequest",
    "TuiFacade",
    "TuiSection",
    "TuiSession",
    "build_app",
]

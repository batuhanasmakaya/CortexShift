"""The CortexShift interactive terminal control center.

This is an adapter, not a backend. Every fact it renders and every action it performs is
produced by `TuiFacade` on top of the existing application services; the dashboard itself
contains no SQL, opens no database, runs no Git command, and builds no provider argv.

Two rules shape its runtime behaviour:

1. **The dashboard never holds the workspace lease.** Observing and editing task state
   must never block a coding agent, so the lease is only taken inside the services that
   genuinely require exclusivity (run, resume, switch, recover).
2. **The terminal is released before a provider starts.** Choosing run, resume, or switch
   exits the Textual application with a `TuiExitRequest`. Only after `App.run()` has
   returned does the coordinator launch the native provider, which then owns the terminal
   directly. No provider TUI is ever embedded, scraped, or multiplexed.
"""

import traceback
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, cast

from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.command import DiscoveryHit, Hit, Hits, Provider
from textual.containers import Horizontal, Vertical
from textual.widgets import ContentSwitcher, DataTable, Footer, Header, OptionList, Static
from textual.widgets.option_list import Option
from textual.worker import WorkerState

from cortexshift.domain.checkpoint import CheckpointRecord
from cortexshift.domain.errors import CortexShiftError
from cortexshift.domain.task import Task
from cortexshift.tui.actions import TuiExitAction, TuiExitRequest
from cortexshift.tui.facade import TuiFacade
from cortexshift.tui.modals import (
    AddRemainingModal,
    CheckpointInput,
    CheckpointModal,
    ConfirmModal,
    CurrentWorkInput,
    InfoModal,
    MarkCompletedModal,
    ProviderActionModal,
    ProviderActionOption,
    RecordIssueModal,
    SetCurrentWorkModal,
    render_handoff_preview,
    render_recovery_preview,
    render_switch_preview,
)
from cortexshift.tui.models import (
    TuiHandoffPreview,
    TuiMcpStatus,
    TuiProviderStatus,
    TuiRecoveryPreview,
    TuiRepositoryModel,
    TuiStateSnapshot,
    TuiSwitchPreview,
    WorkspaceActivity,
    format_relative,
)
from cortexshift.tui.screens import SECTION_ORDER, SectionView, TuiSection
from cortexshift.tui.screens.checkpoints import CheckpointsSection
from cortexshift.tui.screens.handoffs import HandoffsSection
from cortexshift.tui.screens.help import HelpScreen
from cortexshift.tui.screens.overview import OverviewSection
from cortexshift.tui.screens.providers import ProvidersSection
from cortexshift.tui.screens.repository import RepositorySection
from cortexshift.tui.screens.sessions import SessionsSection
from cortexshift.tui.screens.task import TaskSection

STATE_REFRESH_SECONDS = 2.0
# The worker group the lightweight state reload runs in. Named so the timer can ask
# whether a reload is already in flight rather than starting one on top of it.
STATE_WORKER_GROUP = "cortexshift-state"
COMPACT_WIDTH = 90
MINIMUM_WIDTH = 60
MINIMUM_HEIGHT = 12


@dataclass(frozen=True)
class ServiceCall:
    """A service invocation dispatched to a worker thread.

    Application services perform file, subprocess, and database I/O; running them on the
    UI thread would stall rendering, so every one of them is routed through here.
    """

    run: Callable[[], object]
    complete: Callable[[object], None]
    failure_title: str


class CortexShiftCommands(Provider):
    """Semantic actions exposed through Textual's built-in command palette."""

    @property
    def _app(self) -> "CortexShiftApp":
        return cast("CortexShiftApp", self.app)

    def _commands(self) -> Iterable[tuple[str, str, Callable[[], None]]]:
        app = self._app
        for section in SECTION_ORDER:
            yield (
                f"Go to {section.label}",
                f"Show the {section.label} section ({section.shortcut})",
                _section_callback(app, section),
            )
        yield ("Refresh all", "Reload state, repository, and provider status", app.action_refresh)
        yield (
            "Refresh repository",
            "Run a live read-only Git inspection",
            app.refresh_repository,
        )
        yield ("Create checkpoint", "Capture a MANUAL checkpoint", app.action_checkpoint)
        yield ("Set current work", "Update the active task's in-flight work", app.action_set_work)
        yield ("Mark item completed", "Complete one remaining item", app.action_mark_completed)
        yield ("Add remaining item", "Record newly discovered work", app.action_add_remaining)
        yield ("Record issue", "Record a blocker on the active task", app.action_record_issue)
        yield (
            "Provider action",
            "Run, resume, or switch a native provider",
            app.action_provider_actions,
        )
        yield (
            "Preview handoff",
            "Render canonical handoff context without launching anything",
            app.action_preview_handoff,
        )
        yield (
            "Recover workspace",
            "Reconcile unfinalized sessions through RecoveryService",
            app.action_recover,
        )
        yield ("Help", "Show the keyboard contract", app.action_help)

    async def search(self, query: str) -> Hits:
        """Yield palette hits matching the operator's query."""
        matcher = self.matcher(query)
        for name, help_text, callback in self._commands():
            score = matcher.match(name)
            if score > 0:
                yield Hit(score, matcher.highlight(name), callback, help=help_text)

    async def discover(self) -> Hits:
        """Yield the full command list before the operator types anything."""
        for name, help_text, callback in self._commands():
            yield DiscoveryHit(name, callback, help=help_text)


def _section_callback(app: "CortexShiftApp", section: TuiSection) -> Callable[[], None]:
    """Bind a section switch for the command palette."""

    def callback() -> None:
        app.show_section(section)

    return callback


class CortexShiftApp(App[TuiExitRequest | None]):
    """The CortexShift dashboard."""

    CSS_PATH = "cortexshift.tcss"
    TITLE = "CortexShift"
    COMMANDS: ClassVar[set[type[Provider] | Callable[[], type[Provider]]]] = App.COMMANDS | {
        CortexShiftCommands
    }

    BINDINGS = [
        Binding("1", "section('overview')", "Overview", show=False),
        Binding("2", "section('task')", "Task", show=False),
        Binding("3", "section('repository')", "Repository", show=False),
        Binding("4", "section('sessions')", "Sessions", show=False),
        Binding("5", "section('checkpoints')", "Checkpoints", show=False),
        Binding("6", "section('handoffs')", "Handoffs", show=False),
        Binding("7", "section('providers')", "Providers", show=False),
        Binding("r", "refresh", "Refresh", show=True),
        Binding("c", "checkpoint", "Checkpoint", show=True),
        Binding("x", "provider_actions", "Provider", show=True),
        Binding("w", "set_work", "Set work", show=False),
        Binding("m", "mark_completed", "Complete", show=False),
        Binding("n", "add_remaining", "Add item", show=False),
        Binding("i", "record_issue", "Issue", show=False),
        Binding("a", "activate_task", "Activate", show=False),
        Binding("p", "preview_handoff", "Preview", show=False),
        Binding("g", "setup_antigravity_mcp", "MCP setup", show=False),
        Binding("R", "recover", "Recover", show=False),
        Binding("question_mark", "help", "Help", show=True),
        Binding("q", "quit", "Quit", show=True),
    ]

    def __init__(
        self,
        facade: TuiFacade,
        *,
        state_refresh_seconds: float = STATE_REFRESH_SECONDS,
        initial_section: TuiSection = TuiSection.OVERVIEW,
    ) -> None:
        super().__init__()
        self.facade = facade
        self._state_refresh_seconds = state_refresh_seconds
        self._initial_section = initial_section

        self._snapshot: TuiStateSnapshot | None = None
        self._repository: TuiRepositoryModel | None = None
        self._providers: tuple[TuiProviderStatus, ...] = ()
        self._mcp: TuiMcpStatus | None = None
        self._workspace_activity = WorkspaceActivity.UNKNOWN

        self._state_generation = 0
        self._last_state_error: str | None = None
        self._repository_generation = 0
        self._provider_generation = 0
        self._syncing_nav = False
        self._active_section = initial_section

    # ------------------------------------------------------------------
    # Composition
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        """Build the dashboard shell.

        The shell renders before any state loads, so startup never blocks on Git or
        provider probes.
        """
        yield Header(show_clock=False)
        with Horizontal(id="shell"):
            with Vertical(id="sidebar"):
                yield Static(Text("CortexShift", style="bold"), id="sidebar-title")
                yield OptionList(id="nav")
            with ContentSwitcher(id="content", initial=self._initial_section.value):
                yield OverviewSection(id=TuiSection.OVERVIEW.value)
                yield TaskSection(id=TuiSection.TASK.value)
                yield RepositorySection(id=TuiSection.REPOSITORY.value)
                yield SessionsSection(id=TuiSection.SESSIONS.value)
                yield CheckpointsSection(id=TuiSection.CHECKPOINTS.value)
                yield HandoffsSection(id=TuiSection.HANDOFFS.value)
                yield ProvidersSection(id=TuiSection.PROVIDERS.value)
        yield Static(
            Text(
                "This terminal is too small for the CortexShift dashboard.\n\n"
                "Resize to at least 60 x 12 (80 x 24 recommended) and it will return.",
                style="bold",
            ),
            id="too-small",
        )
        yield Static("", id="status-line")
        yield Footer()

    def on_mount(self) -> None:
        """Populate navigation, then load state in the background."""
        nav = self.query_one("#nav", OptionList)
        nav.add_options(
            [
                Option(f"{section.shortcut}  {section.label}", id=section.value)
                for section in SECTION_ORDER
            ]
        )
        self._sync_nav_highlight()
        self._render_status_line()

        self.refresh_state()
        self.refresh_repository()
        self.refresh_providers()
        self.set_interval(self._state_refresh_seconds, self._on_state_tick)

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    @property
    def active_section(self) -> TuiSection:
        """The section currently visible in the content area."""
        return self._active_section

    def show_section(self, section: TuiSection) -> None:
        """Switch to a primary section and let it react to becoming visible."""
        self._active_section = section
        self.query_one("#content", ContentSwitcher).current = section.value
        self._sync_nav_highlight()
        self._render_status_line()

        view = self.section_view(section)
        view.on_section_shown()

        # Live Git runs on screen entry and explicit refresh only — never on the
        # lightweight state timer, so large repositories are not hammered.
        if section is TuiSection.REPOSITORY:
            self.refresh_repository()
        elif section is TuiSection.PROVIDERS and not self._providers:
            self.refresh_providers()

    def section_view(self, section: TuiSection) -> SectionView:
        """Return the view object for a section."""
        return self.query_one(f"#{section.value}", SectionView)

    def action_section(self, section: str) -> None:
        """Jump to a primary section by identifier."""
        self.show_section(TuiSection(section))

    def _sync_nav_highlight(self) -> None:
        nav = self.query_one("#nav", OptionList)
        self._syncing_nav = True
        try:
            nav.highlighted = SECTION_ORDER.index(self._active_section)
        finally:
            self._syncing_nav = False

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        """Follow sidebar navigation with the keyboard."""
        if event.option_list.id != "nav" or self._syncing_nav:
            return
        event.stop()
        section = SECTION_ORDER[event.option_index]
        if section is not self._active_section:
            self.show_section(section)

    # ------------------------------------------------------------------
    # Refresh: lightweight persisted state
    # ------------------------------------------------------------------

    def _on_state_tick(self) -> None:
        """Lightweight timer tick.

        Reloads persisted state only. Another agent may be updating the canonical Task
        through MCP while this dashboard is open, and those updates must surface here.
        Git is never inspected on this path.

        A tick that arrives while the previous reload is still running is skipped rather
        than replacing it. The reload is exclusive, so starting another would cancel the
        one in flight and the generation guard would drop its result: on a machine where
        reading state takes longer than the interval, every tick would cancel the reload
        that was about to finish and the dashboard would silently stop updating. Skipping
        keeps the timer from starving the very refresh it exists to perform. An operator
        asking for a refresh still supersedes -- see `action_refresh`.
        """
        if self._state_refresh_running():
            return
        self.refresh_state()

    def _state_refresh_running(self) -> bool:
        """Whether a state reload is in flight right now."""
        return any(
            worker.group == STATE_WORKER_GROUP and worker.state is WorkerState.RUNNING
            for worker in self.workers
        )

    def refresh_state(self) -> None:
        """Reload persisted read models in an exclusive worker."""
        self._state_generation += 1
        self._load_state(self._state_generation)

    @work(exclusive=True, group=STATE_WORKER_GROUP, thread=True, exit_on_error=False)
    def _load_state(self, generation: int) -> None:
        try:
            snapshot = self.facade.load_state()
        except Exception as exc:
            self.call_from_thread(self._on_state_error, exc)
            return
        self.call_from_thread(self._apply_state, generation, snapshot)

    def _on_state_error(self, error: Exception) -> None:
        """Report a state refresh failure once, not on every tick.

        This path runs on a timer. If the database becomes unreadable the operator needs
        to be told, but repeating the same notification every couple of seconds would
        bury the dashboard rather than inform them.
        """
        signature = f"{type(error).__name__}: {error}"
        if signature == self._last_state_error:
            return
        self._last_state_error = signature
        self._report_error("State refresh failed", error)

    def _apply_state(self, generation: int, snapshot: TuiStateSnapshot) -> None:
        """Apply a state result, discarding any that a newer refresh has superseded."""
        self._last_state_error = None
        if generation != self._state_generation:
            return
        self._snapshot = snapshot
        for section in SECTION_ORDER:
            self.section_view(section).update_state(snapshot)
        self._render_status_line()

    # ------------------------------------------------------------------
    # Refresh: live repository (native Git, worker only)
    # ------------------------------------------------------------------

    def refresh_repository(self) -> None:
        """Run a live Git inspection in an exclusive worker.

        `exclusive=True` cancels an in-flight inspection, and the generation guard drops
        any late result, so rapid refreshes can never leave older data on screen.
        """
        self._repository_generation += 1
        self._inspect_repository(self._repository_generation)

    @work(exclusive=True, group="cortexshift-repository", thread=True, exit_on_error=False)
    def _inspect_repository(self, generation: int) -> None:
        try:
            repository = self.facade.inspect_repository()
        except Exception as exc:
            self.call_from_thread(self._report_error, "Repository inspection failed", exc)
            return
        self.call_from_thread(self._apply_repository, generation, repository)

    def _apply_repository(self, generation: int, repository: TuiRepositoryModel) -> None:
        """Apply a repository result unless a newer inspection has already started."""
        if generation != self._repository_generation:
            return
        self._repository = repository
        for section in SECTION_ORDER:
            self.section_view(section).update_repository(repository)
        self._render_status_line()

    # ------------------------------------------------------------------
    # Refresh: provider discovery and MCP status (worker only)
    # ------------------------------------------------------------------

    def refresh_providers(self, *, force: bool = False) -> None:
        """Probe provider CLIs and MCP integration in an exclusive worker."""
        self._provider_generation += 1
        self._discover_providers(self._provider_generation, force)

    @work(exclusive=True, group="cortexshift-providers", thread=True, exit_on_error=False)
    def _discover_providers(self, generation: int, force: bool) -> None:
        try:
            providers = self.facade.provider_status(refresh=force)
            mcp = self.facade.mcp_status()
            activity = self.facade.workspace_activity()
        except Exception as exc:
            self.call_from_thread(self._report_error, "Provider discovery failed", exc)
            return
        self.call_from_thread(self._apply_providers, generation, providers, mcp, activity)

    def _apply_providers(
        self,
        generation: int,
        providers: tuple[TuiProviderStatus, ...],
        mcp: TuiMcpStatus,
        activity: WorkspaceActivity,
    ) -> None:
        """Apply provider results unless a newer probe has already started."""
        if generation != self._provider_generation:
            return
        self._providers = providers
        self._mcp = mcp
        self._workspace_activity = activity
        for section in SECTION_ORDER:
            view = self.section_view(section)
            view.update_providers(providers, mcp)
            view.update_activity(activity)
        self._render_status_line()

    def action_refresh(self) -> None:
        """Refresh everything, including live Git and provider discovery."""
        self.refresh_state()
        self.refresh_repository()
        self.refresh_providers(force=True)
        self.notify("Refreshing state, repository, and providers…", timeout=2)

    # ------------------------------------------------------------------
    # Service dispatch
    # ------------------------------------------------------------------

    def dispatch(self, call: ServiceCall) -> None:
        """Run an application service call off the UI thread."""
        self._service_worker(call)

    @work(group="cortexshift-service", thread=True, exit_on_error=False)
    def _service_worker(self, call: ServiceCall) -> None:
        try:
            result = call.run()
        except Exception as exc:
            self.call_from_thread(self._report_error, call.failure_title, exc)
            return
        self.call_from_thread(call.complete, result)

    def _report_error(self, title: str, error: Exception) -> None:
        """Surface an error without taking the dashboard down.

        Expected CortexShift errors are reported in their own words. Anything else is
        reported as unexpected and logged in full — never silently swallowed.
        """
        if isinstance(error, CortexShiftError):
            self.notify(str(error), title=title, severity="error", timeout=8)
            return
        self.log.error(f"{title}: {error!r}\n{traceback.format_exc()}")
        self.notify(
            f"{type(error).__name__}: {error}",
            title=f"{title} (unexpected)",
            severity="error",
            timeout=10,
        )

    # ------------------------------------------------------------------
    # Task actions
    # ------------------------------------------------------------------

    def _require_active_task(self) -> bool:
        if self._snapshot is None or self._snapshot.active_task is None:
            self.notify(
                "No active CortexShift task. Activate one on the Task screen first.",
                title="No active task",
                severity="warning",
            )
            return False
        return True

    def action_set_work(self) -> None:
        """Open the set-current-work dialog for the active task."""
        if not self._require_active_task():
            return
        assert self._snapshot is not None and self._snapshot.active_task is not None
        initial = self._snapshot.active_task.current_work or ""

        def completed(value: object) -> None:
            # A cancel returns nothing; only a confirmed entry reaches the service, so
            # dismissing the dialog can never clear the operator's current work.
            if not isinstance(value, CurrentWorkInput):
                return
            entry = value.value
            message = "Current work updated." if entry else "Current work cleared."
            self.dispatch(
                ServiceCall(
                    run=lambda: self.facade.set_current_work(entry),
                    complete=self._on_task_mutated(message),
                    failure_title="Could not set current work",
                )
            )

        self.push_screen(SetCurrentWorkModal(initial), completed)

    def action_mark_completed(self) -> None:
        """Open the mark-completed dialog for the active task."""
        if not self._require_active_task():
            return
        assert self._snapshot is not None and self._snapshot.active_task is not None
        remaining = self._snapshot.active_task.remaining

        def completed(value: object) -> None:
            if value is None:
                return
            item = cast(str, value)
            self.dispatch(
                ServiceCall(
                    run=lambda: self.facade.mark_completed([item]),
                    complete=self._on_task_mutated(f"Marked completed: {item}"),
                    failure_title="Could not mark item completed",
                )
            )

        self.push_screen(MarkCompletedModal(remaining), completed)

    def action_add_remaining(self) -> None:
        """Open the add-remaining-item dialog for the active task."""
        if not self._require_active_task():
            return

        def completed(value: object) -> None:
            if value is None:
                return
            item = cast(str, value)
            self.dispatch(
                ServiceCall(
                    run=lambda: self.facade.add_remaining([item]),
                    complete=self._on_task_mutated(f"Added remaining item: {item}"),
                    failure_title="Could not add remaining item",
                )
            )

        self.push_screen(AddRemainingModal(), completed)

    def action_record_issue(self) -> None:
        """Open the record-issue dialog for the active task."""
        if not self._require_active_task():
            return

        def completed(value: object) -> None:
            if value is None:
                return
            item = cast(str, value)
            self.dispatch(
                ServiceCall(
                    run=lambda: self.facade.record_issues([item]),
                    complete=self._on_task_mutated(f"Recorded issue: {item}"),
                    failure_title="Could not record issue",
                )
            )

        self.push_screen(RecordIssueModal(), completed)

    def action_activate_task(self) -> None:
        """Activate the task selected on the Task screen."""
        if self._active_section is not TuiSection.TASK:
            self.show_section(TuiSection.TASK)
            return

        view = cast(TaskSection, self.section_view(TuiSection.TASK))
        row = view.selected_task
        if row is None:
            self.notify("No task is selected.", severity="warning")
            return
        if row.is_active:
            self.notify(f"{row.title} is already the active task.", timeout=3)
            return

        task_id = row.id
        self.dispatch(
            ServiceCall(
                run=lambda: self.facade.activate_task(task_id),
                complete=self._on_task_mutated(f"Activated: {row.title}"),
                failure_title="Could not activate task",
            )
        )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Enter on the Task table activates the selected task."""
        if event.data_table.id != "task-table":
            return
        event.stop()
        self.action_activate_task()

    def _on_task_mutated(self, message: str) -> Callable[[object], None]:
        """Build a completion callback that refreshes state after a task mutation."""

        def complete(result: object) -> None:
            task = cast(Task, result)
            self.notify(message, title=f"Task {task.id}", timeout=4)
            self.refresh_state()

        return complete

    # ------------------------------------------------------------------
    # Checkpoint
    # ------------------------------------------------------------------

    def action_checkpoint(self) -> None:
        """Open the checkpoint dialog and create a MANUAL checkpoint on confirmation."""
        if not self._require_active_task():
            return

        def completed(value: object) -> None:
            if value is None:
                return
            entry = cast(CheckpointInput, value)
            self.dispatch(
                ServiceCall(
                    run=lambda: self.facade.create_checkpoint(
                        decisions=[entry.decision] if entry.decision else None,
                        test_summary=entry.test_summary,
                        note=entry.note,
                    ),
                    complete=self._on_checkpoint_created,
                    failure_title="Could not create checkpoint",
                )
            )

        self.push_screen(CheckpointModal(), completed)

    def _on_checkpoint_created(self, result: object) -> None:
        record = cast(CheckpointRecord, result)
        suffix = (
            " Test summary recorded as reported / unverified."
            if record.payload.test_status.known
            else ""
        )
        self.notify(
            f"Created {record.kind.value} checkpoint {record.id}.{suffix}",
            title="Checkpoint",
            timeout=6,
        )
        self.refresh_state()

    # ------------------------------------------------------------------
    # Recovery
    # ------------------------------------------------------------------

    def action_recover(self) -> None:
        """Preview recovery, then reconcile through RecoveryService on confirmation."""
        self.dispatch(
            ServiceCall(
                run=self.facade.preview_recovery,
                complete=self._on_recovery_preview,
                failure_title="Could not preview recovery",
            )
        )

    def _on_recovery_preview(self, result: object) -> None:
        preview = cast(TuiRecoveryPreview, result)
        if preview.stale_count == 0:
            self.notify(
                "No unfinalized sessions were found. Nothing to recover.",
                title="Recovery",
                timeout=5,
            )
            return

        def confirmed(value: object) -> None:
            if value is not True:
                return
            self.dispatch(
                ServiceCall(
                    run=self.facade.recover,
                    complete=self._on_recovered,
                    failure_title="Recovery failed",
                )
            )

        self.push_screen(
            ConfirmModal(
                "Recover workspace",
                render_recovery_preview(preview),
                confirm_label="Recover",
                confirm_variant="warning",
            ),
            confirmed,
        )

    def _on_recovered(self, result: object) -> None:
        report = getattr(result, "reconciled_session_ids", [])
        checkpoint_id = getattr(result, "checkpoint_id", None)
        detail = f" Recovery checkpoint {checkpoint_id}." if checkpoint_id else ""
        self.notify(
            f"Reconciled {len(report)} session(s).{detail}",
            title="Recovery complete",
            timeout=8,
        )
        self.refresh_state()

    # ------------------------------------------------------------------
    # Handoff preview
    # ------------------------------------------------------------------

    def action_preview_handoff(self) -> None:
        """Preview the canonical handoff for a target provider, launching nothing."""
        options = tuple(
            ProviderActionOption(
                action=TuiExitAction.SWITCH,
                provider=provider,
                label=f"Preview handoff to {provider}",
                detail="Renders canonical context. Persists nothing, uses no model quota.",
            )
            for provider in self.facade.supported_providers()
        )
        if not options:
            self.notify("No providers are registered.", severity="warning")
            return

        def chosen(value: object) -> None:
            if value is None:
                return
            option = cast(ProviderActionOption, value)
            provider = option.provider
            self.dispatch(
                ServiceCall(
                    run=lambda: self.facade.preview_handoff(provider),
                    complete=self._on_handoff_preview,
                    failure_title="Could not preview handoff",
                )
            )

        self.push_screen(ProviderActionModal(options), chosen)

    def _on_handoff_preview(self, result: object) -> None:
        preview = cast(TuiHandoffPreview, result)
        self.push_screen(
            InfoModal(
                f"Handoff preview → {preview.target_provider_name}",
                render_handoff_preview(preview),
            )
        )

    # ------------------------------------------------------------------
    # Antigravity workspace MCP setup
    # ------------------------------------------------------------------

    def action_setup_antigravity_mcp(self) -> None:
        """Configure workspace MCP for Antigravity after explicit confirmation."""
        body = Text()
        path = self._mcp.antigravity_config_path if self._mcp else None
        body.append("CortexShift will add its MCP server entry to:\n\n", style="bold")
        body.append(f"  {path or '.agents/mcp_config.json'}\n\n")
        body.append(
            "Unrelated MCP servers and top-level keys are preserved. If a conflicting "
            "'cortexshift' entry already exists, the setup is refused rather than "
            "overwritten — CortexShift never forces a configuration silently.",
            style="dim",
        )

        def confirmed(value: object) -> None:
            if value is not True:
                return
            self.dispatch(
                ServiceCall(
                    run=lambda: self.facade.configure_antigravity_mcp(),
                    complete=self._on_antigravity_configured,
                    failure_title="Antigravity MCP setup failed",
                )
            )

        self.push_screen(
            ConfirmModal(
                "Configure CortexShift MCP for Antigravity",
                body,
                confirm_label="Configure",
            ),
            confirmed,
        )

    def _on_antigravity_configured(self, result: object) -> None:
        payload = cast(dict[str, object], result)
        self.notify(
            f"Antigravity workspace MCP {payload.get('action', 'updated')}.",
            title="MCP setup",
            timeout=6,
        )
        self.refresh_providers(force=True)

    # ------------------------------------------------------------------
    # Provider actions — the terminal handoff boundary
    # ------------------------------------------------------------------

    def action_provider_actions(self) -> None:
        """Open the provider action palette."""
        self.dispatch(
            ServiceCall(
                run=self._build_provider_options,
                complete=self._on_provider_options,
                failure_title="Could not list provider actions",
            )
        )

    def _build_provider_options(self) -> tuple[ProviderActionOption, ...]:
        """Compute currently valid provider actions.

        Runs on a worker thread: it resolves executables on PATH and reads session
        history through the facade.
        """
        snapshot = self._snapshot
        source_provider = (
            snapshot.activity.latest_session.provider_id
            if snapshot and snapshot.activity.latest_session
            else None
        )
        has_active_task = bool(snapshot and snapshot.active_task)

        options: list[ProviderActionOption] = []
        for provider in self.facade.supported_providers():
            installed = self.facade.provider_available(provider)
            resumable = self.facade.resumable_sessions(provider) if installed else ()

            options.append(
                ProviderActionOption(
                    action=TuiExitAction.RUN,
                    provider=provider,
                    label=f"Run {provider}",
                    detail="Start a new native session on the active task.",
                    enabled=installed and has_active_task,
                    disabled_reason=(
                        f"{provider} was not found in PATH."
                        if not installed
                        else "No active CortexShift task."
                    ),
                )
            )

            newest = resumable[0] if resumable else None
            options.append(
                ProviderActionOption(
                    action=TuiExitAction.RESUME,
                    provider=provider,
                    label=f"Resume {provider}",
                    detail=(
                        f"Resume native conversation from session {newest.short_id}."
                        if newest
                        else ""
                    ),
                    selected_session_id=newest.id if newest else None,
                    enabled=newest is not None,
                    disabled_reason=(
                        f"{provider} was not found in PATH."
                        if not installed
                        else "No exactly resumable native session is recorded for this task."
                    ),
                )
            )

            same_provider = source_provider == provider
            options.append(
                ProviderActionOption(
                    action=TuiExitAction.SWITCH,
                    provider=provider,
                    label=f"Switch to {provider}",
                    detail=(
                        "Hand the task over with a fresh canonical handoff, reusing a "
                        "known native conversation when one exists."
                    ),
                    enabled=installed and has_active_task and not same_provider,
                    disabled_reason=(
                        f"{provider} was not found in PATH."
                        if not installed
                        else (
                            "The latest session already belongs to this provider — "
                            "use Resume instead."
                            if same_provider
                            else "No active CortexShift task."
                        )
                    ),
                )
            )
        return tuple(options)

    def _on_provider_options(self, result: object) -> None:
        options = cast(tuple[ProviderActionOption, ...], result)
        if not options:
            self.notify("No provider actions are available.", severity="warning")
            return
        self.push_screen(ProviderActionModal(options), self._on_provider_action_chosen)

    def _on_provider_action_chosen(self, value: object) -> None:
        if value is None:
            return
        option = cast(ProviderActionOption, value)

        if option.action is TuiExitAction.SWITCH:
            provider = option.provider
            new_session = option.force_new_session
            self.dispatch(
                ServiceCall(
                    run=lambda: self.facade.preview_switch(provider, new_session=new_session),
                    complete=self._switch_confirmation(option),
                    failure_title="Could not prepare the switch",
                )
            )
            return

        self.request_provider_launch(
            TuiExitRequest(
                action=option.action,
                provider=option.provider,
                selected_session_id=option.selected_session_id,
                force_new_session=option.force_new_session,
            )
        )

    def _switch_confirmation(self, option: ProviderActionOption) -> Callable[[object], None]:
        """Confirm a switch against its dry run before releasing the terminal."""

        def complete(result: object) -> None:
            preview = cast(TuiSwitchPreview, result)

            def confirmed(value: object) -> None:
                if value is not True:
                    return
                self.request_provider_launch(
                    TuiExitRequest(
                        action=TuiExitAction.SWITCH,
                        provider=option.provider,
                        selected_session_id=option.selected_session_id,
                        force_new_session=option.force_new_session,
                    )
                )

            self.push_screen(
                ConfirmModal(
                    f"Switch to {preview.target_provider_name}",
                    render_switch_preview(preview),
                    confirm_label="Switch",
                ),
                confirmed,
            )

        return complete

    def request_provider_launch(self, request: TuiExitRequest) -> None:
        """Exit the dashboard so the coordinator can launch the native provider.

        Nothing is launched here. Textual tears down, restores the terminal, and returns
        this request from `App.run()`; only then does the coordinator call the run,
        resume, or switch service.
        """
        self.exit(request)

    # ------------------------------------------------------------------
    # Help, status line, responsive layout
    # ------------------------------------------------------------------

    def action_help(self) -> None:
        """Show the keyboard contract."""
        self.push_screen(HelpScreen())

    def _render_status_line(self) -> None:
        """Render the status line, shedding lower-priority facts on narrow terminals."""
        status = self.query_one("#status-line", Static)
        compact = self.size.width < COMPACT_WIDTH
        parts: list[str] = []

        if self._snapshot is not None:
            parts.append(self._snapshot.project.name)
        else:
            parts.append("Loading…")

        # The sidebar folds away in compact mode, so the section name moves here.
        if compact:
            parts.append(self._active_section.label)

        if self._repository is not None and self._repository.ready:
            branch = self._repository.branch or "(detached)"
            parts.append(f"{branch} · {'dirty' if self._repository.dirty else 'clean'}")
        elif self._repository is not None:
            parts.append(f"git {self._repository.status.value}")

        parts.append(f"workspace {self._workspace_activity.value}")

        if not compact and self._snapshot is not None:
            parts.append(f"updated {format_relative(self._snapshot.loaded_at)}")

        status.update(Text("  │  ".join(parts), style="dim"))

    def on_resize(self, event: events.Resize) -> None:
        """Adapt the layout to the terminal size.

        The dashboard stays usable at 80 x 24. Below 90 columns the sidebar folds away
        and navigation continues through the number keys; below the minimum size a clear
        message replaces the layout instead of a broken render.
        """
        width, height = event.size.width, event.size.height
        self.set_class(width < COMPACT_WIDTH, "compact")
        self.set_class(width < MINIMUM_WIDTH or height < MINIMUM_HEIGHT, "too-small")
        self._render_status_line()


def build_app(
    project_root: Path | str | None = None,
    *,
    facade: TuiFacade | None = None,
    state_refresh_seconds: float = STATE_REFRESH_SECONDS,
) -> CortexShiftApp:
    """Construct the dashboard bound to one initialized CortexShift project.

    Raises:
        ProjectNotInitializedError: If no initialized project root is found.
    """
    resolved = facade or TuiFacade.resolve(project_root)
    return CortexShiftApp(resolved, state_refresh_seconds=state_refresh_seconds)

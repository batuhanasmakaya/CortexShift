"""The Overview section: the default landing view of the control center."""

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Static

from cortexshift.tui.models import (
    DataAuthority,
    TuiMcpStatus,
    TuiProviderStatus,
    TuiRepositoryModel,
    TuiStateSnapshot,
    WorkspaceActivity,
    format_relative,
    truncate,
)
from cortexshift.tui.screens import SectionView, TuiSection
from cortexshift.tui.widgets import (
    BulletList,
    FieldList,
    Panel,
    marked,
    render_progress_bar,
)

MARKER_WARN = "!"


class OverviewSection(SectionView):
    """Project identity, active task progress, repository state, and recent activity."""

    section = TuiSection.OVERVIEW

    def compose(self) -> ComposeResult:
        """Build the overview layout."""
        yield Static(Text("Overview", style="bold"), classes="section-title")
        yield Static("", id="overview-recovery", classes="notice")

        with Horizontal(classes="columns"):
            with Vertical(classes="column"):
                with Panel("Project"):
                    yield FieldList(id="overview-project")
                with Panel("Active task"):
                    yield Static("", id="overview-task-title")
                    yield Static("", id="overview-progress")
                    yield FieldList(id="overview-task")
            with Vertical(classes="column"):
                with Panel("Repository", authority=DataAuthority.LIVE):
                    yield FieldList(id="overview-repository")
                with Panel("Activity"):
                    yield FieldList(id="overview-activity")
                with Panel("Providers"):
                    yield BulletList(id="overview-providers")

    # -- rendering ---------------------------------------------------------

    def update_state(self, snapshot: TuiStateSnapshot) -> None:
        """Render project, task, and activity facts from persisted state."""
        project = snapshot.project
        self.query_one("#overview-project", FieldList).set_fields(
            [
                ("Project", project.name),
                ("Project ID", project.short_id),
                ("CortexShift", project.cortexshift_version),
                ("Schema", f"v{project.schema_version}"),
            ]
        )

        self._render_task(snapshot)
        self._render_activity(snapshot)

    def _render_task(self, snapshot: TuiStateSnapshot) -> None:
        title = self.query_one("#overview-task-title", Static)
        progress = self.query_one("#overview-progress", Static)
        fields = self.query_one("#overview-task", FieldList)

        task = snapshot.active_task
        if task is None:
            title.update(Text("No active task.", style="bold"))
            progress.update(
                Text(
                    "Activate a task on the Task screen (2), or create one first.",
                    style="dim",
                )
            )
            fields.set_fields([])
            return

        heading = Text()
        heading.append(task.title, style="bold")
        heading.append(f"   {task.status}", style="dim")
        title.update(heading)
        progress.update(render_progress_bar(task.progress))
        fields.set_fields(
            [
                ("Task ID", task.short_id),
                ("Objective", truncate(task.objective, 70)),
                ("Current work", truncate(task.current_work, 70)),
                ("Completed", str(task.progress.completed_count)),
                ("Remaining", str(task.progress.remaining_count)),
                ("Known issues", str(task.progress.issue_count)),
            ]
        )

    def _render_activity(self, snapshot: TuiStateSnapshot) -> None:
        activity = snapshot.activity
        session = activity.latest_session
        checkpoint = activity.latest_checkpoint
        handoff = activity.latest_handoff

        session_value: str | Text = "No session recorded."
        if session is not None:
            session_value = Text()
            session_value.append(f"{session.provider_id} · ")
            session_value.append_text(marked(session.status, session.status))
            session_value.append(f" · {format_relative(session.started_at)}")

        checkpoint_value: str | Text = "No checkpoint recorded."
        if checkpoint is not None:
            checkpoint_value = f"{checkpoint.kind} · {format_relative(checkpoint.created_at)}"

        handoff_value: str | Text = "No handoff recorded."
        if handoff is not None:
            handoff_value = (
                f"{handoff.source_provider_id} → {handoff.target_provider_id} · "
                f"{handoff.status} · {format_relative(handoff.created_at)}"
            )

        self.query_one("#overview-activity", FieldList).set_fields(
            [
                ("Latest session", session_value),
                ("Latest checkpoint", checkpoint_value),
                ("Latest handoff", handoff_value),
            ]
        )

        notice = self.query_one("#overview-recovery", Static)
        if activity.recovery_may_be_required:
            count = activity.unfinalized_session_count
            plural = "" if count == 1 else "s"
            notice.update(
                Text(
                    f"{MARKER_WARN} {count} unfinalized CortexShift Session{plural} recorded. "
                    "Recovery may be required — press R to review.",
                    style="bold yellow",
                )
            )
        else:
            notice.update("")

    def update_repository(self, repository: TuiRepositoryModel | None) -> None:
        """Render the most recent live Git observation."""
        fields = self.query_one("#overview-repository", FieldList)
        if repository is None:
            fields.set_fields([("Git", Text("Inspecting…", style="dim italic"))])
            return

        if not repository.ready:
            fields.set_fields(
                [
                    ("Git", marked("warn", repository.status.value)),
                    ("Detail", truncate(repository.diagnostic, 60)),
                ]
            )
            return

        branch = repository.branch or "(detached HEAD)"
        state = "dirty" if repository.dirty else "clean"
        fields.set_fields(
            [
                ("Branch", branch),
                ("HEAD", repository.short_head),
                ("State", marked(state, state)),
                ("Changed files", str(repository.changed_file_count)),
                ("Observed", format_relative(repository.observed_at)),
            ]
        )

    def update_providers(
        self,
        providers: tuple[TuiProviderStatus, ...],
        mcp: TuiMcpStatus | None,
    ) -> None:
        """Render a compact provider availability list without account details."""
        widget = self.query_one("#overview-providers", BulletList)
        if not providers:
            widget.set_items([], empty="Probing providers…")
            return

        lines = [
            f"{provider.display_name:<14}{'available' if provider.available else 'unavailable'}"
            for provider in providers
        ]
        if mcp is not None and not mcp.antigravity_configured:
            lines.append("Antigravity workspace MCP is not configured (Providers · G)")
        widget.set_items(lines, limit=10)

    def update_activity(self, activity: WorkspaceActivity) -> None:
        """Workspace lease state is surfaced by the app's status line."""

"""Immutable presentation models for the CortexShift terminal control center.

These are read models: bounded, flattened projections assembled by `TuiFacade` from
canonical domain entities so screen code never has to understand persistence adapters,
Git inspection results, or provider probe internals. They are never persisted, never
serialized to a wire protocol, and never substitute for canonical domain objects.

Every model carries an explicit authority label (`DataAuthority`) so the dashboard can
state honestly whether the reader is looking at live truth, an immutable historical
observation, an unverified agent report, or a last-known value that may have gone stale.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from cortexshift.domain.checkpoint import CheckpointRecord
from cortexshift.domain.git import RepositoryInspectionStatus
from cortexshift.domain.handoff import HandoffRecord
from cortexshift.domain.task import Task


class DataAuthority(StrEnum):
    """How much authority a rendered value carries in the truth hierarchy.

    CortexShift never presents a historical observation as current reality, and never
    presents an agent's report as a verified result.
    """

    LIVE = "live"
    HISTORICAL = "historical"
    REPORTED = "reported"
    LAST_KNOWN = "last_known"
    UNKNOWN = "unknown"


AUTHORITY_LABELS: dict[DataAuthority, str] = {
    DataAuthority.LIVE: "Live",
    DataAuthority.HISTORICAL: "Historical observation",
    DataAuthority.REPORTED: "Reported / unverified",
    DataAuthority.LAST_KNOWN: "Last-known",
    DataAuthority.UNKNOWN: "Unknown",
}


class WorkspaceActivity(StrEnum):
    """Observed state of the exclusive project workspace lease.

    Derived strictly from a non-blocking OS advisory lock probe. The presence of the
    lock file on disk is never treated as evidence of an active lease.
    """

    FREE = "free"
    BUSY = "busy"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class TuiProjectModel:
    """Identity of the CortexShift project the dashboard is bound to."""

    project_id: str
    name: str
    root: Path
    state_file: str
    schema_version: int
    cortexshift_version: str

    @property
    def short_id(self) -> str:
        """Abbreviated project identifier for dense table and header rendering."""
        return abbreviate_id(self.project_id)


@dataclass(frozen=True, slots=True)
class TuiTaskProgress:
    """Structured completion counts for the active Task.

    A percentage is only meaningful when structured items exist. CortexShift never
    infers progress from Git state or from a provider process exit status.
    """

    completed_count: int = 0
    remaining_count: int = 0
    issue_count: int = 0

    @property
    def total(self) -> int:
        """Number of structured progress items forming the denominator."""
        return self.completed_count + self.remaining_count

    @property
    def has_denominator(self) -> bool:
        """Whether a meaningful completion ratio can be computed at all."""
        return self.total > 0

    @property
    def fraction(self) -> float | None:
        """Completion ratio in [0, 1], or None when no structured progress exists."""
        if not self.has_denominator:
            return None
        return self.completed_count / self.total

    @property
    def percent(self) -> int | None:
        """Completion percentage, or None when no structured progress exists."""
        fraction = self.fraction
        if fraction is None:
            return None
        return int(round(fraction * 100))


@dataclass(frozen=True, slots=True)
class TuiTaskModel:
    """Complete canonical Task state for the Task screen."""

    id: str
    title: str
    status: str
    objective: str
    requirements: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    completed: tuple[str, ...] = ()
    current_work: str | None = None
    remaining: tuple[str, ...] = ()
    known_issues: tuple[str, ...] = ()
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def short_id(self) -> str:
        """Abbreviated task identifier for dense rendering."""
        return abbreviate_id(self.id)

    @property
    def progress(self) -> TuiTaskProgress:
        """Structured progress counts derived from canonical task lists."""
        return TuiTaskProgress(
            completed_count=len(self.completed),
            remaining_count=len(self.remaining),
            issue_count=len(self.known_issues),
        )

    @classmethod
    def from_task(cls, task: Task) -> "TuiTaskModel":
        """Project a canonical Task into its read model."""
        return cls(
            id=task.id,
            title=task.title,
            status=task.status.value,
            objective=task.objective,
            requirements=tuple(task.requirements),
            constraints=tuple(task.constraints),
            completed=tuple(task.completed),
            current_work=task.current_work,
            remaining=tuple(task.remaining),
            known_issues=tuple(task.known_issues),
            created_at=task.created_at,
            updated_at=task.updated_at,
        )


@dataclass(frozen=True, slots=True)
class TuiTaskRow:
    """One row of the Task table."""

    id: str
    title: str
    status: str
    is_active: bool
    is_terminal: bool
    completed_count: int
    remaining_count: int
    updated_at: datetime | None = None

    @property
    def short_id(self) -> str:
        """Abbreviated task identifier for dense rendering."""
        return abbreviate_id(self.id)


@dataclass(frozen=True, slots=True)
class TuiRepositoryModel:
    """Result of a live, strictly read-only Git working tree inspection."""

    status: RepositoryInspectionStatus
    project_root: Path
    git_available: bool
    git_version: str | None = None
    git_root: str | None = None
    branch: str | None = None
    head_sha: str | None = None
    detached_head: bool = False
    dirty: bool = False
    staged_files: tuple[str, ...] = ()
    modified_files: tuple[str, ...] = ()
    untracked_files: tuple[str, ...] = ()
    conflicted_files: tuple[str, ...] = ()
    working_tree_diff_summary: str | None = None
    staged_diff_summary: str | None = None
    diagnostic: str | None = None
    observed_at: datetime | None = None

    @property
    def ready(self) -> bool:
        """Whether the repository was inspected successfully."""
        return self.status == RepositoryInspectionStatus.READY

    @property
    def changed_file_count(self) -> int:
        """Total number of distinct changed paths reported by the inspection."""
        return len(
            {
                *self.staged_files,
                *self.modified_files,
                *self.untracked_files,
                *self.conflicted_files,
            }
        )

    @property
    def short_head(self) -> str:
        """Abbreviated HEAD commit, or an em dash when no commit exists."""
        if not self.head_sha:
            return "—"
        return self.head_sha[:8]


@dataclass(frozen=True, slots=True)
class TuiSessionRow:
    """One CortexShift orchestration Session.

    Holds orchestration metadata only. Provider transcripts, prompts, reasoning, and
    terminal history are never read and never displayed.
    """

    id: str
    task_id: str
    provider_id: str
    status: str
    native_resumable: bool
    native_session_id: str | None = None
    resumed_from_session_id: str | None = None
    exit_reason: str | None = None
    exit_code: int | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    reconciled_at: datetime | None = None

    @property
    def short_id(self) -> str:
        """Abbreviated session identifier for dense rendering."""
        return abbreviate_id(self.id)

    @property
    def unfinalized(self) -> bool:
        """Whether this record was never finalized and may warrant recovery."""
        return self.status in ("initializing", "running")

    @property
    def authority(self) -> DataAuthority:
        """An unfinalized row is last-known state, not a live running process."""
        return DataAuthority.LAST_KNOWN if self.unfinalized else DataAuthority.HISTORICAL


@dataclass(frozen=True, slots=True)
class TuiCheckpointRow:
    """One immutable Checkpoint observation."""

    id: str
    kind: str
    session_id: str | None
    created_at: datetime
    git_summary: str
    test_reported: bool
    record: CheckpointRecord

    @property
    def short_id(self) -> str:
        """Abbreviated checkpoint identifier for dense rendering."""
        return abbreviate_id(self.id)


@dataclass(frozen=True, slots=True)
class TuiHandoffRow:
    """One canonical cross-provider Handoff record."""

    id: str
    source_provider_id: str
    target_provider_id: str
    status: str
    checkpoint_id: str | None
    created_at: datetime
    record: HandoffRecord

    @property
    def short_id(self) -> str:
        """Abbreviated handoff identifier for dense rendering."""
        return abbreviate_id(self.id)


@dataclass(frozen=True, slots=True)
class TuiProviderStatus:
    """Passive discovery result for one provider CLI.

    Account identifiers, credential paths, and token material are never collected.
    """

    provider_id: str
    display_name: str
    executable: str
    installed: bool
    version: str | None = None
    authentication: str = "unknown"
    supports_native_resume: bool = False
    supports_exact_resume: bool = False
    mcp_integration: str = "unknown"
    diagnostics: tuple[str, ...] = ()

    @property
    def available(self) -> bool:
        """Whether the provider CLI resolved on PATH."""
        return self.installed


@dataclass(frozen=True, slots=True)
class TuiMcpStatus:
    """CortexShift MCP server and provider integration status.

    Rendering this never starts an MCP server.
    """

    sdk_available: bool
    sdk_version: str
    transport: str = "stdio (local only)"
    claude_integration: str = "automatic per launch (--mcp-config)"
    codex_integration: str = "automatic per launch (-c overrides)"
    antigravity_configured: bool = False
    antigravity_config_path: str | None = None
    read_tools: tuple[str, ...] = ()
    write_tools: tuple[str, ...] = ()
    resources: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TuiActivityModel:
    """Most recent orchestration activity recorded for the project."""

    latest_session: TuiSessionRow | None = None
    latest_checkpoint: TuiCheckpointRow | None = None
    latest_handoff: TuiHandoffRow | None = None
    unfinalized_session_count: int = 0

    @property
    def recovery_may_be_required(self) -> bool:
        """Whether unfinalized session records exist that recovery could reconcile."""
        return self.unfinalized_session_count > 0


@dataclass(frozen=True, slots=True)
class TuiStateSnapshot:
    """All SQLite-backed read models assembled in one pass.

    Deliberately excludes live Git inspection and provider discovery so the lightweight
    refresh timer never spawns a subprocess.
    """

    project: TuiProjectModel
    active_task: TuiTaskModel | None = None
    tasks: tuple[TuiTaskRow, ...] = ()
    sessions: tuple[TuiSessionRow, ...] = ()
    checkpoints: tuple[TuiCheckpointRow, ...] = ()
    handoffs: tuple[TuiHandoffRow, ...] = ()
    activity: TuiActivityModel = field(default_factory=TuiActivityModel)
    loaded_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class TuiRecoveryPreview:
    """Non-mutating preview of what a recovery run would reconcile."""

    task_id: str
    task_title: str
    stale_session_ids: tuple[str, ...] = ()
    dirty: bool = False
    files_touched: tuple[str, ...] = ()
    repository_status: str = "unknown"

    @property
    def stale_count(self) -> int:
        """Number of unfinalized sessions that would be reconciled."""
        return len(self.stale_session_ids)


@dataclass(frozen=True, slots=True)
class TuiSwitchPreview:
    """Bounded preview of a provider switch, produced without any model call."""

    target_provider_id: str
    target_provider_name: str
    source_provider_id: str
    source_session_id: str
    task_id: str
    task_title: str
    target_native_mode: str
    selected_prior_target_session_id: str | None
    git_status: str
    git_branch: str | None
    git_dirty: bool
    delivery_strategy: str
    bootstrap_model_turn_required: bool
    checkpoint_enrichment: str
    context_characters: int
    context_max_characters: int
    context_truncated: bool


@dataclass(frozen=True, slots=True)
class TuiHandoffPreview:
    """Bounded canonical handoff context rendered without persisting anything."""

    target_provider_id: str
    target_provider_name: str
    delivery_strategy: str
    bootstrap_model_turn_required: bool
    rendered_context: str
    context_characters: int
    context_max_characters: int
    context_truncated: bool


def abbreviate_id(value: str, keep: int = 8) -> str:
    """Abbreviate a canonical identifier for dense table rendering.

    Only ever used for display. Service calls always receive the full canonical ID, and
    detail panels always expose it in full.
    """
    if not value:
        return "—"
    prefix, separator, suffix = value.partition("_")
    if not separator:
        return value if len(value) <= keep else f"{value[:keep]}…"
    if len(suffix) <= keep:
        return value
    return f"{prefix}_{suffix[:keep]}…"


def format_relative(moment: datetime | None, *, now: datetime | None = None) -> str:
    """Render a UTC timestamp as a compact relative age.

    Canonical UTC datetimes are never mutated; this is a display projection only. Exact
    timestamps remain available in detail views.
    """
    if moment is None:
        return "—"

    from cortexshift.domain.identifiers import utc_now

    reference = now or utc_now()
    anchored = moment.replace(tzinfo=reference.tzinfo) if moment.tzinfo is None else moment
    seconds = (reference - anchored).total_seconds()
    if seconds < 0:
        return "just now"
    if seconds < 60:
        return "just now"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    if days < 30:
        return f"{days}d ago"
    months = days // 30
    if months < 12:
        return f"{months}mo ago"
    return f"{days // 365}y ago"


def format_timestamp(moment: datetime | None) -> str:
    """Render an exact UTC timestamp for copy-friendly detail panels."""
    if moment is None:
        return "—"
    return moment.isoformat()


def truncate(text: str | None, limit: int) -> str:
    """Bound a free-text value for dense rendering without altering canonical state."""
    if not text:
        return "—"
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return f"{collapsed[: max(1, limit - 1)]}…"


def bounded(items: Sequence[str], limit: int) -> tuple[list[str], int]:
    """Split a sequence into a displayable head and a count of omitted entries."""
    head = list(items[:limit])
    return head, max(0, len(items) - limit)

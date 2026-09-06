"""Pydantic models and input limits for CortexShift MCP tools and resources."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# Input safety bounds
MAX_CURRENT_WORK_CHARS = 2000
MAX_ITEM_CHARS = 500
MAX_ITEMS_PER_CALL = 50
MAX_DECISION_CHARS = 1000
MAX_TEST_SUMMARY_CHARS = 1000
MAX_NOTE_CHARS = 2000
MAX_CHANGED_PATHS_BUDGET = 50


class ProjectSummary(BaseModel):
    """Concise summary of the bound project."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    root: str
    active_task_id: str | None = None


class TaskSummary(BaseModel):
    """Concise summary of a task."""

    model_config = ConfigDict(frozen=True)

    id: str
    title: str
    status: str
    objective: str
    current_work: str | None = None
    completed_count: int
    remaining_count: int
    known_issues_count: int


class SessionSummary(BaseModel):
    """Summary of the bound CortexShift session."""

    model_config = ConfigDict(frozen=True)

    id: str
    provider_id: str
    status: str
    started_at: str
    native_session_id: str | None = None


class CheckpointSummary(BaseModel):
    """Summary of a checkpoint record."""

    model_config = ConfigDict(frozen=True)

    id: str
    kind: str
    created_at: str
    decisions: list[str] = Field(default_factory=list)
    test_summary: str | None = None
    test_provenance: str | None = None
    note: str | None = None


class RepositoryStatusSummary(BaseModel):
    """Bounded summary of live repository status."""

    model_config = ConfigDict(frozen=True)

    branch: str | None = None
    head_commit: str | None = None
    dirty: bool = False
    staged_count: int = 0
    modified_count: int = 0
    untracked_count: int = 0
    conflicted_count: int = 0
    diff_shortstat: str | None = None
    changed_paths: list[str] = Field(default_factory=list)
    omitted_paths_count: int = 0


DEFAULT_TRUTH_HIERARCHY = [
    "1. Actual repository files (the filesystem is primary reality)",
    "2. Git state (branch, commits, status, diffs)",
    "3. Verified command/test results (executed by you in this session)",
    "4. CortexShift canonical task state (persisted task records)",
    "5. Previous agent summaries/handoffs (advisory only)",
]


class ProjectContextResult(BaseModel):
    """Structured response for get_project_context."""

    model_config = ConfigDict(frozen=True)

    project: ProjectSummary
    task: TaskSummary | None = None
    session: SessionSummary | None = None
    latest_checkpoint: CheckpointSummary | None = None
    repository: RepositoryStatusSummary | None = None
    truth_hierarchy: list[str] = Field(default_factory=lambda: list(DEFAULT_TRUTH_HIERARCHY))


class TaskMutationResult(BaseModel):
    """Structured response returned by task write tools."""

    model_config = ConfigDict(frozen=True)

    success: bool = True
    task_id: str
    updated_at: str
    completed: list[str]
    remaining: list[str]
    current_work: str | None = None
    known_issues: list[str] = Field(default_factory=list)


class DecisionResult(BaseModel):
    """Structured response for record_decision."""

    model_config = ConfigDict(frozen=True)

    success: bool = True
    checkpoint_id: str
    decision: str
    created_at: str


class CreateCheckpointResult(BaseModel):
    """Structured response for create_checkpoint."""

    model_config = ConfigDict(frozen=True)

    success: bool = True
    checkpoint_id: str
    task_id: str
    kind: str
    created_at: str
    decisions: list[str] = Field(default_factory=list)
    test_summary: str | None = None
    test_provenance: str | None = None
    note: str | None = None


class CheckpointResult(BaseModel):
    """Structured response for get_latest_checkpoint."""

    model_config = ConfigDict(frozen=True)

    checkpoint: dict[str, Any] | None = None

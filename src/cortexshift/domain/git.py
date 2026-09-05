"""Domain model for Git repository snapshots and inspection results."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from cortexshift.domain.identifiers import generate_id, utc_now


class RepositoryInspectionStatus(StrEnum):
    """Status classification of a repository inspection."""

    READY = "ready"
    GIT_NOT_INSTALLED = "git_not_installed"
    NOT_GIT_REPOSITORY = "not_git_repository"
    PROBE_ERROR = "probe_error"


class GitSnapshot(BaseModel):
    """Snapshot of a repository's Git working tree and commit state.

    Represents an immutable, empirical version control observation at capture time.
    A stored snapshot is evidence of what was true when captured, never proof of
    current working tree reality after modifications occur.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=lambda: generate_id("snap"))
    project_id: str
    project_root: str
    git_root: str
    git_version: str | None = None
    branch: str | None = None
    head_sha: str | None = None
    detached_head: bool = False
    dirty: bool = False
    staged_files: list[str] = Field(default_factory=list)
    modified_files: list[str] = Field(default_factory=list)
    untracked_files: list[str] = Field(default_factory=list)
    conflicted_files: list[str] = Field(default_factory=list)
    working_tree_diff_summary: str | None = None
    staged_diff_summary: str | None = None
    captured_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_inputs(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "repo_path" in data:
                data.setdefault("project_root", data["repo_path"])
                data.setdefault("git_root", data["repo_path"])
            if "is_dirty" in data:
                data.setdefault("dirty", data["is_dirty"])
            if "snapshot_at" in data:
                data.setdefault("captured_at", data["snapshot_at"])
            if "diff_summary" in data:
                data.setdefault("working_tree_diff_summary", data["diff_summary"])
            data.setdefault("project_id", "proj_default")
        return data

    @property
    def repo_path(self) -> str:
        """Backward-compatible alias for project_root."""
        return self.project_root

    @property
    def is_dirty(self) -> bool:
        """Backward-compatible alias for dirty."""
        return self.dirty

    @property
    def snapshot_at(self) -> datetime:
        """Backward-compatible alias for captured_at."""
        return self.captured_at

    @property
    def diff_summary(self) -> str | None:
        """Backward-compatible alias for working_tree_diff_summary."""
        return self.working_tree_diff_summary


class RepositoryInspection(BaseModel):
    """Result of a live repository inspection."""

    model_config = ConfigDict(frozen=True)

    status: RepositoryInspectionStatus
    project_root: str
    git_available: bool
    git_version: str | None = None
    snapshot: GitSnapshot | None = None
    diagnostic: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_ready(self) -> bool:
        """Return True if the repository was successfully inspected and ready."""
        return self.status == RepositoryInspectionStatus.READY and self.snapshot is not None

"""Domain model for Git repository snapshots."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from cortexshift.domain.identifiers import utc_now


class GitSnapshot(BaseModel):
    """Snapshot of a repository's Git working tree and commit state.

    Represents empirical version control state without executing Git commands.
    """

    model_config = ConfigDict(frozen=True)

    repo_path: str
    branch: str | None = None
    head_sha: str | None = None
    is_dirty: bool = False
    staged_files: list[str] = Field(default_factory=list)
    modified_files: list[str] = Field(default_factory=list)
    untracked_files: list[str] = Field(default_factory=list)
    diff_summary: str | None = None
    snapshot_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

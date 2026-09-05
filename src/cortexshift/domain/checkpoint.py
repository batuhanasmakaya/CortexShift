"""Domain models for the canonical CortexShift checkpoint protocol (Protocol v1).

A Checkpoint is an immutable structured observation of the Task and repository at a
meaningful point in development. Checkpoints do not contain provider transcripts,
reasoning, full diffs, or file contents. They provide resilient, deterministic engineering
evidence that enables recovery from crashes, quota exhaustion, and unexpected terminations
without requiring the outgoing provider or model to still be available.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from cortexshift.domain.git import RepositoryInspectionStatus
from cortexshift.domain.identifiers import generate_id, utc_now
from cortexshift.domain.provider import ProviderId
from cortexshift.domain.session import SessionExitReason, SessionStatus

CHECKPOINT_PROTOCOL_VERSION = 1

MAX_OPERATOR_NOTE_CHARS = 2_000
MAX_DECISION_CHARS = 1_000
MAX_TEST_SUMMARY_CHARS = 1_000


class CheckpointKind(StrEnum):
    """Classification of how a checkpoint was captured.

    - MANUAL: Explicitly requested by user or agent via `cortexshift checkpoint create`.
    - SESSION_END: Automatically captured when a launched provider process terminates.
    - RECOVERY: Reconstructed after an interrupted session via `cortexshift recover`.
    """

    MANUAL = "manual"
    SESSION_END = "session_end"
    RECOVERY = "recovery"


class CheckpointTestProvenance(StrEnum):
    """Origin and trustworthiness of recorded test status.

    - UNKNOWN: No test execution was recorded or observed.
    - REPORTED: Reported by human operator or agent, but not independently verified.
    - VERIFIED: Directly observed and validated by a CortexShift verification subsystem.
    """

    UNKNOWN = "unknown"
    REPORTED = "reported"
    VERIFIED = "verified"


class CheckpointTestStatus(BaseModel):
    """Structured test status with explicit provenance.

    A provider process exiting with code 0 does NOT prove tests passed. Where no
    independently verified execution occurred, provenance remains REPORTED or UNKNOWN.
    """

    model_config = ConfigDict(frozen=True)

    known: bool = False
    summary: str = "No independently verified test results recorded."
    provenance: CheckpointTestProvenance = CheckpointTestProvenance.UNKNOWN


class CheckpointTaskSnapshot(BaseModel):
    """Historical point-in-time snapshot of the Task state.

    Checkpoints preserve the Task state as it was when the checkpoint was captured,
    ensuring historical immutability when the Task is later modified.
    """

    model_config = ConfigDict(frozen=True)

    task_id: str
    task_title: str
    task_status: str
    objective: str
    requirements: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    completed: list[str] = Field(default_factory=list)
    current_work: str | None = None
    remaining: list[str] = Field(default_factory=list)
    known_issues: list[str] = Field(default_factory=list)


class CheckpointGitState(BaseModel):
    """Historical point-in-time observation of Git state."""

    model_config = ConfigDict(frozen=True)

    status: RepositoryInspectionStatus
    available: bool = False
    note: str
    branch: str | None = None
    head_sha: str | None = None
    detached_head: bool = False
    dirty: bool = False
    staged_count: int = 0
    modified_count: int = 0
    untracked_count: int = 0
    conflicted_count: int = 0
    working_tree_diff_summary: str | None = None
    staged_diff_summary: str | None = None
    snapshot_id: str | None = None


class CheckpointSourceSession(BaseModel):
    """Compact summary of the CortexShift Session associated with this checkpoint.

    Provider transcripts, conversations, and reasoning are strictly excluded.
    """

    model_config = ConfigDict(frozen=True)

    session_id: str
    provider_id: ProviderId
    native_session_id: str | None = None
    status: SessionStatus | None = None
    exit_reason: SessionExitReason | None = None
    exit_code: int | None = None


class CheckpointPayload(BaseModel):
    """Canonical structured point-in-time engineering context for a checkpoint."""

    model_config = ConfigDict(frozen=True)

    protocol_version: int = CHECKPOINT_PROTOCOL_VERSION
    generated_at: datetime = Field(default_factory=utc_now)

    task: CheckpointTaskSnapshot
    git_state: CheckpointGitState
    files_touched: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    test_status: CheckpointTestStatus = Field(default_factory=CheckpointTestStatus)
    operator_note: str | None = None
    source_session: CheckpointSourceSession | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


def generate_checkpoint_id() -> str:
    """Generate default ID for checkpoints."""
    return generate_id("cp")


class CheckpointRecord(BaseModel):
    """Canonical persistent checkpoint entity wrapping an immutable payload."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=generate_checkpoint_id)
    protocol_version: int = CHECKPOINT_PROTOCOL_VERSION
    project_id: str
    task_id: str
    session_id: str | None = None
    git_snapshot_id: str | None = None
    kind: CheckpointKind
    payload: CheckpointPayload
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


# Canonical alias
Checkpoint = CheckpointRecord

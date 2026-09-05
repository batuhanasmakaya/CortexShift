"""Domain models for the canonical, provider-independent CortexShift handoff protocol.

A handoff is an immutable point-in-time context package that allows a CortexShift Task
to move from one coding agent to another without requiring the outgoing agent to still
be available. Two concepts are modelled explicitly:

- ``HandoffPayload``: the canonical point-in-time engineering context (protocol v1).
- ``HandoffRecord``: orchestration and delivery metadata wrapping a payload.

The payload is deliberately provider-neutral. Provider-specific transport (how the
context reaches a given CLI) lives behind handoff delivery adapters, and rendered
provider prompts are never persisted.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from cortexshift.domain.git import RepositoryInspectionStatus
from cortexshift.domain.identifiers import generate_id, utc_now
from cortexshift.domain.provider import ProviderId
from cortexshift.domain.session import SessionExitReason, SessionStatus

# Version of the canonical handoff contract. Deliberately independent from the
# SQLite schema version: handoff formatting and canonical fields may evolve
# without a database migration, and vice versa.
HANDOFF_PROTOCOL_VERSION = 1

# Maximum number of characters retained for an operator-supplied note. The note is
# optional advisory context and must never make the canonical payload unbounded.
MAX_OPERATOR_NOTE_CHARS = 2_000


class HandoffStatus(StrEnum):
    """Delivery lifecycle of a handoff record.

    - PREPARED: canonical handoff exists; target delivery is not yet known complete.
    - DELIVERED: handoff context was successfully delivered via the provider strategy.
    - FAILED: delivery or provider bootstrap could not be completed.

    Handoff status is distinct from Session status: the target Session remains the
    source of truth for how the receiving coding session itself eventually ended.
    """

    PREPARED = "prepared"
    DELIVERED = "delivered"
    FAILED = "failed"


class HandoffFailureCode(StrEnum):
    """Safe machine classification of a handoff delivery failure.

    Failure codes never embed raw provider stdout/stderr, credentials, or prompts.
    """

    TARGET_PROVIDER_MISSING = "target_provider_missing"
    BOOTSTRAP_FAILED = "bootstrap_failed"
    BOOTSTRAP_TIMEOUT = "bootstrap_timeout"
    BOOTSTRAP_INVALID_OUTPUT = "bootstrap_invalid_output"
    SPAWN_FAILED = "spawn_failed"
    WORKSPACE_LOCKED = "workspace_locked"


class HandoffGitState(BaseModel):
    """Canonical Git-state section of a handoff payload.

    CortexShift projects do not require Git. When Git is unavailable or the project is
    not a repository, this records an explicit, honest marker rather than fabricating
    repository facts. When Git is ready, ``snapshot_id`` references the immutable
    ``GitSnapshot`` persisted at handoff time — a historical observation, never proof
    of current working tree reality.
    """

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


class HandoffTestStatus(BaseModel):
    """Canonical TEST STATUS section.

    CortexShift has no durable verified test record in Phase 5. A provider process
    exiting with code 0 does not prove that project tests passed, so ``known`` stays
    False and the summary states the unknown honestly.
    """

    model_config = ConfigDict(frozen=True)

    known: bool = False
    summary: str


class HandoffSourceSession(BaseModel):
    """Metadata of the CortexShift Session the work is being handed off from.

    Derived strictly from durable CortexShift session records. Provider-native
    transcripts, conversation histories, and hidden reasoning are never read.
    """

    model_config = ConfigDict(frozen=True)

    session_id: str
    provider_id: ProviderId
    status: SessionStatus
    started_at: datetime
    ended_at: datetime | None = None
    exit_reason: SessionExitReason | None = None
    exit_code: int | None = None


class HandoffPayload(BaseModel):
    """Canonical, provider-independent point-in-time engineering context.

    Built deterministically from durable local state (canonical Project, canonical Task,
    previous CortexShift Session metadata, live repository inspection, and the Git
    snapshot persisted at handoff time). Generating this payload never requires the
    outgoing provider to be installed, running, or able to answer.
    """

    model_config = ConfigDict(frozen=True)

    protocol_version: int = HANDOFF_PROTOCOL_VERSION
    generated_at: datetime = Field(default_factory=utc_now)

    # PROJECT
    project_name: str
    project_root: str

    # Task identity
    task_id: str
    task_title: str
    task_status: str

    # Canonical protocol sections
    original_objective: str
    requirements: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    completed: list[str] = Field(default_factory=list)
    current_work: str | None = None
    remaining: list[str] = Field(default_factory=list)
    important_decisions: list[str] = Field(default_factory=list)
    decisions_known: bool = False
    files_touched: list[str] = Field(default_factory=list)
    test_status: HandoffTestStatus
    known_issues: list[str] = Field(default_factory=list)
    git_state: HandoffGitState
    do_not_redo: list[str] = Field(default_factory=list)
    recommended_next_action: str

    # Provenance
    source_session: HandoffSourceSession
    target_provider_id: ProviderId
    operator_note: str | None = None
    source_checkpoint_id: str | None = None
    source_checkpoint_kind: str | None = None
    source_checkpoint_created_at: datetime | None = None

    metadata: dict[str, Any] = Field(default_factory=dict)


def generate_handoff_id() -> str:
    """Generate default ID for handoff records."""
    return generate_id("handoff")


class HandoffRecord(BaseModel):
    """Orchestration and delivery metadata wrapping a canonical handoff payload.

    The record tracks which Task moved between which CortexShift Sessions and providers,
    which Git snapshot was captured, and whether delivery ultimately succeeded. Rendered
    provider prompts and provider responses are deliberately not part of this record.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=generate_handoff_id)
    protocol_version: int = HANDOFF_PROTOCOL_VERSION

    project_id: str
    task_id: str

    source_session_id: str
    source_provider_id: ProviderId
    target_provider_id: ProviderId

    source_checkpoint_id: str | None = None
    git_snapshot_id: str | None = None
    target_session_id: str | None = None

    status: HandoffStatus = HandoffStatus.PREPARED
    payload: HandoffPayload

    created_at: datetime = Field(default_factory=utc_now)
    delivered_at: datetime | None = None
    failure_code: HandoffFailureCode | None = None

    metadata: dict[str, Any] = Field(default_factory=dict)

    def mark_delivered(
        self,
        target_session_id: str | None = None,
        delivered_at: datetime | None = None,
    ) -> "HandoffRecord":
        """Return a copy marked as delivered, optionally binding the target Session."""
        return self.model_copy(
            update={
                "status": HandoffStatus.DELIVERED,
                "delivered_at": delivered_at or utc_now(),
                "target_session_id": target_session_id or self.target_session_id,
                "failure_code": None,
            }
        )

    def mark_failed(
        self,
        failure_code: HandoffFailureCode,
        target_session_id: str | None = None,
    ) -> "HandoffRecord":
        """Return a copy marked as failed with a safe machine classification."""
        return self.model_copy(
            update={
                "status": HandoffStatus.FAILED,
                "failure_code": failure_code,
                "target_session_id": target_session_id or self.target_session_id,
            }
        )

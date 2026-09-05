"""Unit tests for deterministic canonical handoff payload construction."""

from cortexshift.application.handoff_builder import (
    UNKNOWN_TEST_STATUS_STATEMENT,
    HandoffBuilder,
    bound_operator_note,
    derive_files_touched,
    derive_recommended_next_action,
)
from cortexshift.domain.git import RepositoryInspectionStatus
from cortexshift.domain.handoff import HANDOFF_PROTOCOL_VERSION, MAX_OPERATOR_NOTE_CHARS
from cortexshift.domain.identifiers import utc_now
from cortexshift.domain.project import Project
from cortexshift.domain.provider import PROVIDER_CLAUDE, PROVIDER_CODEX
from cortexshift.domain.session import Session, SessionExitReason, SessionStatus
from cortexshift.domain.task import Task, TaskStatus
from tests.factories import make_inspection


def _project() -> Project:
    return Project(id="proj_b", name="BuilderProject", repo_path="/repo")


def _task(**overrides: object) -> Task:
    defaults: dict[str, object] = {
        "project_id": "proj_b",
        "title": "OAuth support",
        "objective": "Add OAuth2 PKCE support.",
        "requirements": ["Support refresh tokens"],
        "constraints": ["No new dependencies"],
        "completed_items": ["Scaffolded auth module"],
        "remaining_items": ["Wire token refresh", "Add integration tests"],
        "known_issues": ["Token refresh race condition"],
        "status": TaskStatus.IN_PROGRESS,
    }
    defaults.update(overrides)
    return Task(**defaults)  # type: ignore[arg-type]


def _session() -> Session:
    return Session(
        id="sess_source",
        task_id="task_b",
        provider_id=PROVIDER_CLAUDE,
        status=SessionStatus.COMPLETED,
        ended_at=utc_now(),
        exit_reason=SessionExitReason.NORMAL_COMPLETION,
        exit_code=0,
    )


def test_build_captures_canonical_task_state() -> None:
    """Verify the payload carries every canonical section from durable task state."""
    payload = HandoffBuilder().build(
        project=_project(),
        task=_task(current_work="Implementing token exchange"),
        source_session=_session(),
        inspection=make_inspection(),
        target_provider_id=PROVIDER_CODEX,
        snapshot_id="snap_1",
    )

    assert payload.protocol_version == HANDOFF_PROTOCOL_VERSION
    assert payload.project_name == "BuilderProject"
    assert payload.original_objective == "Add OAuth2 PKCE support."
    assert payload.requirements == ["Support refresh tokens"]
    assert payload.constraints == ["No new dependencies"]
    assert payload.completed == ["Scaffolded auth module"]
    assert payload.current_work == "Implementing token exchange"
    assert payload.remaining == ["Wire token refresh", "Add integration tests"]
    assert payload.known_issues == ["Token refresh race condition"]
    assert payload.source_session.session_id == "sess_source"
    assert payload.source_session.provider_id == PROVIDER_CLAUDE
    assert payload.target_provider_id == PROVIDER_CODEX
    assert payload.git_state.snapshot_id == "snap_1"


def test_build_is_deterministic_apart_from_timestamp() -> None:
    """Verify repeated builds from identical state produce identical canonical content."""
    builder = HandoffBuilder()
    kwargs = {
        "project": _project(),
        "task": _task(current_work="Implementing token exchange"),
        "source_session": _session(),
        "inspection": make_inspection(),
        "target_provider_id": PROVIDER_CODEX,
        "snapshot_id": "snap_1",
    }
    first = builder.build(**kwargs)  # type: ignore[arg-type]
    second = builder.build(**kwargs)  # type: ignore[arg-type]

    assert first.model_dump(exclude={"generated_at"}) == second.model_dump(exclude={"generated_at"})


def test_unknown_decisions_and_tests_are_never_fabricated() -> None:
    """Verify absent decision and test records stay explicitly unknown."""
    payload = HandoffBuilder().build(
        project=_project(),
        task=_task(),
        # A source session that exited 0 must never be read as "all tests pass".
        source_session=_session(),
        inspection=make_inspection(),
        target_provider_id=PROVIDER_CODEX,
    )

    assert payload.decisions_known is False
    assert payload.important_decisions == []
    assert payload.test_status.known is False
    assert payload.test_status.summary == UNKNOWN_TEST_STATUS_STATEMENT
    assert "pass" not in payload.test_status.summary.lower().replace("previous claims", "")


def test_completed_items_inform_do_not_redo() -> None:
    """Verify DO NOT REDO is derived from recorded completed work."""
    payload = HandoffBuilder().build(
        project=_project(),
        task=_task(completed_items=["Item A", "Item B"]),
        source_session=_session(),
        inspection=make_inspection(),
        target_provider_id=PROVIDER_CODEX,
    )

    assert payload.do_not_redo == ["Item A", "Item B"]
    assert payload.completed == ["Item A", "Item B"]


def test_files_touched_is_deduplicated_union_in_deterministic_order() -> None:
    """Verify files touched unions all Git change classes without duplicates."""
    inspection = make_inspection(
        staged=["a.py", "shared.py"],
        modified=["shared.py", "b.py"],
        untracked=["c.py"],
        conflicted=["b.py", "d.py"],
    )

    assert derive_files_touched(inspection) == ["a.py", "shared.py", "b.py", "c.py", "d.py"]


def test_files_touched_empty_without_snapshot() -> None:
    """Verify no files are invented when Git produced no snapshot."""
    inspection = make_inspection(status=RepositoryInspectionStatus.NOT_GIT_REPOSITORY)
    assert derive_files_touched(inspection) == []


def test_recommended_next_action_prefers_current_work() -> None:
    """Verify current work wins over the remaining backlog."""
    task = _task(current_work="Implementing token exchange")
    assert "Implementing token exchange" in derive_recommended_next_action(task)


def test_recommended_next_action_falls_back_to_first_remaining() -> None:
    """Verify the first remaining item is used when no work is in flight."""
    task = _task(current_work=None, remaining_items=["Wire token refresh", "Later item"])
    action = derive_recommended_next_action(task)
    assert "Wire token refresh" in action
    assert "Later item" not in action


def test_recommended_next_action_falls_back_to_repository_inspection() -> None:
    """Verify an empty backlog yields a repository-inspection instruction."""
    task = _task(current_work=None, remaining_items=[])
    action = derive_recommended_next_action(task)
    assert "git status" in action


def test_git_state_ready_records_counts_and_snapshot() -> None:
    """Verify a ready repository produces a fully populated Git state section."""
    payload = HandoffBuilder().build(
        project=_project(),
        task=_task(),
        source_session=_session(),
        inspection=make_inspection(),
        target_provider_id=PROVIDER_CODEX,
        snapshot_id="snap_ready",
    )

    git = payload.git_state
    assert git.status == RepositoryInspectionStatus.READY
    assert git.available is True
    assert git.branch == "main"
    assert git.staged_count == 1
    assert git.modified_count == 1
    assert git.untracked_count == 1
    assert git.snapshot_id == "snap_ready"


def test_git_state_missing_git_uses_explicit_marker() -> None:
    """Verify missing Git yields an honest marker instead of fabricated repository facts."""
    for status in (
        RepositoryInspectionStatus.GIT_NOT_INSTALLED,
        RepositoryInspectionStatus.NOT_GIT_REPOSITORY,
    ):
        payload = HandoffBuilder().build(
            project=_project(),
            task=_task(),
            source_session=_session(),
            inspection=make_inspection(status=status),
            target_provider_id=PROVIDER_CODEX,
        )
        git = payload.git_state
        assert git.status == status
        assert git.available is False
        assert git.branch is None
        assert git.head_sha is None
        assert git.snapshot_id is None
        assert git.note


def test_operator_note_is_bounded_and_reported() -> None:
    """Verify an oversized operator note is bounded with an explicit omission marker."""
    assert bound_operator_note(None) is None
    assert bound_operator_note("   ") is None
    assert bound_operator_note("  hello  ") == "hello"

    huge = "x" * (MAX_OPERATOR_NOTE_CHARS + 500)
    bounded = bound_operator_note(huge)
    assert bounded is not None
    assert "500 characters omitted" in bounded
    assert len(bounded) < len(huge)


def test_operator_note_kept_separate_from_canonical_fields() -> None:
    """Verify the operator note has explicit provenance and never merges into task state."""
    payload = HandoffBuilder().build(
        project=_project(),
        task=_task(),
        source_session=_session(),
        inspection=make_inspection(),
        target_provider_id=PROVIDER_CODEX,
        operator_note="Watch out for the flaky test",
    )

    assert payload.operator_note == "Watch out for the flaky test"
    assert "flaky" not in payload.original_objective
    assert all("flaky" not in r for r in payload.requirements)
    assert all("flaky" not in c for c in payload.constraints)

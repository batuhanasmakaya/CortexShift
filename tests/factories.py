"""Shared deterministic factories and test doubles for Phase 5 handoff tests."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.domain.git import (
    GitSnapshot,
    RepositoryInspection,
    RepositoryInspectionStatus,
)
from cortexshift.domain.handoff import (
    HandoffGitState,
    HandoffPayload,
    HandoffSourceSession,
    HandoffTestStatus,
)
from cortexshift.domain.identifiers import utc_now
from cortexshift.domain.project import Project
from cortexshift.domain.provider import PROVIDER_CLAUDE, PROVIDER_CODEX, ProviderId
from cortexshift.domain.session import Session, SessionExitReason, SessionStatus
from cortexshift.domain.task import Task
from cortexshift.ports.headless_runner import HeadlessResult


def make_inspection(
    project_root: Path | str = "/repo",
    project_id: str = "proj_test",
    status: RepositoryInspectionStatus = RepositoryInspectionStatus.READY,
    staged: list[str] | None = None,
    modified: list[str] | None = None,
    untracked: list[str] | None = None,
    conflicted: list[str] | None = None,
    diagnostic: str | None = None,
) -> RepositoryInspection:
    """Build a RepositoryInspection without touching a real repository."""
    if status != RepositoryInspectionStatus.READY:
        return RepositoryInspection(
            status=status,
            project_root=str(project_root),
            git_available=status != RepositoryInspectionStatus.GIT_NOT_INSTALLED,
            snapshot=None,
            diagnostic=diagnostic,
        )

    snapshot = GitSnapshot(
        project_id=project_id,
        project_root=str(project_root),
        git_root=str(project_root),
        git_version="git version 2.40.0",
        branch="main",
        head_sha="abc1234567890def",
        dirty=True,
        staged_files=staged if staged is not None else ["src/staged.py"],
        modified_files=modified if modified is not None else ["src/modified.py"],
        untracked_files=untracked if untracked is not None else ["src/untracked.py"],
        conflicted_files=conflicted if conflicted is not None else [],
        working_tree_diff_summary="2 files changed, 10 insertions(+)",
        staged_diff_summary="1 file changed, 4 insertions(+)",
    )
    return RepositoryInspection(
        status=RepositoryInspectionStatus.READY,
        project_root=str(project_root),
        git_available=True,
        git_version="git version 2.40.0",
        snapshot=snapshot,
    )


def make_payload(
    target_provider_id: ProviderId = PROVIDER_CODEX,
    source_provider_id: ProviderId = PROVIDER_CLAUDE,
    completed: list[str] | None = None,
    remaining: list[str] | None = None,
    requirements: list[str] | None = None,
    constraints: list[str] | None = None,
    files_touched: list[str] | None = None,
    known_issues: list[str] | None = None,
    current_work: str | None = "Implementing the token exchange",
    objective: str = "Add OAuth2 PKCE support to the auth layer.",
    operator_note: str | None = None,
) -> HandoffPayload:
    """Build a canonical handoff payload directly, bypassing the builder."""
    return HandoffPayload(
        project_name="DemoProject",
        project_root="/repo",
        task_id="task_demo",
        task_title="OAuth support",
        task_status="in_progress",
        original_objective=objective,
        requirements=requirements if requirements is not None else ["Support refresh tokens"],
        constraints=constraints if constraints is not None else ["No new dependencies"],
        completed=completed if completed is not None else ["Scaffolded auth module"],
        current_work=current_work,
        remaining=remaining if remaining is not None else ["Wire token refresh"],
        important_decisions=[],
        decisions_known=False,
        files_touched=files_touched if files_touched is not None else ["src/auth.py"],
        test_status=HandoffTestStatus(known=False, summary="No verified test result is recorded."),
        known_issues=known_issues if known_issues is not None else [],
        git_state=HandoffGitState(
            status=RepositoryInspectionStatus.READY,
            available=True,
            note="Live Git inspection succeeded at handoff time.",
            branch="main",
            head_sha="abc1234567890def",
            dirty=True,
            staged_count=1,
            modified_count=1,
            untracked_count=1,
            snapshot_id="snap_demo",
        ),
        do_not_redo=completed if completed is not None else ["Scaffolded auth module"],
        recommended_next_action="Continue the work already in progress.",
        source_session=HandoffSourceSession(
            session_id="sess_source",
            provider_id=source_provider_id,
            status=SessionStatus.COMPLETED,
            started_at=utc_now(),
            ended_at=utc_now(),
            exit_reason=SessionExitReason.NORMAL_COMPLETION,
            exit_code=0,
        ),
        target_provider_id=target_provider_id,
        operator_note=operator_note,
    )


def seed_project(
    root_path: Path,
    project_name: str = "HandoffProject",
    objective: str = "Add OAuth2 PKCE support to the auth layer.",
    requirements: list[str] | None = None,
    constraints: list[str] | None = None,
    completed: list[str] | None = None,
    remaining: list[str] | None = None,
    known_issues: list[str] | None = None,
    current_work: str | None = None,
) -> tuple[Project, Task]:
    """Initialize a CortexShift project with one active, populated task."""
    db_file = root_path / ".cortexshift" / "state.sqlite3"
    with SQLiteStateStore(db_file) as store:
        project = Project(name=project_name, repo_path=str(root_path))
        store.save_project(project)

        task = Task(
            project_id=project.id,
            title="OAuth support",
            objective=objective,
            requirements=requirements if requirements is not None else ["Support refresh tokens"],
            constraints=constraints if constraints is not None else ["No new dependencies"],
            completed_items=completed or [],
            remaining_items=remaining if remaining is not None else ["Wire token refresh"],
            known_issues=known_issues or [],
            current_work=current_work,
        )
        store.save_task(task)
        store.set_active_task_id(project.id, task.id)

    return project, task


def seed_session(
    root_path: Path,
    task_id: str,
    provider_id: ProviderId = PROVIDER_CLAUDE,
    status: SessionStatus = SessionStatus.COMPLETED,
    exit_reason: SessionExitReason | None = SessionExitReason.NORMAL_COMPLETION,
    exit_code: int | None = 0,
) -> Session:
    """Persist a historical CortexShift session for an existing task."""
    db_file = root_path / ".cortexshift" / "state.sqlite3"
    session = Session(
        task_id=task_id,
        provider_id=provider_id,
        status=status,
        ended_at=utc_now(),
        exit_reason=exit_reason,
        exit_code=exit_code,
    )
    with SQLiteStateStore(db_file, auto_migrate=False) as store:
        store.save_session(session)
    return session


class FakeShutil:
    """Stand-in for the `shutil` name bound inside one application module.

    Patching `<module>.shutil.which` would rebind the attribute on the real, shared
    `shutil` module and silently break unrelated lookups (notably the Git inspector's
    `shutil.which("git")`). Rebinding the module-local `shutil` name instead keeps the
    substitution scoped to the module under test.
    """

    def __init__(self, resolver: Callable[[str], str | None]) -> None:
        self._resolver = resolver

    def which(self, cmd: str, *args: Any, **kwargs: Any) -> str | None:
        return self._resolver(cmd)


def patch_which(
    monkeypatch: pytest.MonkeyPatch,
    module: str,
    resolver: Callable[[str], str | None],
) -> None:
    """Scope provider executable resolution to a single application module."""
    monkeypatch.setattr(f"{module}.shutil", FakeShutil(resolver))


class FakeCodexBootstrap:
    """Deterministic native CLI event stream; records transport only in test memory."""

    def __init__(self) -> None:
        self.invocations: list[list[str]] = []

    def run_headless(
        self,
        argv: list[str],
        cwd: Path | str,
        timeout: float = 300.0,
        env: dict[str, str] | None = None,
    ) -> "HeadlessResult":
        import json

        from cortexshift.ports.headless_runner import HeadlessResult

        self.invocations.append(argv)
        native_id = argv[argv.index("--json") + 1] if "resume" in argv else "test-codex-native-id"
        return HeadlessResult(
            exit_code=0,
            stdout="\n".join(
                [
                    json.dumps({"type": "thread.started", "thread_id": native_id}),
                    json.dumps({"type": "item.completed", "response": "CODEX-RESPONSE-PRIVATE"}),
                    json.dumps({"type": "turn.completed"}),
                ]
            ),
            stderr="",
        )

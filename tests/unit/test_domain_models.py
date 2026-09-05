"""Comprehensive unit tests for all CortexShift domain models."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from cortexshift.domain import (
    PROVIDER_ANTIGRAVITY,
    PROVIDER_CLAUDE,
    PROVIDER_CODEX,
    Checkpoint,
    GitSnapshot,
    Handoff,
    Project,
    ProviderCapabilities,
    ProviderId,
    Session,
    SessionExitReason,
    SessionStatus,
    Task,
    TaskStatus,
    generate_id,
    utc_now,
)


class TestIdentifiers:
    """Tests for ID generation and UTC timestamp utilities."""

    def test_generate_id_format(self) -> None:
        proj_id = generate_id("proj")
        assert proj_id.startswith("proj_")
        assert len(proj_id) == 5 + 32  # prefix + underscore + 32 hex chars

        task_id = generate_id("task")
        assert task_id.startswith("task_")

    def test_utc_now_timezone_aware(self) -> None:
        now = utc_now()
        assert now.tzinfo is not None
        assert now.tzinfo == UTC


class TestProjectModel:
    """Tests for the Project domain entity."""

    def test_project_defaults(self) -> None:
        project = Project(name="Test Project", repo_path="/path/to/repo")
        assert project.id.startswith("proj_")
        assert project.name == "Test Project"
        assert project.repo_path == "/path/to/repo"
        assert isinstance(project.created_at, datetime)
        assert project.created_at.tzinfo == UTC
        assert project.metadata == {}

    def test_project_validation_empty_name(self) -> None:
        with pytest.raises(ValidationError, match="Project name cannot be empty"):
            Project(name="   ", repo_path="/path/to/repo")

    def test_project_serialization_roundtrip(self) -> None:
        project = Project(name="Demo", repo_path="/tmp/demo", metadata={"key": "val"})
        json_data = project.model_dump_json()
        restored = Project.model_validate_json(json_data)
        assert restored == project


class TestTaskModel:
    """Tests for the Task domain entity and TaskStatus enum."""

    def test_task_defaults(self) -> None:
        task = Task(
            project_id="proj_123",
            title="Implement Feature X",
            objective="Deliver end-to-end Feature X functionality",
        )
        assert task.id.startswith("task_")
        assert task.project_id == "proj_123"
        assert task.status == TaskStatus.PENDING
        assert task.requirements == []
        assert task.constraints == []
        assert task.completed_items == []
        assert task.current_work is None
        assert task.remaining_items == []
        assert task.known_issues == []
        assert task.created_at.tzinfo == UTC
        assert task.updated_at.tzinfo == UTC

    def test_task_status_values(self) -> None:
        assert TaskStatus.PENDING.value == "pending"
        assert TaskStatus.IN_PROGRESS.value == "in_progress"
        assert TaskStatus.BLOCKED.value == "blocked"
        assert TaskStatus.COMPLETED.value == "completed"
        assert TaskStatus.CANCELLED.value == "cancelled"

    def test_task_empty_title_fails(self) -> None:
        with pytest.raises(ValidationError, match="Field cannot be empty"):
            Task(project_id="proj_123", title="", objective="Valid objective")

    def test_task_empty_objective_fails(self) -> None:
        with pytest.raises(ValidationError, match="Field cannot be empty"):
            Task(project_id="proj_123", title="Valid title", objective="  ")

    def test_task_serialization_roundtrip(self) -> None:
        task = Task(
            project_id="proj_123",
            title="Build Auth",
            objective="Secure API endpoints",
            requirements=["JWT", "Refresh tokens"],
            constraints=["No cloud DB"],
            status=TaskStatus.IN_PROGRESS,
            completed_items=["JWT validation"],
            current_work="Refresh token rotation",
            remaining_items=["Revocation list"],
            known_issues=["Clock drift on server"],
        )
        json_data = task.model_dump_json()
        restored = Task.model_validate_json(json_data)
        assert restored == task


class TestSessionModel:
    """Tests for the Session domain entity and enums."""

    def test_session_defaults(self) -> None:
        session = Session(
            task_id="task_123",
            provider_id=PROVIDER_CLAUDE,
        )
        assert session.id.startswith("sess_")
        assert session.task_id == "task_123"
        assert session.provider_id == "claude"
        assert session.status == SessionStatus.INITIALIZING
        assert session.native_session_id is None
        assert session.started_at.tzinfo == UTC
        assert session.ended_at is None
        assert session.exit_reason is None

    def test_session_status_and_exit_reasons(self) -> None:
        session = Session(
            task_id="task_123",
            provider_id=PROVIDER_ANTIGRAVITY,
            status=SessionStatus.COMPLETED,
            exit_reason=SessionExitReason.NORMAL_COMPLETION,
        )
        assert session.status == SessionStatus.COMPLETED
        assert session.exit_reason == SessionExitReason.NORMAL_COMPLETION

    def test_session_serialization_roundtrip(self) -> None:
        session = Session(
            task_id="task_123",
            provider_id=PROVIDER_CODEX,
            native_session_id="thread_xyz123",
            status=SessionStatus.INTERRUPTED,
            exit_reason=SessionExitReason.RATE_LIMITED,
        )
        json_data = session.model_dump_json()
        restored = Session.model_validate_json(json_data)
        assert restored == session


class TestCheckpointModel:
    """Tests for the Checkpoint progress snapshot entity."""

    def test_checkpoint_defaults(self) -> None:
        checkpoint = Checkpoint(
            task_id="task_123",
            done=["Created data models"],
            current="Writing unit tests",
            next_steps=["Implement CLI command"],
            decisions=["Use Pydantic v2"],
            issues=["None"],
            files=["src/cortexshift/domain/task.py"],
            test_summary="15 passed in 0.2s",
        )
        assert checkpoint.id.startswith("cp_")
        assert checkpoint.task_id == "task_123"
        assert checkpoint.done == ["Created data models"]
        assert checkpoint.current == "Writing unit tests"
        assert checkpoint.test_summary == "15 passed in 0.2s"
        assert checkpoint.created_at.tzinfo == UTC

    def test_checkpoint_serialization_roundtrip(self) -> None:
        checkpoint = Checkpoint(
            task_id="task_abc",
            session_id="sess_123",
            done=["Step 1"],
            next_steps=["Step 2"],
        )
        json_data = checkpoint.model_dump_json()
        restored = Checkpoint.model_validate_json(json_data)
        assert restored == checkpoint


class TestGitSnapshotModel:
    """Tests for the GitSnapshot domain model."""

    def test_git_snapshot_creation(self) -> None:
        snapshot = GitSnapshot(
            repo_path="/Users/developer/project",
            branch="feature/handoff",
            head_sha="a1b2c3d4e5f6",
            is_dirty=True,
            staged_files=["src/main.py"],
            modified_files=["README.md"],
            untracked_files=["new_file.py"],
            diff_summary="2 files changed, 10 insertions(+), 2 deletions(-)",
        )
        assert snapshot.repo_path == "/Users/developer/project"
        assert snapshot.branch == "feature/handoff"
        assert snapshot.head_sha == "a1b2c3d4e5f6"
        assert snapshot.is_dirty is True
        assert snapshot.staged_files == ["src/main.py"]
        assert snapshot.snapshot_at.tzinfo == UTC

    def test_git_snapshot_serialization_roundtrip(self) -> None:
        snapshot = GitSnapshot(
            repo_path="/repo",
            is_dirty=False,
        )
        json_data = snapshot.model_dump_json()
        restored = GitSnapshot.model_validate_json(json_data)
        assert restored == snapshot


class TestProviderModel:
    """Tests for extensible ProviderId and ProviderCapabilities."""

    def test_valid_provider_ids(self) -> None:
        valid_ids = ["claude", "codex", "antigravity", "future-agent-x", "kimi_ai", "agent123"]
        for vid in valid_ids:
            p_id = ProviderId(vid)
            assert str(p_id) == vid

    def test_provider_id_case_normalization(self) -> None:
        p_id = ProviderId("  Claude  ")
        assert str(p_id) == "claude"

    def test_invalid_provider_id_fails(self) -> None:
        invalid_ids = ["", "   ", "agent with spaces", "agent!@#", "-leading-dash"]
        for iid in invalid_ids:
            with pytest.raises(ValueError):
                ProviderId(iid)

    def test_standard_provider_constants(self) -> None:
        assert PROVIDER_CLAUDE == "claude"
        assert PROVIDER_CODEX == "codex"
        assert PROVIDER_ANTIGRAVITY == "antigravity"

    def test_provider_capabilities(self) -> None:
        caps = ProviderCapabilities(
            provider_id=ProviderId("future-bot"),
            display_name="Future Bot Agent",
            supports_interactive=True,
            supports_headless=True,
            supports_native_resume=True,
            supports_mcp=True,
        )
        assert caps.provider_id == "future-bot"
        assert caps.supports_interactive is True
        assert caps.supports_mcp is True
        assert caps.supports_usage_metrics is False

    def test_provider_capabilities_serialization_roundtrip(self) -> None:
        caps = ProviderCapabilities(
            provider_id=PROVIDER_CLAUDE,
            display_name="Claude Code",
            supports_interactive=True,
            supports_headless=False,
            supports_native_resume=True,
        )
        json_data = caps.model_dump_json()
        restored = ProviderCapabilities.model_validate_json(json_data)
        assert restored == caps


class TestHandoffModel:
    """Tests for the canonical Handoff payload."""

    def test_handoff_canonical_fields(self) -> None:
        git_snap = GitSnapshot(
            repo_path="/path/to/repo",
            branch="main",
            head_sha="abcdef123456",
            is_dirty=False,
        )
        handoff = Handoff(
            task_id="task_xyz",
            project_name="CortexShift",
            original_objective="Build Phase 0 foundation",
            requirements=["Clean architecture", "Pydantic v2 models"],
            constraints=["No cloud DB", "No vendor conditionals"],
            completed=["Domain entities", "Ports", "Documentation"],
            current_work="Unit testing",
            remaining=["Integration test suite"],
            important_decisions=["Use Ports and Adapters"],
            files_touched=["src/cortexshift/domain/handoff.py"],
            test_status="25 tests passing",
            known_issues=[],
            git_state=git_snap,
            do_not_redo=["Do not introduce ORM"],
            recommended_next_action="Run pytest and verify mypy strict pass",
        )

        assert handoff.id.startswith("handoff_")
        assert handoff.project_name == "CortexShift"
        assert handoff.original_objective == "Build Phase 0 foundation"
        assert len(handoff.requirements) == 2
        assert len(handoff.completed) == 3
        assert handoff.current_work == "Unit testing"
        assert handoff.recommended_next_action == "Run pytest and verify mypy strict pass"
        assert handoff.created_at.tzinfo == UTC
        assert handoff.git_state is not None
        assert handoff.git_state.branch == "main"

    def test_handoff_serialization_roundtrip(self) -> None:
        handoff = Handoff(
            task_id="task_xyz",
            project_name="CortexShift",
            original_objective="Objective",
            requirements=["Req 1"],
            constraints=["Constraint 1"],
            completed=["Item 1"],
            remaining=["Item 2"],
            test_status="All passing",
            recommended_next_action="Next action",
        )
        json_data = handoff.model_dump_json()
        restored = Handoff.model_validate_json(json_data)
        assert restored == handoff


class TestDoctorDomainModels:
    """Tests for Doctor domain models."""

    def test_authentication_status_values(self) -> None:
        from cortexshift.domain.doctor import AuthenticationStatus

        assert AuthenticationStatus.AUTHENTICATED.value == "authenticated"
        assert AuthenticationStatus.NOT_AUTHENTICATED.value == "not_authenticated"
        assert AuthenticationStatus.UNKNOWN.value == "unknown"
        assert AuthenticationStatus.NOT_PROBED.value == "not_probed"

    def test_provider_diagnostic_properties_and_aliases(self) -> None:
        from cortexshift.domain.doctor import (
            AuthenticationStatus,
            DoctorReport,
            PlatformInfo,
            ProviderDiagnostic,
        )

        caps = ProviderCapabilities(
            provider_id=PROVIDER_CLAUDE,
            display_name="Claude Code",
        )
        diag = ProviderDiagnostic(
            provider_id=PROVIDER_CLAUDE,
            display_name="Claude Code",
            executable="claude",
            installed=True,
            resolved_path="/usr/bin/claude",
            version="2.0.0",
            authentication_status=AuthenticationStatus.AUTHENTICATED,
            capabilities=caps,
            diagnostics=["Notes"],
        )

        assert diag.id == PROVIDER_CLAUDE
        assert diag.authentication == AuthenticationStatus.AUTHENTICATED

        # Immutability
        with pytest.raises(ValidationError):
            diag.installed = False

        # DoctorReport serialization roundtrip
        platform_info = PlatformInfo(
            system="Darwin",
            release="24.0.0",
            machine="arm64",
            python_version="3.12.0",
        )
        report = DoctorReport(
            cortexshift_version="0.1.0",
            python_version="3.12.0",
            platform=platform_info,
            timestamp=datetime.now(UTC),
            providers=[diag],
        )

        dumped = report.model_dump_json()
        restored = DoctorReport.model_validate_json(dumped)
        assert restored == report

"""Comprehensive unit tests for all CortexShift domain models."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from cortexshift.domain import (
    HANDOFF_PROTOCOL_VERSION,
    PROVIDER_ANTIGRAVITY,
    PROVIDER_CLAUDE,
    PROVIDER_CODEX,
    Checkpoint,
    CheckpointPayload,
    GitSnapshot,
    HandoffFailureCode,
    HandoffGitState,
    HandoffPayload,
    HandoffRecord,
    HandoffSourceSession,
    HandoffStatus,
    HandoffTestStatus,
    Project,
    ProviderCapabilities,
    ProviderId,
    RepositoryInspectionStatus,
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
        assert restored.completed == ["JWT validation"]
        assert restored.remaining == ["Revocation list"]

    def test_task_completed_and_remaining_aliases(self) -> None:
        task = Task.model_validate(
            {
                "project_id": "proj_123",
                "title": "Aliases Test",
                "objective": "Verify aliases",
                "completed": ["Item 1", "Item 2"],
                "remaining": ["Item 3"],
            }
        )
        assert task.completed_items == ["Item 1", "Item 2"]
        assert task.completed == ["Item 1", "Item 2"]
        assert task.remaining_items == ["Item 3"]
        assert task.remaining == ["Item 3"]

        # Check model_dump contains both
        dump = task.model_dump()
        assert dump["completed"] == ["Item 1", "Item 2"]
        assert dump["remaining"] == ["Item 3"]

        # Check method aliases
        updated = task.add_completed(["Item 4"]).add_remaining(["Item 5"])
        assert updated.completed == ["Item 1", "Item 2", "Item 4"]
        assert updated.remaining == ["Item 3", "Item 5"]


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
    """Tests for the Checkpoint Protocol v1 domain entities."""

    @staticmethod
    def _make_payload() -> CheckpointPayload:
        from cortexshift.domain.checkpoint import (
            CheckpointGitState,
            CheckpointPayload,
            CheckpointTaskSnapshot,
            CheckpointTestProvenance,
            CheckpointTestStatus,
        )

        return CheckpointPayload(
            task=CheckpointTaskSnapshot(
                task_id="task_123",
                task_title="Test Task",
                task_status="in_progress",
                objective="Deliver Feature X",
                completed=["Created data models"],
                current_work="Writing unit tests",
                remaining=["Implement CLI command"],
            ),
            git_state=CheckpointGitState(
                status=RepositoryInspectionStatus.READY,
                available=True,
                note="Historical observation.",
                branch="main",
                dirty=True,
            ),
            files_touched=["src/cortexshift/domain/task.py"],
            decisions=["Use Pydantic v2"],
            test_status=CheckpointTestStatus(
                known=True,
                summary="15 passed in 0.2s",
                provenance=CheckpointTestProvenance.REPORTED,
            ),
            operator_note="Manual checkpoint during test",
        )

    def test_checkpoint_defaults(self) -> None:
        from cortexshift.domain.checkpoint import (
            CHECKPOINT_PROTOCOL_VERSION,
            CheckpointKind,
            CheckpointRecord,
        )

        payload = self._make_payload()
        checkpoint = Checkpoint(
            project_id="proj_123",
            task_id="task_123",
            kind=CheckpointKind.MANUAL,
            payload=payload,
        )
        assert checkpoint.id.startswith("cp_")
        assert checkpoint.protocol_version == CHECKPOINT_PROTOCOL_VERSION == 1
        assert checkpoint.task_id == "task_123"
        assert checkpoint.project_id == "proj_123"
        assert checkpoint.kind == CheckpointKind.MANUAL
        assert checkpoint.session_id is None
        assert checkpoint.git_snapshot_id is None
        assert checkpoint.payload.task.completed == ["Created data models"]
        assert checkpoint.payload.task.current_work == "Writing unit tests"
        assert checkpoint.payload.test_status.summary == "15 passed in 0.2s"
        assert checkpoint.created_at.tzinfo == UTC
        assert isinstance(checkpoint, CheckpointRecord)

    def test_checkpoint_serialization_roundtrip(self) -> None:
        from cortexshift.domain.checkpoint import CheckpointKind

        checkpoint = Checkpoint(
            project_id="proj_abc",
            task_id="task_abc",
            session_id="sess_123",
            git_snapshot_id="snap_123",
            kind=CheckpointKind.SESSION_END,
            payload=self._make_payload(),
        )
        json_data = checkpoint.model_dump_json()
        restored = Checkpoint.model_validate_json(json_data)
        assert restored == checkpoint
        assert restored.payload.decisions == ["Use Pydantic v2"]
        assert restored.payload.files_touched == ["src/cortexshift/domain/task.py"]


class TestGitSnapshotModel:
    """Tests for the GitSnapshot domain model."""

    def test_git_snapshot_creation(self) -> None:
        snapshot = GitSnapshot(  # type: ignore[call-arg]
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
        snapshot = GitSnapshot(  # type: ignore[call-arg]
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


class TestHandoffModels:
    """Tests for the canonical handoff payload and orchestration record."""

    @staticmethod
    def _source_session() -> HandoffSourceSession:
        return HandoffSourceSession(
            session_id="sess_source",
            provider_id=PROVIDER_CLAUDE,
            status=SessionStatus.COMPLETED,
            started_at=utc_now(),
            ended_at=utc_now(),
            exit_reason=SessionExitReason.NORMAL_COMPLETION,
            exit_code=0,
        )

    def _payload(self) -> HandoffPayload:
        return HandoffPayload(
            project_name="CortexShift",
            project_root="/path/to/repo",
            task_id="task_xyz",
            task_title="Phase 5 handoff",
            task_status="in_progress",
            original_objective="Build canonical manual handoff",
            requirements=["Clean architecture", "Pydantic v2 models"],
            constraints=["No cloud DB", "No vendor conditionals"],
            completed=["Domain entities", "Ports", "Documentation"],
            current_work="Unit testing",
            remaining=["Integration test suite"],
            important_decisions=[],
            decisions_known=False,
            files_touched=["src/cortexshift/domain/handoff.py"],
            test_status=HandoffTestStatus(known=False, summary="No verified test result recorded."),
            known_issues=[],
            git_state=HandoffGitState(
                status=RepositoryInspectionStatus.READY,
                available=True,
                note="Historical observation captured at handoff time.",
                branch="main",
                head_sha="abcdef123456",
                dirty=False,
                snapshot_id="snap_abc",
            ),
            do_not_redo=["Domain entities"],
            recommended_next_action="Run pytest and verify mypy strict pass",
            source_session=self._source_session(),
            target_provider_id=PROVIDER_CODEX,
        )

    def test_payload_canonical_fields(self) -> None:
        payload = self._payload()

        assert payload.protocol_version == HANDOFF_PROTOCOL_VERSION == 1
        assert payload.project_name == "CortexShift"
        assert payload.original_objective == "Build canonical manual handoff"
        assert len(payload.requirements) == 2
        assert len(payload.completed) == 3
        assert payload.current_work == "Unit testing"
        assert payload.recommended_next_action == "Run pytest and verify mypy strict pass"
        assert payload.generated_at.tzinfo == UTC
        assert payload.git_state.branch == "main"
        assert payload.git_state.snapshot_id == "snap_abc"
        assert payload.source_session.provider_id == PROVIDER_CLAUDE
        assert payload.target_provider_id == PROVIDER_CODEX

    def test_unknown_state_is_encoded_honestly(self) -> None:
        payload = self._payload()

        assert payload.decisions_known is False
        assert payload.important_decisions == []
        assert payload.test_status.known is False

    def test_record_wraps_payload_with_orchestration_metadata(self) -> None:
        record = HandoffRecord(
            project_id="proj_xyz",
            task_id="task_xyz",
            source_session_id="sess_source",
            source_provider_id=PROVIDER_CLAUDE,
            target_provider_id=PROVIDER_CODEX,
            git_snapshot_id="snap_abc",
            payload=self._payload(),
        )

        assert record.id.startswith("handoff_")
        assert record.protocol_version == 1
        assert record.status == HandoffStatus.PREPARED
        assert record.delivered_at is None
        assert record.failure_code is None
        assert record.target_session_id is None
        assert record.created_at.tzinfo == UTC

    def test_record_delivery_transitions(self) -> None:
        record = HandoffRecord(
            project_id="proj_xyz",
            task_id="task_xyz",
            source_session_id="sess_source",
            source_provider_id=PROVIDER_CLAUDE,
            target_provider_id=PROVIDER_CODEX,
            payload=self._payload(),
        )

        delivered = record.mark_delivered(target_session_id="sess_target")
        assert delivered.status == HandoffStatus.DELIVERED
        assert delivered.target_session_id == "sess_target"
        assert delivered.delivered_at is not None
        assert delivered.failure_code is None

        failed = record.mark_failed(HandoffFailureCode.BOOTSTRAP_TIMEOUT)
        assert failed.status == HandoffStatus.FAILED
        assert failed.failure_code == HandoffFailureCode.BOOTSTRAP_TIMEOUT

    def test_record_serialization_roundtrip(self) -> None:
        record = HandoffRecord(
            project_id="proj_xyz",
            task_id="task_xyz",
            source_session_id="sess_source",
            source_provider_id=PROVIDER_CLAUDE,
            target_provider_id=PROVIDER_CODEX,
            payload=self._payload(),
        )
        restored = HandoffRecord.model_validate_json(record.model_dump_json())
        assert restored == record
        assert restored.payload.git_state.status == RepositoryInspectionStatus.READY


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

"""The facade aggregates services without adding rules of its own."""

import json
from pathlib import Path

import pytest

from cortexshift.adapters.providers.antigravity import ANTIGRAVITY_MCP_CONFIG_REL_PATH
from cortexshift.application.checkpoint_service import CheckpointService
from cortexshift.application.handoff_service import HandoffService
from cortexshift.domain.checkpoint import CheckpointKind, CheckpointTestProvenance
from cortexshift.domain.errors import CortexShiftError, ProjectNotInitializedError
from cortexshift.domain.provider import PROVIDER_CLAUDE, PROVIDER_CODEX
from cortexshift.domain.session import SessionExitReason, SessionStatus
from cortexshift.tui.facade import TuiFacade
from cortexshift.tui.models import abbreviate_id, format_relative, truncate
from tests.tui.conftest import build_facade, seed_project, seed_session


def test_resolving_an_uninitialized_directory_fails_cleanly(tmp_path: Path) -> None:
    with pytest.raises(ProjectNotInitializedError):
        TuiFacade.resolve(tmp_path)


def test_the_facade_binds_to_one_resolved_project_root(project: Path) -> None:
    facade = build_facade(project)
    assert facade.project_root == project.resolve()


def test_load_state_assembles_every_read_model(project: Path) -> None:
    facade = build_facade(project)
    task = facade.load_state().active_task
    assert task is not None

    seed_session(project, task.id, provider_id=PROVIDER_CLAUDE, native_session_id="uuid-a")
    CheckpointService().create_checkpoint(note="A milestone", start_dir=project)

    snapshot = facade.load_state()

    assert snapshot.project.name == "Luna"
    assert snapshot.project.schema_version == 6
    assert snapshot.active_task is not None
    assert snapshot.active_task.title == "Implement screen understanding"
    assert len(snapshot.tasks) == 1
    assert snapshot.tasks[0].is_active is True
    assert len(snapshot.sessions) == 1
    assert len(snapshot.checkpoints) == 1
    assert snapshot.activity.latest_session is not None
    assert snapshot.activity.latest_checkpoint is not None
    assert snapshot.loaded_at is not None


def test_load_state_never_inspects_git(project: Path) -> None:
    """The lightweight path must not spawn a subprocess."""

    class ExplodingRepositoryService:
        def inspect_repository(self, start_path: Path | None = None) -> None:
            raise AssertionError("load_state must not inspect the repository")

    facade = build_facade(project, repository_service=ExplodingRepositoryService())
    assert facade.load_state().project.name == "Luna"


def test_inspect_repository_returns_a_live_read_model(project: Path) -> None:
    facade = build_facade(project)
    repository = facade.inspect_repository()

    assert repository.ready
    assert repository.branch == "main"
    assert repository.dirty is True
    assert "untracked.py" in repository.untracked_files
    assert repository.changed_file_count == 2
    assert repository.observed_at is not None


def test_progress_has_no_denominator_without_structured_items(tmp_path: Path) -> None:
    root = tmp_path / "empty"
    root.mkdir()
    seed_project(root, completed=None, remaining=None, current_work=None)

    task = build_facade(root).load_state().active_task
    assert task is not None
    assert task.progress.has_denominator is False
    assert task.progress.percent is None
    assert task.progress.fraction is None


def test_progress_is_computed_only_from_canonical_items(project: Path) -> None:
    task = build_facade(project).load_state().active_task
    assert task is not None
    assert task.progress.completed_count == 1
    assert task.progress.remaining_count == 2
    assert task.progress.percent == 33


def test_mark_completed_uses_the_canonical_domain_rule(project: Path) -> None:
    facade = build_facade(project)
    updated = facade.mark_completed(["Wire the parser"])

    assert "Wire the parser" in updated.completed
    assert "Wire the parser" not in updated.remaining
    assert updated.status.value == "in_progress"

    # Deduplicated on repeat, and idempotent.
    again = facade.mark_completed(["Wire the parser"])
    assert again.completed.count("Wire the parser") == 1


def test_create_checkpoint_records_reported_provenance(project: Path) -> None:
    facade = build_facade(project)

    with_tests = facade.create_checkpoint(test_summary="12 passed")
    assert with_tests.kind is CheckpointKind.MANUAL
    assert with_tests.payload.test_status.provenance is CheckpointTestProvenance.REPORTED

    without_tests = facade.create_checkpoint(note="No test claim")
    assert without_tests.payload.test_status.known is False
    assert without_tests.payload.test_status.provenance is CheckpointTestProvenance.UNKNOWN


def test_resumable_sessions_apply_the_canonical_eligibility_rule(project: Path) -> None:
    facade = build_facade(project)
    task = facade.load_state().active_task
    assert task is not None

    resumable = seed_session(
        project, task.id, provider_id=PROVIDER_CODEX, native_session_id="codex-thread-1"
    )
    # A spawn failure is never resumable, even with an ID recorded.
    seed_session(
        project,
        task.id,
        provider_id=PROVIDER_CODEX,
        native_session_id="codex-thread-2",
        status=SessionStatus.FAILED,
        exit_reason=SessionExitReason.SPAWN_FAILED,
        exit_code=127,
    )
    # An unfinished invocation is not resumable either.
    seed_session(
        project,
        task.id,
        provider_id=PROVIDER_CODEX,
        native_session_id="codex-thread-3",
        status=SessionStatus.RUNNING,
        exit_reason=None,
        exit_code=None,
    )

    eligible = facade.resumable_sessions("codex")
    assert [session.id for session in eligible] == [resumable.id]


def test_provider_availability_is_resolved_through_the_registry(project: Path) -> None:
    facade = build_facade(project)
    assert facade.provider_available("claude") is True
    assert facade.provider_available("antigravity") is False
    assert facade.provider_available("not-a-provider") is False


def test_supported_providers_come_from_the_runtime_registry(project: Path) -> None:
    assert set(build_facade(project).supported_providers()) == {
        "claude",
        "codex",
        "antigravity",
    }


def test_mcp_status_reports_integration_without_starting_a_server(project: Path) -> None:
    status = build_facade(project).mcp_status()

    assert status.sdk_available is True
    assert status.transport == "stdio (local only)"
    assert status.antigravity_configured is False
    assert len(status.read_tools) == 4
    assert len(status.write_tools) == 6
    assert "cortexshift://task" in status.resources


def test_antigravity_mcp_setup_preserves_unrelated_servers(project: Path) -> None:
    config_path = project / ANTIGRAVITY_MCP_CONFIG_REL_PATH
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({"mcpServers": {"other": {"command": "other"}}, "extra": True}),
        encoding="utf-8",
    )

    facade = build_facade(project)
    result = facade.configure_antigravity_mcp()

    assert result["changed"] is True
    written = json.loads(config_path.read_text(encoding="utf-8"))
    assert written["mcpServers"]["other"] == {"command": "other"}
    assert written["mcpServers"]["cortexshift"]["args"] == ["mcp", "serve"]
    assert written["extra"] is True
    assert facade.mcp_status().antigravity_configured is True


def test_antigravity_mcp_setup_refuses_to_overwrite_a_conflict(project: Path) -> None:
    config_path = project / ANTIGRAVITY_MCP_CONFIG_REL_PATH
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({"mcpServers": {"cortexshift": {"command": "something-else"}}}),
        encoding="utf-8",
    )

    facade = build_facade(project)
    with pytest.raises(CortexShiftError) as excinfo:
        facade.configure_antigravity_mcp()

    assert "--force" in str(excinfo.value)
    # Nothing was silently forced.
    written = json.loads(config_path.read_text(encoding="utf-8"))
    assert written["mcpServers"]["cortexshift"] == {"command": "something-else"}


def test_antigravity_mcp_dry_run_writes_nothing(project: Path) -> None:
    facade = build_facade(project)
    result = facade.configure_antigravity_mcp(dry_run=True)

    assert result["dry_run"] is True
    assert not (project / ANTIGRAVITY_MCP_CONFIG_REL_PATH).exists()


def test_handoff_preview_persists_nothing(project: Path) -> None:
    facade = build_facade(project)
    task = facade.load_state().active_task
    assert task is not None
    seed_session(project, task.id, provider_id=PROVIDER_CLAUDE)

    preview = facade.preview_handoff("codex")

    assert preview.target_provider_id == "codex"
    assert preview.context_characters > 0
    assert "Implement screen understanding" in preview.rendered_context
    assert HandoffService().list_handoffs(project) == []


def test_switch_preview_reports_checkpoint_enrichment(project: Path) -> None:
    facade = build_facade(project)
    task = facade.load_state().active_task
    assert task is not None
    seed_session(project, task.id, provider_id=PROVIDER_CLAUDE)
    CheckpointService().create_checkpoint(
        decisions=["Chose Textual"], test_summary="9 passed", start_dir=project
    )

    preview = facade.preview_switch("codex")

    assert preview.target_provider_id == "codex"
    assert preview.source_provider_id == "claude"
    assert "1 decision(s)" in preview.checkpoint_enrichment
    assert "reported (unverified)" in preview.checkpoint_enrichment
    assert HandoffService().list_handoffs(project) == []


# --- presentation helpers -------------------------------------------------


def test_ids_are_abbreviated_for_tables_but_never_mangled() -> None:
    assert abbreviate_id("task_a1b2c3d4e5f6") == "task_a1b2c3d4…"
    # Short identifiers are shown in full.
    assert abbreviate_id("task_short") == "task_short"
    assert abbreviate_id("") == "—"
    # A prefix-less value still abbreviates safely.
    assert abbreviate_id("0123456789abcdef") == "01234567…"


def test_relative_times_are_display_only() -> None:
    from datetime import timedelta

    from cortexshift.domain.identifiers import utc_now

    now = utc_now()
    assert format_relative(None) == "—"
    assert format_relative(now, now=now) == "just now"
    assert format_relative(now - timedelta(minutes=2), now=now) == "2m ago"
    assert format_relative(now - timedelta(hours=3), now=now) == "3h ago"
    assert format_relative(now - timedelta(days=4), now=now) == "4d ago"
    assert format_relative(now - timedelta(days=60), now=now) == "2mo ago"
    assert format_relative(now - timedelta(days=800), now=now) == "2y ago"


def test_truncate_bounds_display_without_touching_canonical_text() -> None:
    assert truncate(None, 10) == "—"
    assert truncate("short", 10) == "short"
    assert truncate("a very long sentence indeed", 10).endswith("…")
    assert len(truncate("a very long sentence indeed", 10)) == 10
    # Whitespace is collapsed for dense rendering.
    assert truncate("two   spaces", 40) == "two spaces"

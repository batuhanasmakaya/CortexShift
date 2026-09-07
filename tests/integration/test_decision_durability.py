"""Integration tests reproducing the real managed-Codex decision-durability failure.

Observed sequence: `record_decision` minted checkpoint D holding the decision, an ordinary
`create_checkpoint` then minted E with no decisions, and a session-end checkpoint minted F
with none either. Because the handoff read only the newest checkpoint, it reported
`decisions_known = false` and stated that no structured decisions were recorded — over a
decision CortexShift was in fact holding. These tests drive the real MCP facade, the real
store, and the real SwitchService handoff path.
"""

from pathlib import Path

from cortexshift.adapters.git.inspector import GitRepositoryInspector
from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.adapters.workspace_lease import FileWorkspaceLeaseManager
from cortexshift.application.checkpoint_service import CheckpointService
from cortexshift.application.handoff_builder import UNKNOWN_DECISIONS_STATEMENT
from cortexshift.application.switch_service import SwitchService
from cortexshift.domain.checkpoint import (
    CHECKPOINT_TRIGGER_DECISION,
    CHECKPOINT_TRIGGER_KEY,
    CheckpointKind,
)
from cortexshift.domain.handoff import HandoffPayload
from cortexshift.domain.project import Project
from cortexshift.domain.provider import PROVIDER_CLAUDE
from cortexshift.domain.session import Session, SessionStatus
from cortexshift.domain.task import Task
from cortexshift.mcp.context import McpExecutionContext
from cortexshift.mcp.facade import McpApplicationFacade
from tests.factories import seed_project, seed_session
from tests.unit.test_switch_service import FakeInspector, FakeProcessRunner

DECISION = "Real Codex E2E confirmed writable CortexShift MCP context."


def _managed_facade(
    root: Path,
    store: SQLiteStateStore,
    project_id: str,
    task_id: str,
    session: Session,
) -> McpApplicationFacade:
    """Build a managed, writable MCP facade bound to one session, as a provider would."""
    context = McpExecutionContext(
        project_root=root,
        project_id=project_id,
        task_id=task_id,
        session_id=session.id,
        provider_id=session.provider_id,
        managed_session=True,
        read_only=False,
    )
    return McpApplicationFacade(context, store)


def _preview_handoff(root: Path) -> HandoffPayload:
    """Build the canonical handoff through the real SwitchService, mutating nothing."""
    service = SwitchService(
        inspector=FakeInspector(),
        process_runner=FakeProcessRunner(),
        lease_manager=FileWorkspaceLeaseManager(),
        which_fn=lambda cmd: f"/bin/{cmd}",
        is_tty_fn=lambda: True,
    )
    return service.preview("codex", start_dir=root).payload


def _rendered_handoff(root: Path) -> str:
    service = SwitchService(
        inspector=FakeInspector(),
        process_runner=FakeProcessRunner(),
        lease_manager=FileWorkspaceLeaseManager(),
        which_fn=lambda cmd: f"/bin/{cmd}",
        is_tty_fn=lambda: True,
    )
    return service.preview("codex", start_dir=root).rendered_context


def _seed(tmp_path: Path) -> tuple[Project, Task, Session]:
    project, task = seed_project(tmp_path)
    session = seed_session(tmp_path, task.id, provider_id=PROVIDER_CLAUDE)
    return project, task, session


def _store(tmp_path: Path) -> SQLiteStateStore:
    return SQLiteStateStore(tmp_path / ".cortexshift" / "state.sqlite3", auto_migrate=False)


# --- The reported failure ---


def test_decision_survives_a_later_ordinary_checkpoint(tmp_path: Path) -> None:
    """Case A: record_decision then create_checkpoint(no decisions) -- handoff keeps it."""
    project, task, session = _seed(tmp_path)
    store = _store(tmp_path)
    facade = _managed_facade(tmp_path, store, project.id, task.id, session)

    decision_cp_id = facade.record_decision(DECISION).checkpoint_id
    later = facade.create_checkpoint(note="Real Codex managed MCP provenance retest.")
    later_cp_id = later.checkpoint_id
    store.close()

    payload = _preview_handoff(tmp_path)

    assert payload.decisions_known is True
    assert payload.important_decisions == [DECISION]

    # Case H: neither checkpoint was rewritten; each stays a literal point-in-time record.
    verify = _store(tmp_path)
    decision_cp = verify.get_checkpoint(decision_cp_id)
    later_cp = verify.get_checkpoint(later_cp_id)
    assert decision_cp is not None and later_cp is not None
    assert decision_cp.payload.decisions == [DECISION]
    assert later_cp.payload.decisions == []
    verify.close()


def test_decision_survives_the_automatic_session_end_checkpoint(tmp_path: Path) -> None:
    """Case B: the session-end checkpoint must not bury the session's own decision."""
    project, task, session = _seed(tmp_path)
    store = _store(tmp_path)
    facade = _managed_facade(tmp_path, store, project.id, task.id, session)
    facade.record_decision(DECISION)

    ended = session.model_copy(update={"status": SessionStatus.COMPLETED})
    checkpoint_service = CheckpointService(inspector=GitRepositoryInspector())
    session_end = checkpoint_service.capture_session_end_checkpoint(session=ended, store=store)
    assert session_end is not None
    assert session_end.kind == CheckpointKind.SESSION_END
    assert session_end.payload.decisions == []
    latest = store.get_latest_checkpoint(task.id)
    assert latest is not None and latest.id == session_end.id
    store.close()

    payload = _preview_handoff(tmp_path)

    assert payload.decisions_known is True
    assert payload.important_decisions == [DECISION]


def test_handoff_text_no_longer_denies_a_recorded_decision(tmp_path: Path) -> None:
    """The rendered package must not claim absence over a decision it is holding."""
    project, task, session = _seed(tmp_path)
    store = _store(tmp_path)
    facade = _managed_facade(tmp_path, store, project.id, task.id, session)
    facade.record_decision(DECISION)
    facade.create_checkpoint(note="Later milestone with no decisions")
    store.close()

    rendered = _rendered_handoff(tmp_path)

    assert DECISION in rendered
    assert UNKNOWN_DECISIONS_STATEMENT not in rendered


# --- Aggregation semantics through the real stack ---


def test_multiple_recorded_decisions_appear_in_chronological_order(tmp_path: Path) -> None:
    """Case C: every decision recorded during the task reaches the handoff, oldest first."""
    project, task, session = _seed(tmp_path)
    store = _store(tmp_path)
    facade = _managed_facade(tmp_path, store, project.id, task.id, session)

    facade.record_decision("First: adopt SQLite WAL mode")
    facade.create_checkpoint(decisions=["Second: enforce pure stdout wire protocol"])
    facade.record_decision("Third: keep checkpoints immutable")
    facade.create_checkpoint(note="Milestone with no decisions of its own")
    store.close()

    payload = _preview_handoff(tmp_path)

    assert payload.important_decisions == [
        "First: adopt SQLite WAL mode",
        "Second: enforce pure stdout wire protocol",
        "Third: keep checkpoints immutable",
    ]


def test_redundantly_recorded_decision_appears_once(tmp_path: Path) -> None:
    """Case D: re-recording the same decision does not duplicate it in the handoff."""
    project, task, session = _seed(tmp_path)
    store = _store(tmp_path)
    facade = _managed_facade(tmp_path, store, project.id, task.id, session)

    facade.record_decision(DECISION)
    facade.create_checkpoint(decisions=[DECISION, "A distinct decision"])
    facade.record_decision(DECISION)
    store.close()

    payload = _preview_handoff(tmp_path)

    assert payload.important_decisions == [DECISION, "A distinct decision"]


def test_task_without_decisions_still_reports_absence_honestly(tmp_path: Path) -> None:
    """Case G: a genuinely empty decision history keeps the honest unknown statement."""
    project, task, session = _seed(tmp_path)
    store = _store(tmp_path)
    facade = _managed_facade(tmp_path, store, project.id, task.id, session)
    facade.create_checkpoint(note="Milestone with no decisions", test_summary="12 passed")
    store.close()

    service = SwitchService(
        inspector=FakeInspector(),
        process_runner=FakeProcessRunner(),
        lease_manager=FileWorkspaceLeaseManager(),
        which_fn=lambda cmd: f"/bin/{cmd}",
        is_tty_fn=lambda: True,
    )
    result = service.preview("codex", start_dir=tmp_path)

    assert result.payload.decisions_known is False
    assert result.payload.important_decisions == []
    assert UNKNOWN_DECISIONS_STATEMENT in result.rendered_context


def test_decisions_never_cross_task_boundaries(tmp_path: Path) -> None:
    """Case F: another task's decisions in the same project never enter this handoff."""
    project, task, session = _seed(tmp_path)
    store = _store(tmp_path)

    other_task = Task(project_id=project.id, title="Other", objective="Other objective")
    store.save_task(other_task)
    other_session = Session(task_id=other_task.id, provider_id=PROVIDER_CLAUDE)
    store.save_session(other_session)

    other_facade = _managed_facade(tmp_path, store, project.id, other_task.id, other_session)
    other_facade.record_decision("Belongs strictly to the other task")

    facade = _managed_facade(tmp_path, store, project.id, task.id, session)
    facade.record_decision(DECISION)
    store.close()

    payload = _preview_handoff(tmp_path)

    assert payload.task_id == task.id
    assert payload.important_decisions == [DECISION]
    assert "Belongs strictly to the other task" not in payload.important_decisions


# --- record_decision provenance marker (ADR-0009) ---


def test_record_decision_persists_structured_trigger_marker(tmp_path: Path) -> None:
    """ADR-0009 specifies trigger="decision"; it rides in existing metadata mappings."""
    project, task, session = _seed(tmp_path)
    store = _store(tmp_path)
    facade = _managed_facade(tmp_path, store, project.id, task.id, session)

    decision_id = facade.record_decision(DECISION).checkpoint_id
    plain_id = facade.create_checkpoint(note="Ordinary milestone").checkpoint_id
    store.close()

    verify = _store(tmp_path)
    decision_cp = verify.get_checkpoint(decision_id)
    plain_cp = verify.get_checkpoint(plain_id)
    assert decision_cp is not None and plain_cp is not None

    assert decision_cp.metadata[CHECKPOINT_TRIGGER_KEY] == CHECKPOINT_TRIGGER_DECISION
    assert decision_cp.payload.metadata[CHECKPOINT_TRIGGER_KEY] == CHECKPOINT_TRIGGER_DECISION
    # Checkpoints from other paths stay unmarked, and the marker changes nothing else.
    assert CHECKPOINT_TRIGGER_KEY not in plain_cp.metadata
    assert decision_cp.protocol_version == plain_cp.protocol_version == 1
    verify.close()


def test_aggregation_does_not_depend_on_the_trigger_marker(tmp_path: Path) -> None:
    """Decisions supplied through create_checkpoint aggregate just the same."""
    project, task, session = _seed(tmp_path)
    store = _store(tmp_path)
    facade = _managed_facade(tmp_path, store, project.id, task.id, session)

    facade.create_checkpoint(decisions=["Recorded without a decision trigger"])
    facade.create_checkpoint(note="Newer milestone with none")
    store.close()

    payload = _preview_handoff(tmp_path)

    assert payload.decisions_known is True
    assert payload.important_decisions == ["Recorded without a decision trigger"]

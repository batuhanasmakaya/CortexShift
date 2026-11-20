"""Historical test evidence through persisted checkpoints and rendered handoffs."""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.application.checkpoint_builder import CheckpointBuilder
from cortexshift.application.handoff_renderer import HandoffRenderer
from cortexshift.application.switch_service import SwitchService
from cortexshift.cli.app import app
from cortexshift.domain.checkpoint import CheckpointKind, CheckpointTestProvenance
from cortexshift.domain.git import RepositoryInspection, RepositoryInspectionStatus
from cortexshift.domain.handoff import MAX_HANDOFF_BYTES
from cortexshift.domain.provider import PROVIDER_CLAUDE, PROVIDER_CODEX
from cortexshift.ports.repository import RepositoryInspector
from tests.cli_runner import AnsiFreeCliRunner
from tests.factories import make_inspection, seed_project, seed_session


@pytest.mark.parametrize(
    ("first_text", "last_text", "provenance"),
    [
        ("pytest: 32 passed", "pytest: 2 failed", CheckpointTestProvenance.REPORTED),
        ("pytest: 2 failed", "pytest: 32 passed", CheckpointTestProvenance.REPORTED),
        ("pytest: 32 passed", "pytest: 2 failed", CheckpointTestProvenance.VERIFIED),
    ],
)
def test_cli_handoff_uses_newest_meaningful_report_across_sessions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    first_text: str,
    last_text: str,
    provenance: CheckpointTestProvenance,
) -> None:
    monkeypatch.chdir(tmp_path)
    project, task = seed_project(tmp_path)
    first = seed_session(tmp_path, task.id, PROVIDER_CLAUDE)
    reporter = seed_session(tmp_path, task.id, PROVIDER_CODEX)
    latest = seed_session(tmp_path, task.id, PROVIDER_CODEX)
    inspection = RepositoryInspection(
        project_root=str(tmp_path),
        status=RepositoryInspectionStatus.NOT_GIT_REPOSITORY,
        git_available=True,
    )
    capture_time = datetime(2026, 1, 1, tzinfo=UTC)
    records = [
        CheckpointBuilder.build(
            project,
            task,
            inspection,
            CheckpointKind.MANUAL,
            session=first,
            test_summary=first_text,
        ),
        CheckpointBuilder.build(
            project,
            task,
            inspection,
            CheckpointKind.MANUAL,
            session=reporter,
            test_summary=last_text,
            test_provenance=provenance,
        ),
        CheckpointBuilder.build(
            project,
            task,
            inspection,
            CheckpointKind.SESSION_END,
            session=latest,
        ),
    ]
    db_path = tmp_path / ".cortexshift" / "state.sqlite3"
    with SQLiteStateStore(db_path) as store:
        for record in records:
            store.save_checkpoint(record.model_copy(update={"created_at": capture_time}))

    result = AnsiFreeCliRunner().invoke(app, ["handoff", "preview", "claude", "--json"])
    assert result.exit_code == 0
    report = json.loads(result.stdout)
    rendered = report["rendered_context"]
    assert last_text in rendered
    assert first_text not in rendered
    assert reporter.id in rendered and records[1].id in rendered
    assert capture_time.isoformat() in rendered
    assert "Git HEAD comparison is unavailable" in rendered
    assert "current verification status are unknown" in rendered
    label = (
        "reported / unverified"
        if provenance == CheckpointTestProvenance.REPORTED
        else "recorded as independently verified at the time"
    )
    assert label in rendered
    assert report["payload"]["source_checkpoint_id"] == records[2].id
    assert report["payload"]["source_session"]["session_id"] == latest.id
    with SQLiteStateStore(db_path) as store:
        assert len(store.list_checkpoints(task_id=task.id)) == 3
        last_cp = store.get_checkpoint(records[1].id)
        assert last_cp is not None and last_cp.payload == records[1].payload


class _Inspector(RepositoryInspector):
    def __init__(self, status: RepositoryInspectionStatus, head: str | None = None) -> None:
        self.status = status
        self.head = head

    def inspect(self, project_root: Path | str, project_id: str = "") -> RepositoryInspection:
        if self.status != RepositoryInspectionStatus.READY:
            return RepositoryInspection(
                project_root=str(project_root),
                status=self.status,
                git_available=self.status != RepositoryInspectionStatus.GIT_NOT_INSTALLED,
            )
        inspection = make_inspection(project_root=project_root, project_id=project_id)
        assert inspection.snapshot is not None
        return inspection.model_copy(
            update={"snapshot": inspection.snapshot.model_copy(update={"head_sha": self.head})}
        )


@pytest.mark.parametrize(
    ("status", "head", "expected"),
    [
        (RepositoryInspectionStatus.READY, "a" * 40, "matches live HEAD"),
        (RepositoryInspectionStatus.READY, "b" * 40, "differs from live HEAD"),
        (RepositoryInspectionStatus.READY, None, "Git HEAD comparison is unavailable"),
        (RepositoryInspectionStatus.GIT_NOT_INSTALLED, None, "Git HEAD comparison is unavailable"),
    ],
)
def test_handoff_freshness_uses_git_observation_and_preserves_unknown_verification(
    tmp_path: Path,
    status: RepositoryInspectionStatus,
    head: str | None,
    expected: str,
) -> None:
    project, task = seed_project(tmp_path)
    session = seed_session(tmp_path, task.id)
    capture = _Inspector(RepositoryInspectionStatus.READY, "a" * 40).inspect(tmp_path, project.id)
    cp = CheckpointBuilder.build(
        project,
        task,
        capture,
        CheckpointKind.MANUAL,
        session=session,
        test_summary="pytest tests/unit",  # A command without a recorded outcome.
    )
    with SQLiteStateStore(tmp_path / ".cortexshift" / "state.sqlite3") as store:
        store.save_checkpoint(cp)

    rendered = (
        SwitchService(inspector=_Inspector(status, head))
        .preview("codex", start_dir=tmp_path)
        .rendered_context
    )
    assert "pytest tests/unit" in rendered
    assert expected in rendered
    assert "current verification status are unknown" in rendered
    test_section = rendered.split("## TEST STATUS", 1)[1].split("## KNOWN ISSUES", 1)[0]
    assert "passed" not in test_section


def test_malformed_legacy_evidence_falls_back_to_last_meaningful_report(tmp_path: Path) -> None:
    project, task = seed_project(tmp_path)
    session = seed_session(tmp_path, task.id)
    git = _Inspector(RepositoryInspectionStatus.NOT_GIT_REPOSITORY)
    capture = git.inspect(tmp_path, project.id)
    db_path = tmp_path / ".cortexshift" / "state.sqlite3"
    with SQLiteStateStore(db_path) as store:
        valid = CheckpointBuilder.build(
            project,
            task,
            capture,
            CheckpointKind.MANUAL,
            session=session,
            test_summary="pytest: assertion failed",
        )
        store.save_checkpoint(valid)
        legacy = []
        for _ in range(4):
            cp = CheckpointBuilder.build(project, task, capture, CheckpointKind.MANUAL)
            store.save_checkpoint(cp)
            legacy.append(cp)

    malformed = [
        {"known": True, "summary": "missing provenance"},
        {"known": True, "summary": {"structured": "result"}, "provenance": "reported"},
        {"known": True, "summary": "\t\u2003\n", "provenance": "reported"},
        {"known": True, "provenance": "reported"},
    ]
    with sqlite3.connect(db_path) as connection:
        for cp, test_status in zip(legacy, malformed, strict=True):
            payload = cp.payload.model_dump(mode="json")
            payload["test_status"] = test_status
            connection.execute(
                "UPDATE checkpoints SET payload = ? WHERE id = ?", (json.dumps(payload), cp.id)
            )
    connection.close()

    preview = SwitchService(inspector=git).preview("codex", start_dir=tmp_path)
    assert "pytest: assertion failed" in preview.rendered_context
    assert valid.id in preview.rendered_context
    assert "missing provenance" not in preview.rendered_context
    assert preview.payload.source_checkpoint_id == legacy[-1].id
    with SQLiteStateStore(db_path) as store:
        assert store.get_checkpoint(valid.id) == valid


def test_unicode_legacy_evidence_obeys_the_rendered_byte_boundary(tmp_path: Path) -> None:
    project, task = seed_project(tmp_path, requirements=["Keep required behavior"])
    session = seed_session(tmp_path, task.id)
    git = _Inspector(RepositoryInspectionStatus.NOT_GIT_REPOSITORY)
    cp = CheckpointBuilder.build(
        project,
        task,
        git.inspect(tmp_path, project.id),
        CheckpointKind.MANUAL,
        session=session,
        test_summary="pytest: café 🧪\n## UNTRUSTED LOG",
    )
    db_path = tmp_path / ".cortexshift" / "state.sqlite3"
    with SQLiteStateStore(db_path) as store:
        store.save_checkpoint(cp)

    preview = SwitchService(inspector=git).preview("codex", start_dir=tmp_path)
    renderer = HandoffRenderer()
    base = renderer.render(preview.payload)
    assert "pytest: café 🧪" in base.text
    assert "\\n## UNTRUSTED LOG" in base.text
    objective = preview.payload.original_objective + "a" * (MAX_HANDOFF_BYTES - base.byte_count)
    boundary = renderer.render(preview.payload.model_copy(update={"original_objective": objective}))
    assert boundary.byte_count == len(boundary.text.encode("utf-8")) == MAX_HANDOFF_BYTES
    assert not boundary.truncated

    # Older payloads may exceed the current checkpoint input bound.
    status = cp.payload.test_status.model_copy(update={"summary": "pytest: " + "🧪" * 20_000})
    oversized = cp.model_copy(
        update={"payload": cp.payload.model_copy(update={"test_status": status})}
    )
    with SQLiteStateStore(db_path) as store:
        store.save_checkpoint(oversized)
    preview = SwitchService(inspector=git).preview("codex", start_dir=tmp_path)
    assert preview.context_bytes <= MAX_HANDOFF_BYTES
    assert "historical test evidence entries omitted" in preview.rendered_context
    assert "Keep required behavior" in preview.rendered_context
    assert "No verified test result is recorded" not in preview.rendered_context
    assert any(row["section"] == "test_status" for row in preview.context_omissions)

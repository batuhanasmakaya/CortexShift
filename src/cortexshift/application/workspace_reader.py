"""Read-oriented workspace queries over a single verified project snapshot."""

from pathlib import Path
from typing import Any

from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.application.handoff_builder import unique_checkpoint_decisions
from cortexshift.application.native_session import is_native_resumable, native_capabilities
from cortexshift.application.run_service import ProviderRuntimeRegistry
from cortexshift.application.status_service import ProjectStatusService
from cortexshift.application.task_service import TaskService
from cortexshift.application.workspace import WorkspaceIdentity, read_workspace
from cortexshift.domain.errors import (
    CheckpointNotFoundError,
    HandoffNotFoundError,
    TaskNotFoundError,
)
from cortexshift.domain.session import UNFINALIZED_SESSION_STATUSES, Session
from cortexshift.domain.task import Task

PAGE_SIZE = 20
OBJECTIVE_CHARS = 8192


def excerpt(text: str, limit: int = 240) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


def text_items(values: list[str]) -> dict[str, Any]:
    """Keep one detail frame bounded and disclose omitted or shortened content."""
    items = [excerpt(value, 400) for value in values[:16]]
    return {
        "items": items,
        "total": len(values),
        "truncated": any(len(value) > 400 for value in values[:16]),
    }


def page(items: list[dict[str, Any]], offset: int) -> dict[str, Any]:
    return {
        "items": items[:PAGE_SIZE],
        "next": offset + PAGE_SIZE if len(items) > PAGE_SIZE else None,
    }


class WorkspaceReader:
    """Composition of existing Tasks, Session evidence and workspace identity."""

    def __init__(self) -> None:
        self._status = ProjectStatusService()
        self._providers = ProviderRuntimeRegistry()

    @staticmethod
    def _project(identity: WorkspaceIdentity) -> dict[str, Any]:
        return {
            "project_id": identity.project.id,
            "root": str(identity.root),
            "name": excerpt(identity.project.name),
        }

    def verify(self, root: Path) -> dict[str, Any]:
        with read_workspace(root) as (identity, _):
            return self._project(identity)

    def overview(self, root: Path, project_id: str) -> dict[str, Any]:
        with read_workspace(root, expected_project_id=project_id) as (identity, store):
            task = TaskService(store).get_active_task(project_id)
            sessions = store.list_sessions(project_id=project_id, limit=1)
            checkpoints = store.list_checkpoint_references(project_id, limit=1)
            return {
                **self._project(identity),
                "schema_version": store.get_schema_version(),
                "active_task": self._task_summary(task) if task else None,
                "latest_session": self._session(store, project_id, sessions[0])
                if sessions
                else None,
                "workspace_busy": self._status.workspace_activity(identity),
                "unfinalized_sessions": store.count_sessions_by_status(
                    project_id, UNFINALIZED_SESSION_STATUSES
                ),
                "current_work": excerpt(task.current_work, 2000)
                if task and task.current_work
                else None,
                "completed": text_items(task.completed_items if task else []),
                "issues": text_items(task.known_issues if task else []),
                "checkpoint": checkpoints[0] if checkpoints else None,
            }

    def tasks(self, root: Path, project_id: str, offset: int) -> dict[str, Any]:
        with read_workspace(root, expected_project_id=project_id) as (_, store):
            rows = store.list_task_previews(project_id, limit=PAGE_SIZE + 1, offset=offset)
            for row in rows:
                row["title"] = excerpt(row["title"])
                row["objective_preview"] = excerpt(row["objective_preview"], 320)
            return page(rows, offset)

    @staticmethod
    def _task_summary(task: Task) -> dict[str, Any]:
        return {
            "id": task.id,
            "title": excerpt(task.title),
            "status": task.status,
            "created_at": task.created_at.isoformat(),
            "updated_at": task.updated_at.isoformat(),
        }

    def task(self, root: Path, project_id: str, task_id: str) -> dict[str, Any]:
        with read_workspace(root, expected_project_id=project_id) as (_, store):
            task = TaskService(store).get_task(task_id)
            if task.project_id != project_id:
                raise TaskNotFoundError(task_id)
            return {
                **self._task_summary(task),
                "requirements": text_items(task.requirements),
                "constraints": text_items(task.constraints),
                "completed": text_items(task.completed_items),
                "current_work": excerpt(task.current_work, 2000) if task.current_work else None,
                "remaining": text_items(task.remaining_items),
                "issues": text_items(task.known_issues),
                "decisions": text_items(
                    unique_checkpoint_decisions(
                        store.list_checkpoint_decisions(project_id, task_id)
                    )
                ),
            }

    def objective(
        self,
        root: Path,
        project_id: str,
        task_id: str,
        offset: int,
    ) -> dict[str, Any]:
        with read_workspace(root, expected_project_id=project_id) as (_, store):
            text = store.get_objective_slice(
                project_id, task_id, offset=offset, limit=OBJECTIVE_CHARS + 1
            )
            if text is None:
                raise TaskNotFoundError(task_id)
            return {
                "text": text[:OBJECTIVE_CHARS],
                "next": offset + OBJECTIVE_CHARS if len(text) > OBJECTIVE_CHARS else None,
            }

    def _session(
        self,
        store: SQLiteStateStore,
        project_id: str,
        session: Session,
    ) -> dict[str, Any]:
        capabilities = native_capabilities(self._providers.get(session.provider_id))
        checkpoints = store.list_checkpoint_references(
            project_id, session_id=session.id, task_id=session.task_id, limit=1
        )
        handoff_id = session.metadata.get("handoff_id")
        handoff = store.get_handoff(handoff_id) if isinstance(handoff_id, str) else None
        if handoff and (
            handoff.project_id != project_id
            or handoff.task_id != session.task_id
            or handoff.target_session_id != session.id
        ):
            handoff = None
        return {
            **session.model_dump(mode="json", exclude={"metadata", "native_session_id"}),
            "native_resume": is_native_resumable(session, capabilities),
            "task_continuation": capabilities.supports_managed_new_session,
            "ownership_uncertain": session.status in UNFINALIZED_SESSION_STATUSES,
            "checkpoint": checkpoints[0] if checkpoints else None,
            "delivery": {
                "id": handoff.id,
                "status": handoff.status,
                "delivered_at": handoff.delivered_at.isoformat() if handoff.delivered_at else None,
                "failure_code": handoff.failure_code,
            }
            if handoff
            else None,
        }

    def sessions(
        self,
        root: Path,
        project_id: str,
        task_id: str | None,
        offset: int,
    ) -> dict[str, Any]:
        with read_workspace(root, expected_project_id=project_id) as (_, store):
            sessions = store.list_sessions(
                project_id=project_id, task_id=task_id, limit=PAGE_SIZE + 1, offset=offset
            )
            return page([self._session(store, project_id, session) for session in sessions], offset)

    def checkpoints(
        self,
        root: Path,
        project_id: str,
        task_id: str | None,
        offset: int,
    ) -> dict[str, Any]:
        with read_workspace(root, expected_project_id=project_id) as (_, store):
            return page(
                store.list_checkpoint_references(
                    project_id,
                    task_id=task_id,
                    limit=PAGE_SIZE + 1,
                    offset=offset,
                ),
                offset,
            )

    def handoffs(
        self,
        root: Path,
        project_id: str,
        task_id: str | None,
        offset: int,
    ) -> dict[str, Any]:
        with read_workspace(root, expected_project_id=project_id) as (_, store):
            return page(
                store.list_handoff_references(
                    project_id,
                    task_id=task_id,
                    limit=PAGE_SIZE + 1,
                    offset=offset,
                ),
                offset,
            )

    def checkpoint(self, root: Path, project_id: str, record_id: str) -> dict[str, Any]:
        with read_workspace(root, expected_project_id=project_id) as (_, store):
            record = store.get_checkpoint(record_id)
            if record is None or record.project_id != project_id:
                raise CheckpointNotFoundError(record_id)
            payload = record.payload
            return {
                **record.model_dump(mode="json", exclude={"payload", "metadata"}),
                "decisions": text_items(payload.decisions),
                "remaining": text_items(payload.task.remaining),
                "issues": text_items(payload.task.known_issues),
                "test_status": {
                    **payload.test_status.model_dump(mode="json"),
                    "summary": excerpt(payload.test_status.summary, 2000),
                },
                "operator_note": excerpt(payload.operator_note, 2000)
                if payload.operator_note
                else None,
                "observed_head": payload.git_state.head_sha,
            }

    def handoff(self, root: Path, project_id: str, record_id: str) -> dict[str, Any]:
        with read_workspace(root, expected_project_id=project_id) as (_, store):
            record = store.get_handoff(record_id)
            if record is None or record.project_id != project_id:
                raise HandoffNotFoundError(record_id)
            payload = record.payload
            return {
                **record.model_dump(mode="json", exclude={"payload", "metadata"}),
                "decisions": text_items(payload.important_decisions),
                "remaining": text_items(payload.remaining),
                "issues": text_items(payload.known_issues),
                "test_status": {
                    "known": payload.test_status.known,
                    "summary": excerpt(payload.test_status.summary, 2000),
                    "provenance": "historical" if payload.test_status.known else "unknown",
                },
                "operator_note": excerpt(payload.operator_note, 2000)
                if payload.operator_note
                else None,
                "observed_head": payload.git_state.head_sha,
                "next_action": excerpt(payload.recommended_next_action, 2000),
            }

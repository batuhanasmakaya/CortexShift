"""Unit tests verifying historical immutability of Checkpoint task snapshots."""

from pathlib import Path

from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.application.checkpoint_builder import CheckpointBuilder
from cortexshift.domain.checkpoint import CheckpointKind
from cortexshift.domain.git import RepositoryInspection, RepositoryInspectionStatus
from cortexshift.domain.project import Project
from cortexshift.domain.task import Task, TaskStatus


def test_checkpoint_task_snapshot_immutable_under_task_mutations(tmp_path: Path) -> None:
    """Verify task mutations after checkpoint creation do not affect historical checkpoint."""
    db_file = tmp_path / "immutability.sqlite3"
    with SQLiteStateStore(db_file) as store:
        project = Project(name="TestProj", repo_path=str(tmp_path))
        store.save_project(project)

        task = Task(
            project_id=project.id,
            title="Original Title",
            objective="Original Objective",
            requirements=["req 1"],
            constraints=["con 1"],
            status=TaskStatus.IN_PROGRESS,
            completed_items=["done 1"],
            current_work="active 1",
            remaining_items=["remaining 1"],
            known_issues=["issue 1"],
        )
        store.save_task(task)

        inspection = RepositoryInspection(
            project_root=str(tmp_path),
            git_available=False,
            status=RepositoryInspectionStatus.NOT_GIT_REPOSITORY,
            diagnostic="Not git",
        )

        checkpoint = CheckpointBuilder.build(
            project=project,
            task=task,
            inspection=inspection,
            kind=CheckpointKind.MANUAL,
            decisions=["Decision 1"],
        )
        store.save_checkpoint(checkpoint)

        # Mutate the Task in store via model_copy
        mutated_task = task.model_copy(
            update={
                "title": "Altered Title",
                "status": TaskStatus.COMPLETED,
                "completed_items": ["done 1", "done 2", "done 3"],
                "current_work": None,
                "remaining_items": [],
                "known_issues": [],
            }
        )
        store.save_task(mutated_task)

        # Verify task is mutated
        reloaded_task = store.get_task(task.id)
        assert reloaded_task is not None
        assert reloaded_task.title == "Altered Title"
        assert reloaded_task.status == TaskStatus.COMPLETED
        assert len(reloaded_task.completed_items) == 3

        # Reload checkpoint from store and verify it retains the historical snapshot
        reloaded_checkpoint = store.get_checkpoint(checkpoint.id)
        assert reloaded_checkpoint is not None
        snap = reloaded_checkpoint.payload.task

        assert snap.task_title == "Original Title"
        assert snap.task_status == "in_progress"
        assert snap.objective == "Original Objective"
        assert snap.requirements == ["req 1"]
        assert snap.constraints == ["con 1"]
        assert snap.completed == ["done 1"]
        assert snap.current_work == "active 1"
        assert snap.remaining == ["remaining 1"]
        assert snap.known_issues == ["issue 1"]

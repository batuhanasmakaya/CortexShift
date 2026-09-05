from pathlib import Path

from cortexshift.domain import (
    PROVIDER_CLAUDE,
    GitSnapshot,
    HandoffRecord,
    Project,
    ProviderCapabilities,
    ProviderId,
    RepositoryInspection,
    RepositoryInspectionStatus,
    Task,
)
from cortexshift.ports import (
    ProviderAdapter,
    RepositoryInspector,
    RepositorySnapshotStore,
    StateStore,
)


class DummyProviderAdapter:
    """Mock implementation verifying ProviderAdapter protocol compliance."""

    @property
    def provider_id(self) -> ProviderId:
        return PROVIDER_CLAUDE

    def probe(self) -> bool:
        return True

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_id=PROVIDER_CLAUDE,
            display_name="Claude Mock",
        )

    def launch_interactive(self, task: Task, handoff: HandoffRecord | None = None) -> int:
        return 0

    def run_headless(
        self,
        task: Task,
        instruction: str,
        handoff: HandoffRecord | None = None,
    ) -> int:
        return 0

    def resume_native_session(self, task: Task, native_session_id: str) -> int:
        return 0


class DummyRepositoryInspector:
    """Mock implementation verifying RepositoryInspector protocol compliance."""

    def inspect(self, project_root: Path | str, project_id: str = "") -> RepositoryInspection:
        return RepositoryInspection(
            status=RepositoryInspectionStatus.READY,
            project_root=str(project_root),
            git_available=True,
            snapshot=GitSnapshot(
                project_id=project_id or "proj_123",
                project_root=str(project_root),
                git_root=str(project_root),
            ),
        )


class DummyRepositorySnapshotStore:
    """Mock implementation verifying RepositorySnapshotStore protocol compliance."""

    def save_snapshot(self, snapshot: GitSnapshot) -> None:
        pass

    def get_snapshot(self, snapshot_id: str) -> GitSnapshot | None:
        return None

    def list_snapshots(self, project_id: str, limit: int = 10) -> list[GitSnapshot]:
        return []


class DummyStateStore:
    """Mock implementation verifying StateStore protocol compliance."""

    def save_project(self, project: Project) -> None:
        pass

    def get_project(self, project_id: str) -> Project | None:
        return None

    def get_default_project(self) -> Project | None:
        return None

    def save_task(self, task: Task) -> None:
        pass

    def get_task(self, task_id: str) -> Task | None:
        return None

    def list_tasks(self, project_id: str) -> list[Task]:
        return []

    def get_active_task_id(self, project_id: str) -> str | None:
        return None

    def set_active_task_id(self, project_id: str, task_id: str | None) -> None:
        pass

    def get_schema_version(self) -> int:
        return 1


def test_provider_adapter_protocol() -> None:
    adapter = DummyProviderAdapter()
    assert isinstance(adapter, ProviderAdapter)
    assert adapter.provider_id == "claude"
    assert adapter.probe() is True
    assert adapter.get_capabilities().display_name == "Claude Mock"


def test_repository_inspector_protocol() -> None:
    inspector = DummyRepositoryInspector()
    assert isinstance(inspector, RepositoryInspector)
    inspection = inspector.inspect("/fake/path", "proj_1")
    assert inspection.status == RepositoryInspectionStatus.READY
    assert inspection.is_ready is True
    assert inspection.snapshot is not None
    assert inspection.snapshot.project_root == "/fake/path"


def test_repository_snapshot_store_protocol() -> None:
    store = DummyRepositorySnapshotStore()
    assert isinstance(store, RepositorySnapshotStore)
    assert store.get_snapshot("snap_1") is None
    assert store.list_snapshots("proj_1") == []


def test_state_store_protocol() -> None:
    store = DummyStateStore()
    assert isinstance(store, StateStore)
    assert store.get_project("proj_1") is None
    assert store.list_tasks("proj_1") == []

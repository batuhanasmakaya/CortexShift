"""Tests for abstract port protocols."""

from cortexshift.domain import (
    PROVIDER_CLAUDE,
    GitSnapshot,
    Handoff,
    Project,
    ProviderCapabilities,
    ProviderId,
    Task,
)
from cortexshift.ports import ProviderAdapter, RepositoryInspector, StateStore


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

    def launch_interactive(self, task: Task, handoff: Handoff | None = None) -> int:
        return 0

    def run_headless(self, task: Task, instruction: str, handoff: Handoff | None = None) -> int:
        return 0

    def resume_native_session(self, task: Task, native_session_id: str) -> int:
        return 0


class DummyRepositoryInspector:
    """Mock implementation verifying RepositoryInspector protocol compliance."""

    def get_snapshot(self, repo_path: str) -> GitSnapshot:
        return GitSnapshot(repo_path=repo_path, is_dirty=False)

    def is_clean(self, repo_path: str) -> bool:
        return True

    def get_diff_summary(self, repo_path: str) -> str:
        return "Clean working tree"


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
    snapshot = inspector.get_snapshot("/fake/path")
    assert snapshot.repo_path == "/fake/path"
    assert inspector.is_clean("/fake/path") is True


def test_state_store_protocol() -> None:
    store = DummyStateStore()
    assert isinstance(store, StateStore)
    assert store.get_project("proj_1") is None
    assert store.list_tasks("proj_1") == []

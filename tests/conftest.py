"""Native provider model calls are never allowed in the deterministic test suite."""

import pytest

from tests.factories import FakeCodexBootstrap


@pytest.fixture(autouse=True)
def codex_bootstrap(monkeypatch: pytest.MonkeyPatch) -> FakeCodexBootstrap:
    fake = FakeCodexBootstrap()
    monkeypatch.setattr(
        "cortexshift.adapters.providers.codex.SubprocessHeadlessProviderRunner", lambda: fake
    )
    return fake

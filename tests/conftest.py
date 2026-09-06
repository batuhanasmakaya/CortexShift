"""Native provider model calls are never allowed in the deterministic test suite."""

import pytest

from tests.cli_runner import pin_console_width
from tests.factories import FakeCodexBootstrap


@pytest.fixture(autouse=True)
def codex_bootstrap(monkeypatch: pytest.MonkeyPatch) -> FakeCodexBootstrap:
    fake = FakeCodexBootstrap()
    monkeypatch.setattr(
        "cortexshift.adapters.providers.codex.SubprocessHeadlessProviderRunner", lambda: fake
    )
    return fake


@pytest.fixture
def fixed_console_width(monkeypatch: pytest.MonkeyPatch) -> int:
    """Render CLI output at a fixed width, for tests that assert on laid-out tables.

    Opt-in, and deliberately not autouse: the suite otherwise honours whatever terminal
    size the caller asked for, so `COLUMNS=60 pytest` genuinely exercises 60 columns.
    """
    return pin_console_width(monkeypatch)

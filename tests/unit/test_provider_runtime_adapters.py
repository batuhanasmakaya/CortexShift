"""Unit tests for native provider runtime adapters and command safety."""

from pathlib import Path

import pytest

from cortexshift.adapters.providers.antigravity import AntigravityRuntimeAdapter
from cortexshift.adapters.providers.claude import ClaudeRuntimeAdapter
from cortexshift.adapters.providers.codex import CodexRuntimeAdapter
from cortexshift.domain.errors import UnsupportedPromptError
from cortexshift.domain.provider import (
    PROVIDER_ANTIGRAVITY,
    PROVIDER_CLAUDE,
    PROVIDER_CODEX,
)


def test_claude_launch_spec_without_prompt(tmp_path: Path) -> None:
    """Verify Claude launch specification without initial prompt."""
    adapter = ClaudeRuntimeAdapter()
    assert adapter.provider_id == PROVIDER_CLAUDE
    assert adapter.display_name == "Claude Code"
    assert adapter.executable == "claude"

    spec = adapter.build_launch_spec(project_root=tmp_path, executable_path="/bin/claude")
    assert spec.provider_id == PROVIDER_CLAUDE
    assert spec.executable == "/bin/claude"
    assert spec.cwd == tmp_path
    assert spec.argv == ["/bin/claude", "--session-id", spec.native_session_id]
    assert spec.interactive is True
    assert spec.initial_prompt_supported is True
    assert spec.prompt_supplied is False
    assert "-p" not in spec.argv


def test_claude_launch_spec_with_prompt(tmp_path: Path) -> None:
    """Verify Claude launch specification with initial prompt."""
    adapter = ClaudeRuntimeAdapter()
    spec = adapter.build_launch_spec(
        project_root=tmp_path,
        executable_path="/bin/claude",
        prompt="Inspect the repo",
    )
    assert spec.argv == ["/bin/claude", "--session-id", spec.native_session_id, "Inspect the repo"]
    assert spec.prompt_supplied is True
    assert "-p" not in spec.argv


def test_codex_launch_spec_without_prompt(tmp_path: Path) -> None:
    """Verify Codex launch specification without initial prompt."""
    adapter = CodexRuntimeAdapter()
    assert adapter.provider_id == PROVIDER_CODEX
    assert adapter.display_name == "Codex"
    assert adapter.executable == "codex"

    spec = adapter.build_launch_spec(project_root=tmp_path, executable_path="/bin/codex")
    assert spec.argv == ["/bin/codex"]
    assert spec.interactive is True
    assert spec.initial_prompt_supported is True
    assert spec.prompt_supplied is False
    assert "exec" not in spec.argv


def test_codex_launch_spec_with_prompt(tmp_path: Path) -> None:
    """Verify Codex launch specification with initial prompt."""
    adapter = CodexRuntimeAdapter()
    spec = adapter.build_launch_spec(
        project_root=tmp_path,
        executable_path="/bin/codex",
        prompt="Fix the tests",
    )
    assert spec.argv == ["/bin/codex", "Fix the tests"]
    assert spec.prompt_supplied is True
    assert "exec" not in spec.argv


def test_antigravity_launch_spec_without_prompt(tmp_path: Path) -> None:
    """Verify Antigravity launch specification without prompt."""
    adapter = AntigravityRuntimeAdapter()
    assert adapter.provider_id == PROVIDER_ANTIGRAVITY
    assert adapter.display_name == "Antigravity"
    assert adapter.executable == "agy"

    spec = adapter.build_launch_spec(project_root=tmp_path, executable_path="/bin/agy")
    assert spec.argv == ["/bin/agy"]
    assert spec.interactive is True
    assert spec.initial_prompt_supported is False
    assert spec.prompt_supplied is False
    assert "-p" not in spec.argv


def test_antigravity_launch_spec_rejects_prompt(tmp_path: Path) -> None:
    """Verify Antigravity cleanly rejects an initial prompt without headless fallback."""
    adapter = AntigravityRuntimeAdapter()
    with pytest.raises(UnsupportedPromptError) as exc_info:
        adapter.build_launch_spec(
            project_root=tmp_path,
            executable_path="/bin/agy",
            prompt="Try to start",
        )
    assert "Antigravity does not currently expose a supported interactive initial-prompt" in str(
        exc_info.value
    )
    assert "Launch without --prompt" in str(exc_info.value)


@pytest.mark.parametrize(
    "malicious_prompt",
    [
        "hello; rm -rf /",
        "$(touch /tmp/hacked)",
        "`cat /etc/passwd`",
        "foo && bar || baz",
        'test "with" quotes',
        "multi\nline\nprompt",
    ],
)
def test_command_injection_regression_preserves_single_argument(
    tmp_path: Path,
    malicious_prompt: str,
) -> None:
    """Verify that arbitrary shell characters remain safely contained within a single argv item."""
    claude_adapter = ClaudeRuntimeAdapter()
    spec_claude = claude_adapter.build_launch_spec(
        project_root=tmp_path,
        executable_path="/bin/claude",
        prompt=malicious_prompt,
    )
    # Must be exactly 2 items: executable and the raw prompt string
    assert len(spec_claude.argv) == 4
    assert spec_claude.argv[0] == "/bin/claude"
    assert spec_claude.argv[-1] == malicious_prompt

    codex_adapter = CodexRuntimeAdapter()
    spec_codex = codex_adapter.build_launch_spec(
        project_root=tmp_path,
        executable_path="/bin/codex",
        prompt=malicious_prompt,
    )
    assert len(spec_codex.argv) == 2
    assert spec_codex.argv[0] == "/bin/codex"
    assert spec_codex.argv[1] == malicious_prompt

"""Unit tests for provider-specific handoff delivery adapters."""

import json
from pathlib import Path
from typing import Any

import pytest

from cortexshift.adapters.providers.antigravity import (
    ANTIGRAVITY_BOOTSTRAP_PREFIX,
    AntigravityHandoffAdapter,
)
from cortexshift.adapters.providers.claude import ClaudeHandoffAdapter
from cortexshift.adapters.providers.codex import CodexHandoffAdapter
from cortexshift.domain.errors import HandoffDeliveryError
from cortexshift.domain.handoff import HandoffFailureCode
from cortexshift.domain.provider import (
    PROVIDER_ANTIGRAVITY,
    PROVIDER_CLAUDE,
    PROVIDER_CODEX,
)
from cortexshift.ports.handoff_delivery import HandoffDeliveryStrategy, ProviderHandoffAdapter
from cortexshift.ports.headless_runner import HeadlessProviderRunner, HeadlessResult

CONTEXT = "CORTEXSHIFT HANDOFF PROTOCOL v1\n\nSome canonical context."


class FakeHeadlessRunner(HeadlessProviderRunner):
    """Records headless invocations and returns a scripted result."""

    def __init__(self, result: HeadlessResult) -> None:
        self.result = result
        self.invocations: list[dict[str, Any]] = []

    def run_headless(
        self,
        argv: list[str],
        cwd: Path | str,
        timeout: float = 300.0,
        env: dict[str, str] | None = None,
    ) -> HeadlessResult:
        self.invocations.append({"argv": argv, "cwd": cwd, "timeout": timeout, "env": env})
        return self.result


def _success_result(conversation_id: str = "test-conversation-123") -> HeadlessResult:
    return HeadlessResult(
        exit_code=0,
        stdout=json.dumps(
            {
                "conversation_id": conversation_id,
                "status": "SUCCESS",
                "response": "A long continuation plan that must never be persisted.",
                "usage": {"input_tokens": 100},
            }
        ),
        stderr="",
    )


# --- Claude & Codex direct interactive delivery ---


@pytest.mark.parametrize(
    ("adapter", "provider_id", "executable"),
    [
        (ClaudeHandoffAdapter(), PROVIDER_CLAUDE, "claude"),
        (CodexHandoffAdapter(), PROVIDER_CODEX, "codex"),
    ],
)
def test_direct_adapters_pass_context_as_single_argument(
    adapter: ProviderHandoffAdapter,
    provider_id: str,
    executable: str,
    tmp_path: Path,
) -> None:
    """Verify Claude and Codex receive the handoff as one positional argv element."""
    assert adapter.provider_id == provider_id
    assert adapter.executable == executable
    assert adapter.delivery_strategy == HandoffDeliveryStrategy.DIRECT_INITIAL_PROMPT
    assert adapter.bootstrap_model_turn_required is False

    preparation = adapter.prepare_delivery(
        executable_path=f"/bin/{executable}",
        project_root=tmp_path,
        rendered_context=CONTEXT,
    )

    assert preparation.launch_spec.argv == [f"/bin/{executable}", CONTEXT]
    assert preparation.launch_spec.cwd == tmp_path
    assert preparation.launch_spec.interactive is True
    assert preparation.launch_spec.prompt_supplied is True
    assert preparation.native_session_id is None
    assert preparation.bootstrap_performed is False


@pytest.mark.parametrize("adapter", [ClaudeHandoffAdapter(), CodexHandoffAdapter()])
def test_direct_adapters_never_use_headless_or_exec_subcommands(
    adapter: ProviderHandoffAdapter, tmp_path: Path
) -> None:
    """Verify no extra model turn or non-interactive subcommand is introduced."""
    argv = adapter.prepare_delivery(
        executable_path="/bin/tool",
        project_root=tmp_path,
        rendered_context=CONTEXT,
    ).launch_spec.argv

    assert len(argv) == 2
    for forbidden in (
        "exec",
        "-p",
        "--print",
        "--model",
        "--permission-mode",
        "--dangerously-skip-permissions",
        "--sandbox",
    ):
        assert forbidden not in argv


def test_direct_adapters_keep_shell_metacharacters_inert(tmp_path: Path) -> None:
    """Verify injection-shaped context stays a single inert argument."""
    hostile = 'Objective: $(touch hacked); rm -rf / && echo "quoted"\nnewline'
    argv = (
        ClaudeHandoffAdapter()
        .prepare_delivery(
            executable_path="/bin/claude",
            project_root=tmp_path,
            rendered_context=hostile,
        )
        .launch_spec.argv
    )

    assert argv == ["/bin/claude", hostile]


# --- Antigravity read-only bootstrap + conversation resume ---


def test_antigravity_bootstrap_argv_semantics(tmp_path: Path) -> None:
    """Verify the bootstrap runs read-only plan mode with JSON output, prompt as one arg."""
    runner = FakeHeadlessRunner(_success_result())
    adapter = AntigravityHandoffAdapter(headless_runner=runner)

    assert adapter.provider_id == PROVIDER_ANTIGRAVITY
    assert adapter.delivery_strategy == HandoffDeliveryStrategy.PLAN_BOOTSTRAP_THEN_RESUME
    assert adapter.bootstrap_model_turn_required is True

    adapter.prepare_delivery(
        executable_path="/bin/agy",
        project_root=tmp_path,
        rendered_context=CONTEXT,
    )

    argv = runner.invocations[0]["argv"]
    assert argv[0] == "/bin/agy"
    assert "--mode=plan" in argv
    assert "--output-format" in argv
    assert argv[argv.index("--output-format") + 1] == "json"

    prompt_index = argv.index("-p") + 1
    prompt = argv[prompt_index]
    assert prompt.startswith(ANTIGRAVITY_BOOTSTRAP_PREFIX.split("\n")[0])
    assert CONTEXT in prompt
    # The whole handoff must remain exactly one subprocess argument.
    assert sum(1 for arg in argv if CONTEXT in arg) == 1
    assert runner.invocations[0]["cwd"] == tmp_path


def test_antigravity_bootstrap_prompt_is_read_only() -> None:
    """Verify the bootstrap instruction forbids mutation and promises a resume."""
    prefix = ANTIGRAVITY_BOOTSTRAP_PREFIX
    assert "Remain read-only." in prefix
    assert "Do not modify files." in prefix
    assert "Do not run mutating commands." in prefix
    assert "Produce a concise continuation plan." in prefix
    assert "resumed immediately" in prefix


def test_antigravity_bootstrap_never_bypasses_permissions(tmp_path: Path) -> None:
    """Verify no permission-bypass or auto-accept flags are ever passed."""
    runner = FakeHeadlessRunner(_success_result())
    AntigravityHandoffAdapter(headless_runner=runner).prepare_delivery(
        executable_path="/bin/agy",
        project_root=tmp_path,
        rendered_context=CONTEXT,
    )

    argv = runner.invocations[0]["argv"]
    for forbidden in (
        "--dangerously-skip-permissions",
        "--accept-edits",
        "accept-edits",
        "--yolo",
        "--auto-approve",
    ):
        assert forbidden not in argv


def test_antigravity_captures_conversation_and_resumes_it(tmp_path: Path) -> None:
    """Verify the captured conversation ID drives the interactive resume."""
    runner = FakeHeadlessRunner(_success_result("test-conversation-123"))
    preparation = AntigravityHandoffAdapter(headless_runner=runner).prepare_delivery(
        executable_path="/bin/agy",
        project_root=tmp_path,
        rendered_context=CONTEXT,
    )

    assert preparation.native_session_id == "test-conversation-123"
    assert preparation.bootstrap_performed is True
    assert preparation.launch_spec.argv == ["/bin/agy", "--conversation", "test-conversation-123"]
    assert preparation.launch_spec.interactive is True
    assert preparation.launch_spec.prompt_supplied is False


def test_antigravity_discards_bootstrap_response(tmp_path: Path) -> None:
    """Verify the model response, reasoning, and usage never leave the adapter."""
    secret = "A long continuation plan that must never be persisted."
    runner = FakeHeadlessRunner(_success_result())
    preparation = AntigravityHandoffAdapter(headless_runner=runner).prepare_delivery(
        executable_path="/bin/agy",
        project_root=tmp_path,
        rendered_context=CONTEXT,
    )

    serialized = preparation.model_dump_json()
    assert secret not in serialized
    assert "usage" not in serialized
    assert "input_tokens" not in serialized


@pytest.mark.parametrize(
    ("result", "expected_code"),
    [
        (
            HeadlessResult(exit_code=1, stdout="", stderr="boom"),
            HandoffFailureCode.BOOTSTRAP_FAILED,
        ),
        (
            HeadlessResult(exit_code=0, stdout="not json at all", stderr=""),
            HandoffFailureCode.BOOTSTRAP_INVALID_OUTPUT,
        ),
        (
            HeadlessResult(exit_code=0, stdout="", stderr=""),
            HandoffFailureCode.BOOTSTRAP_INVALID_OUTPUT,
        ),
        (
            HeadlessResult(exit_code=0, stdout='["a", "b"]', stderr=""),
            HandoffFailureCode.BOOTSTRAP_INVALID_OUTPUT,
        ),
        (
            HeadlessResult(exit_code=0, stdout='{"status": "SUCCESS"}', stderr=""),
            HandoffFailureCode.BOOTSTRAP_INVALID_OUTPUT,
        ),
        (
            HeadlessResult(
                exit_code=0, stdout='{"conversation_id": "c1", "status": "ERROR"}', stderr=""
            ),
            HandoffFailureCode.BOOTSTRAP_FAILED,
        ),
        (
            HeadlessResult(exit_code=-1, stdout="", stderr="", timed_out=True),
            HandoffFailureCode.BOOTSTRAP_TIMEOUT,
        ),
        (
            HeadlessResult(exit_code=127, stdout="", stderr="", not_found=True),
            HandoffFailureCode.SPAWN_FAILED,
        ),
    ],
)
def test_antigravity_bootstrap_failures_are_classified_safely(
    result: HeadlessResult, expected_code: HandoffFailureCode, tmp_path: Path
) -> None:
    """Verify every bootstrap failure maps to a safe code without leaking raw output."""
    adapter = AntigravityHandoffAdapter(headless_runner=FakeHeadlessRunner(result))

    with pytest.raises(HandoffDeliveryError) as exc:
        adapter.prepare_delivery(
            executable_path="/bin/agy",
            project_root=tmp_path,
            rendered_context=CONTEXT,
        )

    assert exc.value.failure_code == expected_code.value
    message = str(exc.value)
    assert result.stderr not in message or not result.stderr
    assert "boom" not in message


def test_antigravity_empty_conversation_id_rejected(tmp_path: Path) -> None:
    """Verify a blank conversation identifier is never accepted as success."""
    runner = FakeHeadlessRunner(
        HeadlessResult(
            exit_code=0,
            stdout=json.dumps({"conversation_id": "   ", "status": "SUCCESS"}),
            stderr="",
        )
    )

    with pytest.raises(HandoffDeliveryError) as exc:
        AntigravityHandoffAdapter(headless_runner=runner).prepare_delivery(
            executable_path="/bin/agy",
            project_root=tmp_path,
            rendered_context=CONTEXT,
        )

    assert exc.value.failure_code == HandoffFailureCode.BOOTSTRAP_INVALID_OUTPUT.value


def test_all_handoff_adapters_satisfy_the_port() -> None:
    """Verify every delivery adapter structurally implements ProviderHandoffAdapter."""
    for adapter in (ClaudeHandoffAdapter(), CodexHandoffAdapter(), AntigravityHandoffAdapter()):
        assert isinstance(adapter, ProviderHandoffAdapter)

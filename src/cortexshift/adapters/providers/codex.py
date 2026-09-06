"""OpenAI Codex native CLI probe and discovery adapter."""

import json
import re
import shutil
import sys
from collections.abc import Callable
from pathlib import Path

from cortexshift.adapters.headless_runner import SubprocessHeadlessProviderRunner
from cortexshift.domain.doctor import AuthenticationStatus, ProviderDiagnostic
from cortexshift.domain.errors import HandoffDeliveryError, NativeResumeError
from cortexshift.domain.launch import LaunchSpecification
from cortexshift.domain.native_session import NativeSessionCapabilities, valid_native_id
from cortexshift.domain.provider import PROVIDER_CODEX, ProviderCapabilities, ProviderId
from cortexshift.ports.command_runner import CommandRunner
from cortexshift.ports.discovery import ProviderProbe
from cortexshift.ports.handoff_delivery import (
    HandoffDeliveryPreparation,
    HandoffDeliveryStrategy,
    ProviderHandoffAdapter,
)
from cortexshift.ports.headless_runner import (
    DEFAULT_HEADLESS_TIMEOUT_SECONDS,
    HeadlessProviderRunner,
)
from cortexshift.ports.provider import ProviderRuntimeAdapter

_VERSION_RE = re.compile(r"(\d+\.\d+(?:\.\d+)?(?:[-.][a-zA-Z0-9]+)?)")


def _extract_version(text: str) -> str | None:
    """Extract a clean semver-like version string from CLI output."""
    match = _VERSION_RE.search(text)
    if match:
        return match.group(1)
    first_line = text.splitlines()[0].strip() if text else ""
    return first_line[:32] if first_line and len(first_line) <= 32 else None


def _classify_codex_auth(stdout: str, stderr: str, exit_code: int) -> AuthenticationStatus:
    """Classify Codex auth status conservatively without leaking sensitive output."""
    combined = f"{stdout}\n{stderr}".lower()

    # Try parsing JSON output
    if stdout.strip().startswith("{"):
        try:
            data = json.loads(stdout)
            if isinstance(data, dict):
                if data.get("authenticated") is True or data.get("status") == "logged_in":
                    return AuthenticationStatus.AUTHENTICATED
                if data.get("authenticated") is False or data.get("status") == "logged_out":
                    return AuthenticationStatus.NOT_AUTHENTICATED
        except json.JSONDecodeError:
            pass

    # Check for unauthenticated markers
    if (
        "not logged in" in combined
        or "logged out" in combined
        or "no active session" in combined
        or "login required" in combined
        or "run codex login" in combined
    ):
        return AuthenticationStatus.NOT_AUTHENTICATED

    # Check for authenticated markers
    if exit_code == 0 and ("logged in" in combined or "authenticated" in combined):
        return AuthenticationStatus.AUTHENTICATED

    if exit_code == 0 and not combined.strip():
        return AuthenticationStatus.AUTHENTICATED

    if exit_code != 0 and ("unauthorized" in combined or "not authenticated" in combined):
        return AuthenticationStatus.NOT_AUTHENTICATED

    return AuthenticationStatus.UNKNOWN


class CodexProviderProbe(ProviderProbe):
    """Probe for detecting and diagnosing the Codex native CLI."""

    def __init__(
        self,
        command_runner: CommandRunner,
        which_fn: Callable[[str], str | None] = shutil.which,
    ) -> None:
        self._runner = command_runner
        self._which = which_fn

    @property
    def provider_id(self) -> ProviderId:
        return PROVIDER_CODEX

    @property
    def display_name(self) -> str:
        return "Codex"

    @property
    def executable(self) -> str:
        return "codex"

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_id=self.provider_id,
            display_name=self.display_name,
            supports_interactive=True,
            supports_headless=True,
            supports_native_resume=True,
            supports_structured_output=True,
            supports_mcp=True,
            supports_usage_metrics=True,
        )

    def probe(self) -> ProviderDiagnostic:
        capabilities = self.get_capabilities()
        resolved_path = self._which(self.executable)

        if not resolved_path:
            return ProviderDiagnostic(
                provider_id=self.provider_id,
                display_name=self.display_name,
                executable=self.executable,
                installed=False,
                resolved_path=None,
                version=None,
                authentication_status=AuthenticationStatus.UNKNOWN,
                capabilities=capabilities,
                diagnostics=["Not found in PATH"],
            )

        # 1. Version probe
        version_result = self._runner.run([resolved_path, "--version"], timeout=5.0)
        version: str | None = None
        diagnostics: list[str] = []

        if version_result.success:
            version = _extract_version(version_result.stdout)
            if not version:
                diagnostics.append("Version output could not be parsed")
        else:
            if version_result.timed_out:
                diagnostics.append("Version probe timed out")
            else:
                diagnostics.append("Version probe failed")

        # 2. Authentication probe: passive `codex login status`
        auth_result = self._runner.run([resolved_path, "login", "status"], timeout=5.0)
        auth_status = _classify_codex_auth(
            stdout=auth_result.stdout,
            stderr=auth_result.stderr,
            exit_code=auth_result.exit_code,
        )

        if auth_result.timed_out:
            diagnostics.append("Authentication probe timed out")
        elif auth_status == AuthenticationStatus.UNKNOWN and not auth_result.success:
            diagnostics.append("Authentication probe failed")

        return ProviderDiagnostic(
            provider_id=self.provider_id,
            display_name=self.display_name,
            executable=self.executable,
            installed=True,
            resolved_path=resolved_path,
            version=version,
            authentication_status=auth_status,
            capabilities=capabilities,
            diagnostics=diagnostics,
        )


def build_codex_mcp_args(python_executable: str | None = None) -> list[str]:
    """Build the argument list of -c overrides for Codex MCP configuration."""
    exe = python_executable or sys.executable
    return [
        "-c",
        f'mcp_servers.cortexshift.command="{exe}"',
        "-c",
        'mcp_servers.cortexshift.args=["-m", "cortexshift", "mcp", "serve"]',
        "-c",
        "mcp_servers.cortexshift.required=true",
    ]


class CodexRuntimeAdapter(ProviderRuntimeAdapter):
    """Runtime adapter for launching OpenAI Codex interactive sessions."""

    def __init__(self, python_executable: str | None = None) -> None:
        self._python_executable = python_executable

    @property
    def provider_id(self) -> ProviderId:
        return PROVIDER_CODEX

    @property
    def display_name(self) -> str:
        return "Codex"

    @property
    def executable(self) -> str:
        return "codex"

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_id=self.provider_id,
            display_name=self.display_name,
            supports_interactive=True,
            supports_headless=True,
            supports_native_resume=True,
            supports_structured_output=True,
            supports_mcp=True,
            supports_usage_metrics=True,
        )

    def get_native_capabilities(self) -> NativeSessionCapabilities:
        return NativeSessionCapabilities(
            supports_exact_resume=True,
            can_capture_native_id_during_bootstrap=True,
            can_resume_with_followup_context=True,
            requires_model_turn_for_handoff_resume=True,
            supports_managed_new_session=True,
        )

    def build_exact_resume(
        self, project_root: Path, executable_path: str, native_session_id: str
    ) -> LaunchSpecification:
        if not valid_native_id(native_session_id):
            raise NativeResumeError("Invalid native session identifier.")
        mcp_args = build_codex_mcp_args(self._python_executable)
        return LaunchSpecification(
            provider_id=self.provider_id,
            executable=executable_path,
            cwd=project_root,
            argv=[executable_path, *mcp_args, "resume", native_session_id],
            native_session_id=native_session_id,
        )

    def build_launch_spec(
        self,
        project_root: Path,
        executable_path: str,
        prompt: str | None = None,
    ) -> LaunchSpecification:
        """Build argument vector for native Codex launch."""
        mcp_args = build_codex_mcp_args(self._python_executable)
        argv = [executable_path, *mcp_args]
        prompt_supplied = False
        if prompt is not None and prompt.strip():
            argv.append(prompt)
            prompt_supplied = True

        return LaunchSpecification(
            provider_id=self.provider_id,
            executable=executable_path,
            cwd=project_root,
            argv=argv,
            interactive=True,
            initial_prompt_supported=True,
            prompt_supplied=prompt_supplied,
        )


CODEX_BOOTSTRAP_PREFIX = """This is a CortexShift handoff bootstrap.
Analyze and ingest the supplied context. Remain read-only.
Do not modify repository files. Do not run mutating commands.
The same native Codex session will be resumed immediately in the interactive TUI.

"""


class CodexHandoffAdapter(ProviderHandoffAdapter):
    """Capture native identity through one read-only JSONL turn, then open the TUI."""

    def __init__(
        self,
        runtime_adapter: CodexRuntimeAdapter | None = None,
        headless_runner: HeadlessProviderRunner | None = None,
        timeout: float = DEFAULT_HEADLESS_TIMEOUT_SECONDS,
    ) -> None:
        self._runtime = runtime_adapter or CodexRuntimeAdapter()
        self._headless = headless_runner or SubprocessHeadlessProviderRunner()
        self._timeout = timeout

    @property
    def provider_id(self) -> ProviderId:
        return PROVIDER_CODEX

    @property
    def display_name(self) -> str:
        return self._runtime.display_name

    @property
    def executable(self) -> str:
        return self._runtime.executable

    @property
    def delivery_strategy(self) -> HandoffDeliveryStrategy:
        return HandoffDeliveryStrategy.READ_ONLY_BOOTSTRAP_THEN_RESUME

    @property
    def bootstrap_model_turn_required(self) -> bool:
        return True

    def prepare_delivery(
        self,
        executable_path: str,
        project_root: Path,
        rendered_context: str,
        native_session_id: str | None = None,
    ) -> HandoffDeliveryPreparation:
        if native_session_id is not None and not valid_native_id(native_session_id):
            raise NativeResumeError("Invalid native session identifier.")
        # Sandbox is an exec parent option, JSON is also supported on exec resume.
        argv = [executable_path, "exec", "--sandbox", "read-only"]
        if native_session_id is not None:
            argv.extend(["resume", "--json", native_session_id])
        else:
            argv.append("--json")
        argv.append(CODEX_BOOTSTRAP_PREFIX + rendered_context)
        result = self._headless.run_headless(
            argv, project_root, timeout=self._timeout, env={"CORTEXSHIFT_MCP_READ_ONLY": "1"}
        )
        if result.timed_out:
            raise HandoffDeliveryError("bootstrap_timeout", "Codex handoff bootstrap timed out.")
        if result.not_found:
            raise HandoffDeliveryError("spawn_failed", "Codex handoff bootstrap could not start.")
        if result.exit_code != 0:
            raise HandoffDeliveryError("bootstrap_failed", "Codex handoff bootstrap failed.")

        captured: str | None = None
        completed = False
        try:
            for line in result.stdout.splitlines():
                if not line.strip():
                    continue
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise ValueError
                kind = event.get("type")
                if kind in ("error", "turn.failed"):
                    raise HandoffDeliveryError("bootstrap_failed", "Codex handoff turn failed.")
                if kind == "thread.started":
                    candidate = event.get("thread_id")
                    if not isinstance(candidate, str) or not valid_native_id(candidate):
                        raise ValueError
                    if captured is not None and captured != candidate:
                        raise ValueError
                    captured = candidate
                if kind == "turn.completed":
                    completed = True
        except (ValueError, TypeError):
            raise HandoffDeliveryError(
                "bootstrap_invalid_output", "Codex returned invalid bootstrap metadata."
            ) from None
        if captured is None or not completed:
            raise HandoffDeliveryError(
                "bootstrap_invalid_output",
                "Codex did not confirm a native thread and completed turn.",
            )
        if native_session_id is not None and captured != native_session_id:
            raise HandoffDeliveryError(
                "bootstrap_invalid_output", "Codex returned a different thread; refusing fallback."
            )
        return HandoffDeliveryPreparation(
            launch_spec=self._runtime.build_exact_resume(project_root, executable_path, captured),
            native_session_id=captured,
            bootstrap_performed=True,
        )

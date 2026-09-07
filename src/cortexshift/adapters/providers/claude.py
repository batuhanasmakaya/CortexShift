"""Claude Code native CLI probe and discovery adapter."""

import json
import re
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from cortexshift.domain.doctor import AuthenticationStatus, ProviderDiagnostic
from cortexshift.domain.errors import NativeResumeError
from cortexshift.domain.launch import LaunchSpecification
from cortexshift.domain.mcp_binding import McpSessionBinding
from cortexshift.domain.native_session import NativeSessionCapabilities, valid_native_id
from cortexshift.domain.provider import PROVIDER_CLAUDE, ProviderCapabilities, ProviderId
from cortexshift.ports.command_runner import CommandRunner
from cortexshift.ports.discovery import ProviderProbe
from cortexshift.ports.handoff_delivery import (
    HandoffDeliveryPreparation,
    HandoffDeliveryStrategy,
    ProviderHandoffAdapter,
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


def _classify_claude_auth(stdout: str, stderr: str, exit_code: int) -> AuthenticationStatus:
    """Classify Claude auth status conservatively without leaking sensitive output."""
    combined = f"{stdout}\n{stderr}".lower()

    # Try parsing stdout as JSON if formatted as such
    if stdout.strip().startswith("{"):
        try:
            data = json.loads(stdout)
            if isinstance(data, dict):
                if data.get("loggedIn") is True or data.get("status") == "authenticated":
                    return AuthenticationStatus.AUTHENTICATED
                if data.get("loggedIn") is False or data.get("status") == "unauthenticated":
                    return AuthenticationStatus.NOT_AUTHENTICATED
        except json.JSONDecodeError:
            pass

    # Check for definitive unauthenticated markers
    if (
        "not logged in" in combined
        or "logged out" in combined
        or "login required" in combined
        or "run claude auth login" in combined
    ):
        return AuthenticationStatus.NOT_AUTHENTICATED

    # Check for definitive authenticated markers
    if exit_code == 0 and ("logged in" in combined or "authenticated" in combined):
        return AuthenticationStatus.AUTHENTICATED

    # Exit code 0 without explicit errors often indicates valid auth for status subcommands
    if exit_code == 0 and not combined.strip():
        return AuthenticationStatus.AUTHENTICATED

    if exit_code != 0 and ("unauthorized" in combined or "not authenticated" in combined):
        return AuthenticationStatus.NOT_AUTHENTICATED

    return AuthenticationStatus.UNKNOWN


class ClaudeProviderProbe(ProviderProbe):
    """Probe for detecting and diagnosing the Claude Code native CLI."""

    def __init__(
        self,
        command_runner: CommandRunner,
        which_fn: Callable[[str], str | None] = shutil.which,
    ) -> None:
        self._runner = command_runner
        self._which = which_fn

    @property
    def provider_id(self) -> ProviderId:
        return PROVIDER_CLAUDE

    @property
    def display_name(self) -> str:
        return "Claude Code"

    @property
    def executable(self) -> str:
        return "claude"

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

        # 2. Authentication probe: passive `claude auth status`
        auth_result = self._runner.run([resolved_path, "auth", "status"], timeout=5.0)
        auth_status = _classify_claude_auth(
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


MCP_CONFIG_FLAG = "--mcp-config"


def build_claude_mcp_config(
    python_executable: str | None = None,
    binding: McpSessionBinding | None = None,
) -> str:
    """Build the inline JSON string for Claude Code --mcp-config.

    When a managed binding is supplied it is declared as the server's own environment, so
    the MCP server is bound to the CortexShift session regardless of how much of Claude
    Code's own environment reaches the server process.
    """
    exe = python_executable or sys.executable
    server: dict[str, Any] = {
        "command": exe,
        "args": ["-m", "cortexshift", "mcp", "serve"],
    }
    if binding is not None:
        server["env"] = binding.to_env()
    return json.dumps({"mcpServers": {"cortexshift": server}})


class ClaudeRuntimeAdapter(ProviderRuntimeAdapter):
    """Runtime adapter for launching Claude Code interactive sessions."""

    def __init__(self, python_executable: str | None = None) -> None:
        self._python_executable = python_executable

    @property
    def provider_id(self) -> ProviderId:
        return PROVIDER_CLAUDE

    @property
    def display_name(self) -> str:
        return "Claude Code"

    @property
    def executable(self) -> str:
        return "claude"

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
            can_allocate_native_id_before_launch=True,
            can_resume_with_followup_context=True,
            supports_managed_new_session=True,
        )

    def build_exact_resume(
        self, project_root: Path, executable_path: str, native_session_id: str
    ) -> LaunchSpecification:
        if not valid_native_id(native_session_id):
            raise NativeResumeError("Invalid native session identifier.")
        mcp_config = build_claude_mcp_config(self._python_executable)
        return LaunchSpecification(
            provider_id=self.provider_id,
            executable=executable_path,
            cwd=project_root,
            argv=[
                executable_path,
                MCP_CONFIG_FLAG,
                mcp_config,
                "--resume",
                native_session_id,
            ],
            native_session_id=native_session_id,
        )

    def bind_managed_mcp(
        self,
        launch_spec: LaunchSpecification,
        binding: McpSessionBinding,
    ) -> LaunchSpecification:
        """Restate the managed binding inside the inline --mcp-config payload."""
        if launch_spec.provider_id != self.provider_id:
            return launch_spec

        argv = list(launch_spec.argv)
        # A supplied prompt is arbitrary text in the same argv, so it is never scanned:
        # a prompt that happens to read like a flag must not be mistaken for one.
        options = argv[:-1] if launch_spec.prompt_supplied else argv
        if MCP_CONFIG_FLAG not in options:
            return launch_spec
        index = options.index(MCP_CONFIG_FLAG) + 1
        if index >= len(options):
            return launch_spec

        argv[index] = build_claude_mcp_config(self._python_executable, binding=binding)
        return launch_spec.model_copy(update={"argv": argv})

    def build_launch_spec(
        self,
        project_root: Path,
        executable_path: str,
        prompt: str | None = None,
    ) -> LaunchSpecification:
        """Build argument vector for native Claude launch."""
        native_id = str(uuid4())
        mcp_config = build_claude_mcp_config(self._python_executable)
        argv = [
            executable_path,
            MCP_CONFIG_FLAG,
            mcp_config,
            "--session-id",
            native_id,
        ]
        prompt_supplied = False
        if prompt is not None and prompt.strip():
            argv.append(prompt)
            prompt_supplied = True

        return LaunchSpecification(
            provider_id=self.provider_id,
            executable=executable_path,
            cwd=project_root,
            argv=argv,
            native_session_id=native_id,
            interactive=True,
            initial_prompt_supported=True,
            prompt_supplied=prompt_supplied,
        )


class ClaudeHandoffAdapter(ProviderHandoffAdapter):
    """Delivers canonical handoff context to Claude Code's native interactive CLI.

    Claude Code's documented interactive launch accepts an initial prompt as a trailing
    positional argument, so the canonical context is delivered directly as a single
    argv element. No extra headless model turn is required, no transcript is imported,
    and model, permission mode, and sandbox settings remain the user's own.
    """

    def __init__(self, runtime_adapter: ClaudeRuntimeAdapter | None = None) -> None:
        self._runtime = runtime_adapter or ClaudeRuntimeAdapter()

    @property
    def provider_id(self) -> ProviderId:
        return PROVIDER_CLAUDE

    @property
    def display_name(self) -> str:
        return self._runtime.display_name

    @property
    def executable(self) -> str:
        return self._runtime.executable

    @property
    def delivery_strategy(self) -> HandoffDeliveryStrategy:
        return HandoffDeliveryStrategy.DIRECT_INITIAL_PROMPT

    @property
    def bootstrap_model_turn_required(self) -> bool:
        return False

    def bind_managed_mcp(
        self,
        launch_spec: LaunchSpecification,
        binding: McpSessionBinding,
    ) -> LaunchSpecification:
        """Delegate managed MCP binding to the runtime adapter that built the argv."""
        return self._runtime.bind_managed_mcp(launch_spec, binding)

    def prepare_delivery(
        self,
        executable_path: str,
        project_root: Path,
        rendered_context: str,
        native_session_id: str | None = None,
    ) -> HandoffDeliveryPreparation:
        """Build the native interactive launch carrying the handoff as one argument."""
        if native_session_id is not None:
            launch_spec = self._runtime.build_exact_resume(
                project_root, executable_path, native_session_id
            )
            launch_spec = launch_spec.model_copy(
                update={
                    "argv": [*launch_spec.argv, rendered_context],
                    "prompt_supplied": True,
                }
            )
        else:
            launch_spec = self._runtime.build_launch_spec(
                project_root=project_root,
                executable_path=executable_path,
                prompt=rendered_context,
            )
        return HandoffDeliveryPreparation(
            launch_spec=launch_spec,
            native_session_id=launch_spec.native_session_id,
            bootstrap_performed=False,
        )

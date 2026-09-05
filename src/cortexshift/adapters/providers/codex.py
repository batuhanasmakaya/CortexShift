"""OpenAI Codex native CLI probe and discovery adapter."""

import json
import re
import shutil
from collections.abc import Callable

from cortexshift.domain.doctor import AuthenticationStatus, ProviderDiagnostic
from cortexshift.domain.provider import PROVIDER_CODEX, ProviderCapabilities, ProviderId
from cortexshift.ports.command_runner import CommandRunner
from cortexshift.ports.discovery import ProviderProbe

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
            supports_mcp=False,
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

"""Google Antigravity native CLI probe and discovery adapter."""

import re
import shutil
from collections.abc import Callable

from cortexshift.domain.doctor import AuthenticationStatus, ProviderDiagnostic
from cortexshift.domain.provider import PROVIDER_ANTIGRAVITY, ProviderCapabilities, ProviderId
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


class AntigravityProviderProbe(ProviderProbe):
    """Probe for detecting and diagnosing the Google Antigravity native CLI (`agy`).

    CRITICAL INVARIANTS:
    - Never invoke `agy -p ...` or headless prompt commands to check authentication.
      Prompt execution consumes model quota and violates passive discovery.
    - Never read config or credential files from ~/.gemini/... or Keychain.
    - Since `agy` does not currently expose a documented passive, non-model auth status
      command, authentication MUST be reported as UNKNOWN.
    """

    def __init__(
        self,
        command_runner: CommandRunner,
        which_fn: Callable[[str], str | None] = shutil.which,
    ) -> None:
        self._runner = command_runner
        self._which = which_fn

    @property
    def provider_id(self) -> ProviderId:
        return PROVIDER_ANTIGRAVITY

    @property
    def display_name(self) -> str:
        return "Antigravity"

    @property
    def executable(self) -> str:
        return "agy"

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

        # 1. Version probe (passive --version)
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

        # 2. Authentication probe:
        # Passive-only: agy does not offer a non-model auth status command.
        # Report UNKNOWN without running any prompts.
        diagnostics.append("Passive authentication probe not supported")

        return ProviderDiagnostic(
            provider_id=self.provider_id,
            display_name=self.display_name,
            executable=self.executable,
            installed=True,
            resolved_path=resolved_path,
            version=version,
            authentication_status=AuthenticationStatus.UNKNOWN,
            capabilities=capabilities,
            diagnostics=diagnostics,
        )

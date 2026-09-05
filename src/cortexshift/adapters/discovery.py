"""Built-in provider discovery adapter registering native agent probes."""

import shutil
from collections.abc import Callable

from cortexshift.adapters.command_runner import SubprocessCommandRunner
from cortexshift.adapters.providers.antigravity import AntigravityProviderProbe
from cortexshift.adapters.providers.claude import ClaudeProviderProbe
from cortexshift.adapters.providers.codex import CodexProviderProbe
from cortexshift.domain.doctor import ProviderDiagnostic
from cortexshift.domain.provider import (
    PROVIDER_ANTIGRAVITY,
    PROVIDER_CLAUDE,
    PROVIDER_CODEX,
    ProviderId,
)
from cortexshift.ports.command_runner import CommandRunner
from cortexshift.ports.discovery import ProviderDiscoveryPort, ProviderProbe


class BuiltinProviderDiscovery(ProviderDiscoveryPort):
    """Discovery adapter registering and probing built-in coding agent providers.

    Manages native probes for Claude Code, OpenAI Codex, and Google Antigravity.
    """

    def __init__(
        self,
        command_runner: CommandRunner | None = None,
        which_fn: Callable[[str], str | None] = shutil.which,
    ) -> None:
        runner = command_runner or SubprocessCommandRunner()
        self._probes: dict[ProviderId, ProviderProbe] = {
            PROVIDER_CLAUDE: ClaudeProviderProbe(runner, which_fn=which_fn),
            PROVIDER_CODEX: CodexProviderProbe(runner, which_fn=which_fn),
            PROVIDER_ANTIGRAVITY: AntigravityProviderProbe(runner, which_fn=which_fn),
        }

    def get_supported_provider_ids(self) -> list[ProviderId]:
        """Return the canonical IDs of all supported built-in providers."""
        return list(self._probes.keys())

    def discover_provider(self, provider_id: ProviderId) -> ProviderDiagnostic:
        """Probe a specific provider by its ID.

        Raises:
            KeyError: If the provider is not registered.
        """
        if provider_id not in self._probes:
            raise KeyError(f"Provider '{provider_id}' is not supported")
        return self._probes[provider_id].probe()

    def discover_all(self) -> list[ProviderDiagnostic]:
        """Probe all registered providers in declaration order."""
        return [probe.probe() for probe in self._probes.values()]

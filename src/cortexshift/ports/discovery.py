"""Port defining the interface for discovering and probing AI coding agent providers."""

from typing import Protocol, runtime_checkable

from cortexshift.domain.doctor import ProviderDiagnostic
from cortexshift.domain.provider import ProviderId


@runtime_checkable
class ProviderProbe(Protocol):
    """Abstract port for probing a single provider CLI."""

    @property
    def provider_id(self) -> ProviderId:
        """Canonical provider identifier."""
        ...

    def probe(self) -> ProviderDiagnostic:
        """Probe the provider CLI and return its diagnostic status."""
        ...


@runtime_checkable
class ProviderDiscoveryPort(Protocol):
    """Abstract port for discovering and probing native provider CLIs."""

    def discover_all(self) -> list[ProviderDiagnostic]:
        """Discover and probe all registered providers."""
        ...

    def discover_provider(self, provider_id: ProviderId) -> ProviderDiagnostic:
        """Discover and probe a specific provider by its ID.

        Raises:
            KeyError: If the provider is not supported/registered.
        """
        ...

    def get_supported_provider_ids(self) -> list[ProviderId]:
        """Return the list of all supported canonical provider IDs."""
        ...

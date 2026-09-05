"""Application service for running environment and provider diagnostics."""

import platform
from datetime import UTC, datetime

from cortexshift import __version__
from cortexshift.adapters.discovery import BuiltinProviderDiscovery
from cortexshift.domain.doctor import DoctorReport, PlatformInfo, ProviderDiagnostic
from cortexshift.domain.provider import ProviderId
from cortexshift.ports.discovery import ProviderDiscoveryPort


class UnknownProviderError(ValueError):
    """Raised when an unrecognized provider ID is requested for diagnostics."""

    def __init__(self, provider_id: str, supported: list[str]) -> None:
        self.provider_id = provider_id
        self.supported = supported
        super().__init__(
            f"Unknown provider '{provider_id}'. Supported providers: {', '.join(supported)}"
        )


class DoctorService:
    """Orchestrates environment discovery and provider diagnostics.

    Collects platform metadata and delegates provider probing to a
    ProviderDiscoveryPort implementation. Does not interact directly with CLI
    frameworks, Rich rendering, or raw subprocess calls.
    """

    def __init__(self, discovery: ProviderDiscoveryPort | None = None) -> None:
        self._discovery = discovery or BuiltinProviderDiscovery()

    def run_diagnostics(
        self,
        provider_ids: list[ProviderId] | None = None,
    ) -> DoctorReport:
        """Run diagnostics for all or selected providers.

        Args:
            provider_ids: Optional list of provider IDs to filter diagnostics by.
                          If None or empty, all supported providers are diagnosed.

        Returns:
            DoctorReport containing platform metadata and provider diagnostics.

        Raises:
            UnknownProviderError: If any specified provider ID is not supported.
        """
        supported_ids = self._discovery.get_supported_provider_ids()
        supported_str_list = [str(pid) for pid in supported_ids]

        provider_diagnostics: list[ProviderDiagnostic] = []

        if provider_ids:
            # Validate all requested providers first
            for pid in provider_ids:
                if pid not in supported_ids:
                    raise UnknownProviderError(str(pid), supported_str_list)

            for pid in provider_ids:
                provider_diagnostics.append(self._discovery.discover_provider(pid))
        else:
            provider_diagnostics = self._discovery.discover_all()

        platform_info = PlatformInfo(
            system=platform.system(),
            release=platform.release(),
            machine=platform.machine(),
            python_version=platform.python_version(),
        )

        return DoctorReport(
            cortexshift_version=__version__,
            python_version=platform.python_version(),
            platform=platform_info,
            timestamp=datetime.now(UTC),
            providers=provider_diagnostics,
        )

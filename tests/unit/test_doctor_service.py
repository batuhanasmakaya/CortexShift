"""Unit tests for DoctorService application layer."""

import json
import os
import platform

import pytest

from cortexshift.application.doctor import DoctorService, UnknownProviderError
from cortexshift.domain.doctor import AuthenticationStatus, ProviderDiagnostic
from cortexshift.domain.provider import (
    PROVIDER_ANTIGRAVITY,
    PROVIDER_CLAUDE,
    PROVIDER_CODEX,
    ProviderCapabilities,
    ProviderId,
)
from cortexshift.ports.discovery import ProviderDiscoveryPort


class FakeDiscoveryPort(ProviderDiscoveryPort):
    """Fake discovery port providing canned diagnostics."""

    def __init__(self) -> None:
        self.probed_ids: list[ProviderId] = []

    def get_supported_provider_ids(self) -> list[ProviderId]:
        return [PROVIDER_CLAUDE, PROVIDER_CODEX, PROVIDER_ANTIGRAVITY]

    def discover_provider(self, provider_id: ProviderId) -> ProviderDiagnostic:
        self.probed_ids.append(provider_id)
        return ProviderDiagnostic(
            provider_id=provider_id,
            display_name=str(provider_id).capitalize(),
            executable=str(provider_id),
            installed=True,
            resolved_path=f"/bin/{provider_id}",
            version="1.0.0",
            authentication_status=AuthenticationStatus.AUTHENTICATED,
            capabilities=ProviderCapabilities(
                provider_id=provider_id,
                display_name=str(provider_id).capitalize(),
            ),
        )

    def discover_all(self) -> list[ProviderDiagnostic]:
        return [self.discover_provider(pid) for pid in self.get_supported_provider_ids()]


def test_doctor_service_diagnose_all() -> None:
    fake_discovery = FakeDiscoveryPort()
    service = DoctorService(discovery=fake_discovery)

    report = service.run_diagnostics()

    assert len(report.providers) == 3
    assert report.cortexshift_version == "0.1.0"
    assert report.timestamp.tzinfo is not None

    # Check that privacy invariants hold
    # Username, hostname, home directory must NOT be in the report
    try:
        username = os.getlogin()
    except OSError:
        username = "dummy_user"
    hostname = platform.node()

    report_str = report.model_dump_json()
    if username and len(username) > 3:
        assert username not in report_str
    if hostname and len(hostname) > 3:
        assert hostname not in report_str


def test_doctor_service_filter_single_provider() -> None:
    fake_discovery = FakeDiscoveryPort()
    service = DoctorService(discovery=fake_discovery)

    report = service.run_diagnostics(provider_ids=[PROVIDER_CLAUDE])

    assert len(report.providers) == 1
    assert report.providers[0].provider_id == PROVIDER_CLAUDE
    assert fake_discovery.probed_ids == [PROVIDER_CLAUDE]


def test_doctor_service_filter_multiple_providers() -> None:
    fake_discovery = FakeDiscoveryPort()
    service = DoctorService(discovery=fake_discovery)

    report = service.run_diagnostics(provider_ids=[PROVIDER_CODEX, PROVIDER_ANTIGRAVITY])

    assert len(report.providers) == 2
    assert [p.provider_id for p in report.providers] == [PROVIDER_CODEX, PROVIDER_ANTIGRAVITY]


def test_doctor_service_unknown_provider_raises() -> None:
    fake_discovery = FakeDiscoveryPort()
    service = DoctorService(discovery=fake_discovery)

    with pytest.raises(UnknownProviderError) as exc_info:
        service.run_diagnostics(provider_ids=[ProviderId("nonexistent-bot")])

    err_msg = str(exc_info.value)
    assert "nonexistent-bot" in err_msg
    assert "claude" in err_msg
    assert "codex" in err_msg
    assert "antigravity" in err_msg


def test_doctor_report_json_serialization() -> None:
    fake_discovery = FakeDiscoveryPort()
    service = DoctorService(discovery=fake_discovery)

    report = service.run_diagnostics()
    json_data = json.loads(report.model_dump_json())

    assert "cortexshift_version" in json_data
    assert "python_version" in json_data
    assert "platform" in json_data
    assert "system" in json_data["platform"]
    assert "providers" in json_data
    assert len(json_data["providers"]) == 3
    first = json_data["providers"][0]
    assert first["provider_id"] == "claude"
    assert first["installed"] is True
    assert first["authentication_status"] == "authenticated"

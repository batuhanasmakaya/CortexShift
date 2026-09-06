"""Unit tests for cortexshift doctor CLI command."""

import json
from unittest.mock import patch

from cortexshift.cli.app import app
from cortexshift.domain.doctor import (
    AuthenticationStatus,
    DoctorReport,
    PlatformInfo,
    ProviderDiagnostic,
)
from cortexshift.domain.provider import (
    PROVIDER_ANTIGRAVITY,
    PROVIDER_CLAUDE,
    PROVIDER_CODEX,
    ProviderCapabilities,
    ProviderId,
)
from tests.cli_runner import AnsiFreeCliRunner

runner = AnsiFreeCliRunner()


def _make_mock_report() -> DoctorReport:
    """Create a deterministic mock DoctorReport."""
    from datetime import UTC, datetime

    return DoctorReport(
        cortexshift_version="0.1.0",
        python_version="3.12.0",
        platform=PlatformInfo(
            system="Darwin",
            release="24.0.0",
            machine="arm64",
            python_version="3.12.0",
        ),
        timestamp=datetime.now(UTC),
        providers=[
            ProviderDiagnostic(
                provider_id=PROVIDER_CLAUDE,
                display_name="Claude Code",
                executable="claude",
                installed=True,
                resolved_path="/usr/local/bin/claude",
                version="2.1.0",
                authentication_status=AuthenticationStatus.AUTHENTICATED,
                capabilities=ProviderCapabilities(
                    provider_id=PROVIDER_CLAUDE,
                    display_name="Claude Code",
                ),
                diagnostics=[],
            ),
            ProviderDiagnostic(
                provider_id=PROVIDER_CODEX,
                display_name="Codex",
                executable="codex",
                installed=True,
                resolved_path="/usr/local/bin/codex",
                version="0.3.0",
                authentication_status=AuthenticationStatus.NOT_AUTHENTICATED,
                capabilities=ProviderCapabilities(
                    provider_id=PROVIDER_CODEX,
                    display_name="Codex",
                ),
                diagnostics=["Not logged in"],
            ),
            ProviderDiagnostic(
                provider_id=PROVIDER_ANTIGRAVITY,
                display_name="Antigravity",
                executable="agy",
                installed=False,
                resolved_path=None,
                version=None,
                authentication_status=AuthenticationStatus.UNKNOWN,
                capabilities=ProviderCapabilities(
                    provider_id=PROVIDER_ANTIGRAVITY,
                    display_name="Antigravity",
                ),
                diagnostics=["Not found in PATH"],
            ),
            ProviderDiagnostic(
                provider_id=ProviderId("mockagent"),
                display_name="MockAgent",
                executable="mockagent",
                installed=True,
                resolved_path="/usr/local/bin/mockagent",
                version="1.0.0",
                authentication_status=AuthenticationStatus.NOT_PROBED,
                capabilities=ProviderCapabilities(
                    provider_id=ProviderId("mockagent"),
                    display_name="MockAgent",
                ),
                diagnostics=[],
            ),
        ],
    )


def test_doctor_help() -> None:
    result = runner.invoke(app, ["doctor", "--help"])
    assert result.exit_code == 0
    assert "doctor" in result.output
    assert "--json" in result.output
    assert "--provider" in result.output


def test_doctor_human_readable_output() -> None:
    mock_report = _make_mock_report()
    with patch("cortexshift.cli.app.DoctorService.run_diagnostics", return_value=mock_report):
        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 0
        assert "CortexShift Doctor" in result.output
        assert "Claude Code" in result.output
        assert "Codex" in result.output
        assert "Antigravity" in result.output
        assert "3 of 4 providers detected" in result.output
        assert "Not probed" in result.output


def test_doctor_json_output() -> None:
    mock_report = _make_mock_report()
    with patch("cortexshift.cli.app.DoctorService.run_diagnostics", return_value=mock_report):
        result = runner.invoke(app, ["doctor", "--json"])
        assert result.exit_code == 0

        # Output must be clean, valid JSON
        data = json.loads(result.output)
        assert data["cortexshift_version"] == "0.1.0"
        assert len(data["providers"]) == 4
        assert data["providers"][0]["display_name"] == "Claude Code"
        assert data["providers"][0]["authentication_status"] == "authenticated"
        assert data["providers"][1]["authentication_status"] == "not_authenticated"
        assert data["providers"][2]["installed"] is False
        assert data["providers"][3]["authentication_status"] == "not_probed"


def test_doctor_provider_filter_valid() -> None:
    mock_report = _make_mock_report()
    # Retain only claude in filtered report
    filtered_report = mock_report.model_copy(update={"providers": [mock_report.providers[0]]})
    with patch(
        "cortexshift.cli.app.DoctorService.run_diagnostics",
        return_value=filtered_report,
    ) as mock_run:
        result = runner.invoke(app, ["doctor", "--provider", "claude"])
        assert result.exit_code == 0
        assert "Claude Code" in result.output
        mock_run.assert_called_once()
        called_args = mock_run.call_args[1]["provider_ids"]
        assert called_args == [PROVIDER_CLAUDE]


def test_doctor_provider_filter_multiple() -> None:
    mock_report = _make_mock_report()
    with patch(
        "cortexshift.cli.app.DoctorService.run_diagnostics",
        return_value=mock_report,
    ) as mock_run:
        result = runner.invoke(app, ["doctor", "-p", "claude", "-p", "codex"])
        assert result.exit_code == 0
        mock_run.assert_called_once()
        called_args = mock_run.call_args[1]["provider_ids"]
        assert called_args == [PROVIDER_CLAUDE, PROVIDER_CODEX]


def test_doctor_provider_filter_invalid_id() -> None:
    result = runner.invoke(app, ["doctor", "--provider", "not-a-valid-provider-name-!@#$"])
    assert result.exit_code == 2
    assert "Invalid provider ID" in result.output or "Error" in result.output


def test_doctor_provider_filter_unknown_id() -> None:
    result = runner.invoke(app, ["doctor", "--provider", "unsupportedagent"])
    assert result.exit_code == 2
    assert "Error" in result.output
    assert "Supported providers" in result.output or "unsupportedagent" in result.output


def test_doctor_exits_zero_even_when_no_providers_installed() -> None:
    from datetime import UTC, datetime

    empty_report = DoctorReport(
        cortexshift_version="0.1.0",
        python_version="3.12.0",
        platform=PlatformInfo(
            system="Linux",
            release="5.15.0",
            machine="x86_64",
            python_version="3.12.0",
        ),
        timestamp=datetime.now(UTC),
        providers=[
            ProviderDiagnostic(
                provider_id=PROVIDER_CLAUDE,
                display_name="Claude Code",
                executable="claude",
                installed=False,
                authentication_status=AuthenticationStatus.UNKNOWN,
                capabilities=ProviderCapabilities(
                    provider_id=PROVIDER_CLAUDE,
                    display_name="Claude Code",
                ),
                diagnostics=["Not found in PATH"],
            )
        ],
    )
    with patch("cortexshift.cli.app.DoctorService.run_diagnostics", return_value=empty_report):
        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 0
        assert "0 of 1 providers detected" in result.output

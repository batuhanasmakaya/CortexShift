"""Unit tests for native provider probes and discovery adapter."""

from pathlib import Path

from cortexshift.adapters.discovery import BuiltinProviderDiscovery
from cortexshift.adapters.providers.antigravity import AntigravityProviderProbe
from cortexshift.adapters.providers.claude import ClaudeProviderProbe
from cortexshift.adapters.providers.codex import CodexProviderProbe
from cortexshift.domain.doctor import AuthenticationStatus
from cortexshift.domain.provider import (
    PROVIDER_ANTIGRAVITY,
    PROVIDER_CLAUDE,
    PROVIDER_CODEX,
    ProviderId,
)
from cortexshift.ports.command_runner import CommandResult, CommandRunner


class FakeCommandRunner(CommandRunner):
    """Deterministic in-memory command runner for testing."""

    def __init__(self, responses: dict[tuple[str, ...], CommandResult] | None = None) -> None:
        self.responses = responses or {}
        self.executed_commands: list[list[str]] = []

    def run(
        self,
        command: list[str],
        timeout: float = 5.0,
        env: dict[str, str] | None = None,
        cwd: Path | str | None = None,
        sanitize: bool = True,
    ) -> CommandResult:
        self.executed_commands.append(command)
        key = tuple(command)
        if key in self.responses:
            return self.responses[key]
        return CommandResult(
            command=command,
            exit_code=0,
            stdout="",
            stderr="",
        )


# --- Claude Tests ---


def test_claude_not_installed() -> None:
    runner = FakeCommandRunner()
    probe = ClaudeProviderProbe(runner, which_fn=lambda _: None)
    diag = probe.probe()

    assert diag.installed is False
    assert diag.version is None
    assert diag.resolved_path is None
    assert diag.authentication_status == AuthenticationStatus.UNKNOWN
    assert "Not found in PATH" in diag.diagnostics
    assert len(runner.executed_commands) == 0


def test_claude_installed_and_authenticated_json() -> None:
    runner = FakeCommandRunner(
        {
            ("/bin/claude", "--version"): CommandResult(
                command=["/bin/claude", "--version"],
                exit_code=0,
                stdout="claude-code 2.1.3",
                stderr="",
            ),
            ("/bin/claude", "auth", "status"): CommandResult(
                command=["/bin/claude", "auth", "status"],
                exit_code=0,
                stdout='{"loggedIn": true, "email": "secret@example.com"}',
                stderr="",
            ),
        }
    )
    probe = ClaudeProviderProbe(runner, which_fn=lambda _: "/bin/claude")
    diag = probe.probe()

    assert diag.installed is True
    assert diag.resolved_path == "/bin/claude"
    assert diag.version == "2.1.3"
    assert diag.authentication_status == AuthenticationStatus.AUTHENTICATED
    # Ensure sensitive email from stdout is NOT retained anywhere
    assert "secret@example.com" not in str(diag)


def test_claude_installed_and_authenticated_text() -> None:
    runner = FakeCommandRunner(
        {
            ("/bin/claude", "--version"): CommandResult(
                command=["/bin/claude", "--version"],
                exit_code=0,
                stdout="2.0.0",
                stderr="",
            ),
            ("/bin/claude", "auth", "status"): CommandResult(
                command=["/bin/claude", "auth", "status"],
                exit_code=0,
                stdout="Logged in as user@work.com",
                stderr="",
            ),
        }
    )
    probe = ClaudeProviderProbe(runner, which_fn=lambda _: "/bin/claude")
    diag = probe.probe()

    assert diag.installed is True
    assert diag.version == "2.0.0"
    assert diag.authentication_status == AuthenticationStatus.AUTHENTICATED
    assert "user@work.com" not in str(diag)


def test_claude_installed_not_authenticated() -> None:
    runner = FakeCommandRunner(
        {
            ("/bin/claude", "--version"): CommandResult(
                command=["/bin/claude", "--version"],
                exit_code=0,
                stdout="2.1.0",
                stderr="",
            ),
            ("/bin/claude", "auth", "status"): CommandResult(
                command=["/bin/claude", "auth", "status"],
                exit_code=1,
                stdout="",
                stderr="Not logged in. Run claude auth login.",
            ),
        }
    )
    probe = ClaudeProviderProbe(runner, which_fn=lambda _: "/bin/claude")
    diag = probe.probe()

    assert diag.installed is True
    assert diag.authentication_status == AuthenticationStatus.NOT_AUTHENTICATED


def test_claude_version_failure_degrades_gracefully() -> None:
    runner = FakeCommandRunner(
        {
            ("/bin/claude", "--version"): CommandResult(
                command=["/bin/claude", "--version"],
                exit_code=1,
                stdout="",
                stderr="Internal crash",
            ),
            ("/bin/claude", "auth", "status"): CommandResult(
                command=["/bin/claude", "auth", "status"],
                exit_code=0,
                stdout="Authenticated",
                stderr="",
            ),
        }
    )
    probe = ClaudeProviderProbe(runner, which_fn=lambda _: "/bin/claude")
    diag = probe.probe()

    assert diag.installed is True
    assert diag.version is None
    assert "Version probe failed" in diag.diagnostics
    assert diag.authentication_status == AuthenticationStatus.AUTHENTICATED


def test_claude_timeout_handling() -> None:
    runner = FakeCommandRunner(
        {
            ("/bin/claude", "--version"): CommandResult(
                command=["/bin/claude", "--version"],
                exit_code=-1,
                stdout="",
                stderr="",
                timed_out=True,
            ),
            ("/bin/claude", "auth", "status"): CommandResult(
                command=["/bin/claude", "auth", "status"],
                exit_code=-1,
                stdout="",
                stderr="",
                timed_out=True,
            ),
        }
    )
    probe = ClaudeProviderProbe(runner, which_fn=lambda _: "/bin/claude")
    diag = probe.probe()

    assert diag.installed is True
    assert diag.version is None
    assert diag.authentication_status == AuthenticationStatus.UNKNOWN
    assert "Version probe timed out" in diag.diagnostics
    assert "Authentication probe timed out" in diag.diagnostics


# --- Codex Tests ---


def test_codex_not_installed() -> None:
    runner = FakeCommandRunner()
    probe = CodexProviderProbe(runner, which_fn=lambda _: None)
    diag = probe.probe()

    assert diag.installed is False
    assert diag.version is None
    assert diag.authentication_status == AuthenticationStatus.UNKNOWN


def test_codex_installed_and_authenticated() -> None:
    runner = FakeCommandRunner(
        {
            ("/bin/codex", "--version"): CommandResult(
                command=["/bin/codex", "--version"],
                exit_code=0,
                stdout="codex-cli 0.4.1",
                stderr="",
            ),
            ("/bin/codex", "login", "status"): CommandResult(
                command=["/bin/codex", "login", "status"],
                exit_code=0,
                stdout='{"authenticated": true}',
                stderr="",
            ),
        }
    )
    probe = CodexProviderProbe(runner, which_fn=lambda _: "/bin/codex")
    diag = probe.probe()

    assert diag.installed is True
    assert diag.version == "0.4.1"
    assert diag.authentication_status == AuthenticationStatus.AUTHENTICATED


def test_codex_installed_not_authenticated() -> None:
    runner = FakeCommandRunner(
        {
            ("/bin/codex", "--version"): CommandResult(
                command=["/bin/codex", "--version"],
                exit_code=0,
                stdout="0.4.0",
                stderr="",
            ),
            ("/bin/codex", "login", "status"): CommandResult(
                command=["/bin/codex", "login", "status"],
                exit_code=1,
                stdout="No active session found. Please run codex login.",
                stderr="",
            ),
        }
    )
    probe = CodexProviderProbe(runner, which_fn=lambda _: "/bin/codex")
    diag = probe.probe()

    assert diag.installed is True
    assert diag.authentication_status == AuthenticationStatus.NOT_AUTHENTICATED


def test_codex_auth_edge_cases() -> None:
    # Invalid JSON in auth
    runner = FakeCommandRunner(
        {
            ("/bin/codex", "--version"): CommandResult(
                command=["/bin/codex", "--version"],
                exit_code=0,
                stdout="0.4.0",
                stderr="",
            ),
            ("/bin/codex", "login", "status"): CommandResult(
                command=["/bin/codex", "login", "status"],
                exit_code=1,
                stdout="{not valid json",
                stderr="Unauthorized access",
            ),
        }
    )
    probe = CodexProviderProbe(runner, which_fn=lambda _: "/bin/codex")
    diag = probe.probe()
    assert diag.authentication_status == AuthenticationStatus.NOT_AUTHENTICATED

    # Timeout
    runner_timeout = FakeCommandRunner(
        {
            ("/bin/codex", "--version"): CommandResult(
                command=["/bin/codex", "--version"],
                exit_code=-1,
                stdout="",
                stderr="",
                timed_out=True,
            ),
            ("/bin/codex", "login", "status"): CommandResult(
                command=["/bin/codex", "login", "status"],
                exit_code=-1,
                stdout="",
                stderr="",
                timed_out=True,
            ),
        }
    )
    probe_timeout = CodexProviderProbe(runner_timeout, which_fn=lambda _: "/bin/codex")
    diag_timeout = probe_timeout.probe()
    assert diag_timeout.version is None
    assert diag_timeout.authentication_status == AuthenticationStatus.UNKNOWN
    assert "Version probe timed out" in diag_timeout.diagnostics
    assert "Authentication probe timed out" in diag_timeout.diagnostics


def test_claude_auth_edge_cases() -> None:
    runner = FakeCommandRunner(
        {
            ("/bin/claude", "--version"): CommandResult(
                command=["/bin/claude", "--version"],
                exit_code=0,
                stdout="2.1.0",
                stderr="",
            ),
            ("/bin/claude", "auth", "status"): CommandResult(
                command=["/bin/claude", "auth", "status"],
                exit_code=1,
                stdout="{not valid json",
                stderr="unauthorized request",
            ),
        }
    )
    probe = ClaudeProviderProbe(runner, which_fn=lambda _: "/bin/claude")
    diag = probe.probe()
    assert diag.authentication_status == AuthenticationStatus.NOT_AUTHENTICATED


def test_antigravity_version_failure_and_timeout() -> None:
    runner_fail = FakeCommandRunner(
        {
            ("/bin/agy", "--version"): CommandResult(
                command=["/bin/agy", "--version"],
                exit_code=1,
                stdout="",
                stderr="crash",
            ),
        }
    )
    probe_fail = AntigravityProviderProbe(runner_fail, which_fn=lambda _: "/bin/agy")
    diag_fail = probe_fail.probe()
    assert diag_fail.version is None
    assert "Version probe failed" in diag_fail.diagnostics

    runner_timeout = FakeCommandRunner(
        {
            ("/bin/agy", "--version"): CommandResult(
                command=["/bin/agy", "--version"],
                exit_code=-1,
                stdout="",
                stderr="",
                timed_out=True,
            ),
        }
    )
    probe_timeout = AntigravityProviderProbe(runner_timeout, which_fn=lambda _: "/bin/agy")
    diag_timeout = probe_timeout.probe()
    assert diag_timeout.version is None
    assert "Version probe timed out" in diag_timeout.diagnostics


# --- Antigravity Tests ---


def test_antigravity_not_installed() -> None:
    runner = FakeCommandRunner()
    probe = AntigravityProviderProbe(runner, which_fn=lambda _: None)
    diag = probe.probe()

    assert diag.installed is False
    assert diag.version is None
    assert diag.executable == "agy"
    assert diag.provider_id == PROVIDER_ANTIGRAVITY
    assert diag.authentication_status == AuthenticationStatus.UNKNOWN


def test_antigravity_installed() -> None:
    runner = FakeCommandRunner(
        {
            ("/bin/agy", "--version"): CommandResult(
                command=["/bin/agy", "--version"],
                exit_code=0,
                stdout="Antigravity 1.2.0-preview",
                stderr="",
            ),
        }
    )
    probe = AntigravityProviderProbe(runner, which_fn=lambda _: "/bin/agy")
    diag = probe.probe()

    assert diag.installed is True
    assert diag.version == "1.2.0-preview"
    assert diag.authentication_status == AuthenticationStatus.UNKNOWN
    assert "Passive authentication probe not supported" in diag.diagnostics


def test_antigravity_never_invokes_model_prompt_regression() -> None:
    """CRITICAL REGRESSION TEST:

    Asserts that doctor discovery never builds or executes any command
    invoking `agy -p ...`, `--prompt`, or model prompt calls.
    """
    runner = FakeCommandRunner(
        {
            ("/bin/agy", "--version"): CommandResult(
                command=["/bin/agy", "--version"],
                exit_code=0,
                stdout="agy 1.0.0",
                stderr="",
            ),
        }
    )
    probe = AntigravityProviderProbe(runner, which_fn=lambda _: "/bin/agy")
    diag = probe.probe()

    assert diag.installed is True

    # Inspect every command executed by Antigravity probe
    for cmd in runner.executed_commands:
        assert "-p" not in cmd, f"Forbidden flag '-p' used in command: {cmd}"
        assert "--prompt" not in cmd, f"Forbidden flag '--prompt' used in command: {cmd}"
        for arg in cmd:
            assert "prompt" not in arg.lower()


# --- Security Invariant: No Credential Files Read ---


def test_provider_probes_never_read_credential_files(monkeypatch) -> None:
    """CRITICAL REGRESSION TEST:

    Asserts that no provider probe attempts to open known credential storage paths
    like ~/.codex/auth.json, ~/.claude, or ~/.gemini.
    """
    import builtins

    real_open = builtins.open
    opened_paths: list[str] = []

    def guarded_open(file, *args, **kwargs):
        path_str = str(file)
        opened_paths.append(path_str)
        forbidden = [".codex", ".claude", ".gemini", "keychain"]
        for f in forbidden:
            if f in path_str.lower():
                raise AssertionError(
                    f"Security invariant violated! Tried to open credential file: {path_str}"
                )
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)

    runner = FakeCommandRunner()
    discovery = BuiltinProviderDiscovery(
        command_runner=runner,
        which_fn=lambda exe: f"/bin/{exe}",
    )
    diagnostics = discovery.discover_all()
    assert len(diagnostics) == 3


# --- BuiltinProviderDiscovery Tests ---


def test_builtin_provider_discovery_all() -> None:
    runner = FakeCommandRunner()
    discovery = BuiltinProviderDiscovery(
        command_runner=runner,
        which_fn=lambda exe: f"/bin/{exe}",
    )

    supported = discovery.get_supported_provider_ids()
    assert supported == [PROVIDER_CLAUDE, PROVIDER_CODEX, PROVIDER_ANTIGRAVITY]

    diags = discovery.discover_all()
    assert len(diags) == 3
    ids = [d.provider_id for d in diags]
    assert ids == [PROVIDER_CLAUDE, PROVIDER_CODEX, PROVIDER_ANTIGRAVITY]


def test_builtin_provider_discovery_single() -> None:
    runner = FakeCommandRunner()
    discovery = BuiltinProviderDiscovery(
        command_runner=runner,
        which_fn=lambda exe: f"/bin/{exe}",
    )

    diag = discovery.discover_provider(PROVIDER_CLAUDE)
    assert diag.provider_id == PROVIDER_CLAUDE


def test_builtin_provider_discovery_unknown() -> None:
    import pytest

    runner = FakeCommandRunner()
    discovery = BuiltinProviderDiscovery(runner)

    with pytest.raises(KeyError):
        discovery.discover_provider(ProviderId("unsupported-agent"))

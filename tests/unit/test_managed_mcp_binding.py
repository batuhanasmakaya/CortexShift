"""Unit tests for the trusted managed MCP session binding stamped at provider launch.

A provider CLI spawns the CortexShift MCP server itself, as a grandchild of CortexShift.
Whether that grandchild inherits CortexShift's process environment is decided by the
provider, not by CortexShift: Codex spawns MCP servers with a sanitized environment plus
whatever the per-server config declares. The managed binding must therefore be written
into the per-launch MCP configuration CortexShift generates, not merely exported into the
provider process.
"""

import json
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest

from cortexshift.adapters.providers.antigravity import AntigravityRuntimeAdapter
from cortexshift.adapters.providers.claude import (
    ClaudeHandoffAdapter,
    ClaudeRuntimeAdapter,
    build_claude_mcp_config,
)
from cortexshift.adapters.providers.codex import (
    MCP_SERVER_KEY,
    CodexHandoffAdapter,
    CodexRuntimeAdapter,
    build_codex_mcp_args,
)
from cortexshift.application.session_launcher import ProviderSessionLauncher
from cortexshift.domain.mcp_binding import (
    ENV_MCP_READ_ONLY,
    ENV_PROJECT_ROOT,
    ENV_PROVIDER_ID,
    ENV_SESSION_ID,
    ENV_TASK_ID,
    McpSessionBinding,
)
from cortexshift.domain.provider import PROVIDER_CLAUDE, PROVIDER_CODEX, ProviderId
from cortexshift.domain.session import Session, SessionStatus
from cortexshift.ports.process_runner import InteractiveProcessRunner
from cortexshift.ports.provider import ManagedMcpBinder
from cortexshift.ports.session_store import SessionStore


class RecordingProcessRunner(InteractiveProcessRunner):
    """Captures the exact argv and environment a provider process would be spawned with."""

    def __init__(self, exit_code: int = 0) -> None:
        self.exit_code = exit_code
        self.invocations: list[dict[str, Any]] = []

    def run_interactive(
        self, argv: list[str], cwd: Path | str, env: dict[str, str] | None = None
    ) -> int:
        self.invocations.append({"argv": list(argv), "cwd": cwd, "env": dict(env or {})})
        return self.exit_code


class InMemorySessionStore(SessionStore):
    """Minimal SessionStore double for exercising the launcher in isolation."""

    def __init__(self) -> None:
        self.sessions: dict[str, Session] = {}

    def save_session(self, session: Session) -> None:
        self.sessions[session.id] = session

    def get_session(self, session_id: str) -> Session | None:
        return self.sessions.get(session_id)

    def list_sessions(
        self,
        project_id: str | None = None,
        task_id: str | None = None,
        limit: int | None = 20,
    ) -> list[Session]:
        found = [s for s in self.sessions.values() if task_id is None or s.task_id == task_id]
        return found[:limit] if limit is not None else found


def _binding(tmp_path: Path, provider: str = "codex") -> McpSessionBinding:
    return McpSessionBinding(
        project_root=tmp_path,
        task_id="task_abc",
        session_id="sess_fd421189d75745d488a5697dae48dfcd",
        provider_id=ProviderId(provider),
    )


def _codex_mcp_table(argv: list[str]) -> dict[str, Any]:
    """Parse the `-c` overrides Codex would apply, exactly as Codex parses them (TOML)."""
    values = [argv[i + 1] for i, arg in enumerate(argv) if arg == "-c"]
    config: dict[str, Any] = tomllib.loads("\n".join(values))
    server: dict[str, Any] = config["mcp_servers"]["cortexshift"]
    return server


def _claude_mcp_server(argv: list[str]) -> dict[str, Any]:
    idx = argv.index("--mcp-config")
    data: dict[str, Any] = json.loads(argv[idx + 1])
    server: dict[str, Any] = data["mcpServers"]["cortexshift"]
    return server


# ---------------------------------------------------------------------------
# The binding value object itself
# ---------------------------------------------------------------------------


def test_binding_env_carries_every_context_variable(tmp_path: Path) -> None:
    env = _binding(tmp_path).to_env()

    assert env == {
        ENV_PROJECT_ROOT: str(tmp_path),
        ENV_TASK_ID: "task_abc",
        ENV_SESSION_ID: "sess_fd421189d75745d488a5697dae48dfcd",
        ENV_PROVIDER_ID: "codex",
        ENV_MCP_READ_ONLY: "0",
    }


def test_binding_env_marks_read_only_launches(tmp_path: Path) -> None:
    binding = _binding(tmp_path).model_copy(update={"read_only": True})
    assert binding.to_env()[ENV_MCP_READ_ONLY] == "1"


def test_binding_from_session_uses_persisted_session_identity(tmp_path: Path) -> None:
    session = Session(task_id="task_abc", provider_id=PROVIDER_CODEX)
    binding = McpSessionBinding.from_session(session=session, project_root=tmp_path)

    assert binding.session_id == session.id
    assert binding.task_id == "task_abc"
    assert binding.provider_id == PROVIDER_CODEX
    assert binding.project_root == tmp_path


# ---------------------------------------------------------------------------
# A. Managed Codex launch config propagation
# ---------------------------------------------------------------------------


def test_codex_managed_launch_config_carries_full_binding(tmp_path: Path) -> None:
    adapter = CodexRuntimeAdapter()
    spec = adapter.build_launch_spec(project_root=tmp_path, executable_path="/bin/codex")

    bound = adapter.bind_managed_mcp(spec, _binding(tmp_path))
    server = _codex_mcp_table(bound.argv)

    assert server["command"] == sys.executable
    assert server["args"] == ["-m", "cortexshift", "mcp", "serve"]
    assert server["required"] is True
    assert server["env"] == {
        ENV_PROJECT_ROOT: str(tmp_path),
        ENV_TASK_ID: "task_abc",
        ENV_SESSION_ID: "sess_fd421189d75745d488a5697dae48dfcd",
        ENV_PROVIDER_ID: "codex",
        ENV_MCP_READ_ONLY: "0",
    }


def test_codex_binding_does_not_duplicate_or_drop_other_arguments(tmp_path: Path) -> None:
    adapter = CodexRuntimeAdapter()
    spec = adapter.build_launch_spec(
        project_root=tmp_path, executable_path="/bin/codex", prompt="Continue the task"
    )

    bound = adapter.bind_managed_mcp(spec, _binding(tmp_path))

    assert bound.argv[0] == "/bin/codex"
    assert bound.argv[-1] == "Continue the task"
    assert bound.prompt_supplied is True
    # Exactly one MCP server command override survives the rewrite.
    overrides = [bound.argv[i + 1] for i, a in enumerate(bound.argv) if a == "-c"]
    assert sum(1 for o in overrides if o.startswith("mcp_servers.cortexshift.command=")) == 1
    assert bound.to_redacted_argv()[-1] == "<prompt>"


def test_codex_exact_resume_config_carries_binding(tmp_path: Path) -> None:
    adapter = CodexRuntimeAdapter()
    native_id = "88888888-8888-4888-8888-888888888888"
    spec = adapter.build_exact_resume(tmp_path, "/bin/codex", native_id)

    bound = adapter.bind_managed_mcp(spec, _binding(tmp_path))

    assert _codex_mcp_table(bound.argv)["env"][ENV_SESSION_ID] == (
        "sess_fd421189d75745d488a5697dae48dfcd"
    )
    assert bound.argv[-2:] == ["resume", native_id]


def test_codex_handoff_adapter_binds_managed_mcp(tmp_path: Path) -> None:
    adapter = CodexHandoffAdapter()
    spec = CodexRuntimeAdapter().build_launch_spec(
        project_root=tmp_path, executable_path="/bin/codex"
    )

    bound = adapter.bind_managed_mcp(spec, _binding(tmp_path))

    assert _codex_mcp_table(bound.argv)["env"][ENV_TASK_ID] == "task_abc"


def test_codex_binding_survives_toml_parsing_of_awkward_paths(tmp_path: Path) -> None:
    """Codex parses `-c` values as TOML; Windows paths and quotes must round-trip."""
    for root in [
        r"C:\Users\developer\My Tools\repo",
        '/opt/My "Tools"/repo',
        r"C:\Tools 🌟\repo",
    ]:
        binding = McpSessionBinding(
            project_root=Path(root),
            task_id="task_abc",
            session_id="sess_1",
            provider_id=PROVIDER_CODEX,
        )
        server = _codex_mcp_table(build_codex_mcp_args("/usr/bin/python3", binding=binding))
        assert server["env"][ENV_PROJECT_ROOT] == str(Path(root))


# ---------------------------------------------------------------------------
# B. Managed Claude launch config propagation
# ---------------------------------------------------------------------------


def test_claude_managed_launch_config_carries_full_binding(tmp_path: Path) -> None:
    adapter = ClaudeRuntimeAdapter()
    spec = adapter.build_launch_spec(project_root=tmp_path, executable_path="/bin/claude")

    bound = adapter.bind_managed_mcp(spec, _binding(tmp_path, provider="claude"))
    server = _claude_mcp_server(bound.argv)

    assert server["command"] == sys.executable
    assert server["args"] == ["-m", "cortexshift", "mcp", "serve"]
    assert server["env"] == {
        ENV_PROJECT_ROOT: str(tmp_path),
        ENV_TASK_ID: "task_abc",
        ENV_SESSION_ID: "sess_fd421189d75745d488a5697dae48dfcd",
        ENV_PROVIDER_ID: "claude",
        ENV_MCP_READ_ONLY: "0",
    }


def test_claude_binding_preserves_native_session_id_and_prompt(tmp_path: Path) -> None:
    adapter = ClaudeRuntimeAdapter()
    spec = adapter.build_launch_spec(
        project_root=tmp_path, executable_path="/bin/claude", prompt="Canonical handoff"
    )

    bound = adapter.bind_managed_mcp(spec, _binding(tmp_path, provider="claude"))

    assert bound.native_session_id == spec.native_session_id
    assert "--session-id" in bound.argv
    assert bound.argv[bound.argv.index("--session-id") + 1] == spec.native_session_id
    assert bound.argv[-1] == "Canonical handoff"


def test_claude_handoff_adapter_binds_managed_mcp(tmp_path: Path) -> None:
    adapter = ClaudeHandoffAdapter()
    prep = adapter.prepare_delivery(
        executable_path="/bin/claude",
        project_root=tmp_path,
        rendered_context="Handoff context",
    )

    bound = adapter.bind_managed_mcp(prep.launch_spec, _binding(tmp_path, provider="claude"))

    assert _claude_mcp_server(bound.argv)["env"][ENV_SESSION_ID] == (
        "sess_fd421189d75745d488a5697dae48dfcd"
    )


# ---------------------------------------------------------------------------
# Unbound configuration stays unmanaged
# ---------------------------------------------------------------------------


def test_unbound_provider_configs_declare_no_managed_environment() -> None:
    """Without a binding, the generated config must not claim managed execution."""
    assert "env" not in _claude_mcp_server(["--mcp-config", build_claude_mcp_config()])
    assert "env" not in _codex_mcp_table(build_codex_mcp_args())


def test_antigravity_does_not_implement_the_optional_binder_port(tmp_path: Path) -> None:
    """Antigravity is configured through its workspace file, not per-launch CLI flags."""
    adapter = AntigravityRuntimeAdapter()
    spec = adapter.build_launch_spec(project_root=tmp_path, executable_path="/bin/antigravity")

    assert not isinstance(adapter, ManagedMcpBinder)
    assert isinstance(CodexRuntimeAdapter(), ManagedMcpBinder)
    assert isinstance(ClaudeRuntimeAdapter(), ManagedMcpBinder)
    assert spec.argv == ["/bin/antigravity"]


# ---------------------------------------------------------------------------
# The launcher is the only component that mints a binding
# ---------------------------------------------------------------------------


def test_launcher_binds_provider_mcp_config_to_the_started_session(tmp_path: Path) -> None:
    runner = RecordingProcessRunner()
    store = InMemorySessionStore()
    launcher = ProviderSessionLauncher(process_runner=runner, store=store)
    adapter = CodexRuntimeAdapter()

    session = launcher.start_session(task_id="task_abc", provider_id=PROVIDER_CODEX)
    spec = adapter.build_launch_spec(project_root=tmp_path, executable_path="/bin/codex")

    finished = launcher.run(session, spec, mcp_binder=adapter)

    assert finished.status == SessionStatus.COMPLETED
    invocation = runner.invocations[0]

    # The MCP grandchild is configured explicitly, independent of env inheritance.
    server = _codex_mcp_table(invocation["argv"])
    assert server["env"][ENV_SESSION_ID] == session.id
    assert server["env"][ENV_TASK_ID] == "task_abc"
    assert server["env"][ENV_PROVIDER_ID] == "codex"
    assert server["env"][ENV_PROJECT_ROOT] == str(tmp_path)
    assert server["env"][ENV_MCP_READ_ONLY] == "0"

    # The provider process environment keeps carrying the same binding.
    assert invocation["env"][ENV_SESSION_ID] == session.id


def test_launcher_observer_sees_the_bound_specification(tmp_path: Path) -> None:
    runner = RecordingProcessRunner()
    store = InMemorySessionStore()
    launcher = ProviderSessionLauncher(process_runner=runner, store=store)
    adapter = CodexRuntimeAdapter()

    session = launcher.start_session(task_id="task_abc", provider_id=PROVIDER_CODEX)
    spec = adapter.build_launch_spec(project_root=tmp_path, executable_path="/bin/codex")

    observed: list[list[str]] = []
    launcher.run(
        session,
        spec,
        on_launch=lambda s, _sess: observed.append(list(s.argv)),
        mcp_binder=adapter,
    )

    assert _codex_mcp_table(observed[0])["env"][ENV_SESSION_ID] == session.id


def test_launcher_without_binder_leaves_the_specification_alone(tmp_path: Path) -> None:
    runner = RecordingProcessRunner()
    store = InMemorySessionStore()
    launcher = ProviderSessionLauncher(process_runner=runner, store=store)

    session = launcher.start_session(task_id="task_abc", provider_id=PROVIDER_CLAUDE)
    spec = ClaudeRuntimeAdapter().build_launch_spec(
        project_root=tmp_path, executable_path="/bin/claude"
    )

    launcher.run(session, spec, mcp_binder=None)

    assert "env" not in _claude_mcp_server(runner.invocations[0]["argv"])


def test_launcher_binding_overrides_adapter_supplied_context_variables(tmp_path: Path) -> None:
    """An adapter cannot forge a different session, task, or execution mode."""
    runner = RecordingProcessRunner()
    store = InMemorySessionStore()
    launcher = ProviderSessionLauncher(process_runner=runner, store=store)
    adapter = CodexRuntimeAdapter()

    session = launcher.start_session(task_id="task_abc", provider_id=PROVIDER_CODEX)
    spec = adapter.build_launch_spec(
        project_root=tmp_path, executable_path="/bin/codex"
    ).model_copy(
        update={
            "env": {
                ENV_SESSION_ID: "sess_forged",
                ENV_MCP_READ_ONLY: "0",
                "UNRELATED_PASSTHROUGH": "kept",
            }
        }
    )

    launcher.run(session, spec, mcp_binder=adapter)

    env = runner.invocations[0]["env"]
    assert env[ENV_SESSION_ID] == session.id
    assert env["UNRELATED_PASSTHROUGH"] == "kept"
    assert _codex_mcp_table(runner.invocations[0]["argv"])["env"][ENV_SESSION_ID] == session.id


def test_binder_ignores_specifications_from_a_different_provider(tmp_path: Path) -> None:
    claude_spec = ClaudeRuntimeAdapter().build_launch_spec(
        project_root=tmp_path, executable_path="/bin/claude"
    )

    bound = CodexRuntimeAdapter().bind_managed_mcp(claude_spec, _binding(tmp_path))

    assert bound.argv == claude_spec.argv


def test_codex_binder_never_rewrites_a_prompt_that_looks_like_an_override(
    tmp_path: Path,
) -> None:
    """The initial prompt is arbitrary text and must survive binding untouched."""
    hostile = f'-c {MCP_SERVER_KEY}.env.CORTEXSHIFT_MCP_READ_ONLY="1"'
    spec = CodexRuntimeAdapter().build_launch_spec(
        project_root=tmp_path, executable_path="/bin/codex", prompt=hostile
    )

    bound = CodexRuntimeAdapter().bind_managed_mcp(spec, _binding(tmp_path))

    assert bound.argv[-1] == hostile
    assert _codex_mcp_table(bound.argv[:-1])["env"][ENV_MCP_READ_ONLY] == "0"


def test_claude_binder_never_rewrites_a_prompt_that_looks_like_a_flag(tmp_path: Path) -> None:
    """The initial prompt is arbitrary text and must survive binding untouched."""
    hostile = "--mcp-config"
    spec = ClaudeRuntimeAdapter().build_launch_spec(
        project_root=tmp_path, executable_path="/bin/claude", prompt=hostile
    )

    bound = ClaudeRuntimeAdapter().bind_managed_mcp(spec, _binding(tmp_path, provider="claude"))

    assert bound.argv[-1] == hostile
    assert _claude_mcp_server(bound.argv)["env"][ENV_SESSION_ID] == (
        "sess_fd421189d75745d488a5697dae48dfcd"
    )


def test_binder_leaves_a_specification_without_mcp_configuration_untouched(tmp_path: Path) -> None:
    spec = CodexRuntimeAdapter().build_launch_spec(
        project_root=tmp_path, executable_path="/bin/codex"
    )
    stripped = spec.model_copy(update={"argv": ["/bin/codex"]})

    assert CodexRuntimeAdapter().bind_managed_mcp(stripped, _binding(tmp_path)).argv == [
        "/bin/codex"
    ]


@pytest.mark.parametrize("field", ["task_id", "session_id"])
def test_binding_rejects_blank_identity(tmp_path: Path, field: str) -> None:
    values: dict[str, Any] = {
        "project_root": tmp_path,
        "task_id": "task_abc",
        "session_id": "sess_abc",
        "provider_id": "codex",
        field: "   ",
    }
    with pytest.raises(ValueError):
        McpSessionBinding(**values)

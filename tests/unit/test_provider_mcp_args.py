"""Unit tests verifying provider-specific MCP argument construction and warnings."""

import json
import sys
from pathlib import Path

from cortexshift.adapters.providers.antigravity import (
    AntigravityRuntimeAdapter,
)
from cortexshift.adapters.providers.claude import (
    ClaudeHandoffAdapter,
    ClaudeRuntimeAdapter,
)
from cortexshift.adapters.providers.codex import (
    CodexHandoffAdapter,
    CodexRuntimeAdapter,
)


def test_claude_runtime_argv_contains_mcp_config(tmp_path: Path) -> None:
    adapter = ClaudeRuntimeAdapter()
    spec = adapter.build_launch_spec(
        project_root=tmp_path,
        executable_path="/bin/claude",
    )

    assert "--mcp-config" in spec.argv
    idx = spec.argv.index("--mcp-config")
    mcp_config_raw = spec.argv[idx + 1]

    data = json.loads(mcp_config_raw)
    assert "mcpServers" in data
    assert "cortexshift" in data["mcpServers"]
    cs = data["mcpServers"]["cortexshift"]
    assert cs["command"] == sys.executable
    assert cs["args"] == ["-m", "cortexshift", "mcp", "serve"]


def test_claude_handoff_argv_contains_mcp_config(tmp_path: Path) -> None:
    adapter = ClaudeHandoffAdapter()
    prep = adapter.prepare_delivery(
        executable_path="/bin/claude",
        project_root=tmp_path,
        rendered_context="Handoff context",
    )

    assert "--mcp-config" in prep.launch_spec.argv
    idx = prep.launch_spec.argv.index("--mcp-config")
    data = json.loads(prep.launch_spec.argv[idx + 1])
    assert "cortexshift" in data["mcpServers"]


def test_codex_runtime_argv_contains_mcp_flags(tmp_path: Path) -> None:
    adapter = CodexRuntimeAdapter()
    spec = adapter.build_launch_spec(
        project_root=tmp_path,
        executable_path="/bin/codex",
    )

    argv = spec.argv
    c_indices = [i for i, a in enumerate(argv) if a == "-c"]
    c_values = [argv[i + 1] for i in c_indices]

    cmd_val = f'mcp_servers.cortexshift.command="{sys.executable}"'
    assert cmd_val in c_values

    args_val = 'mcp_servers.cortexshift.args=["-m", "cortexshift", "mcp", "serve"]'
    assert args_val in c_values

    assert "mcp_servers.cortexshift.required=true" in c_values


def test_codex_handoff_argv_contains_mcp_flags(tmp_path: Path) -> None:
    adapter = CodexHandoffAdapter()
    prep = adapter.prepare_delivery(
        executable_path="/bin/codex",
        project_root=tmp_path,
        rendered_context="Handoff context",
    )

    argv = prep.launch_spec.argv
    c_indices = [i for i, a in enumerate(argv) if a == "-c"]
    c_values = [argv[i + 1] for i in c_indices]
    assert f'mcp_servers.cortexshift.command="{sys.executable}"' in c_values


def test_antigravity_runtime_no_cli_mcp_flags(tmp_path: Path) -> None:
    adapter = AntigravityRuntimeAdapter()
    spec = adapter.build_launch_spec(
        project_root=tmp_path,
        executable_path="/bin/antigravity",
    )

    # Antigravity does not accept CLI flags for MCP
    assert "--mcp-config" not in spec.argv
    assert "-c" not in spec.argv
    assert spec.argv == ["/bin/antigravity"]

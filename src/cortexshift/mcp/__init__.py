"""CortexShift Model Context Protocol (MCP) shared state integration."""

from cortexshift.mcp.context import (
    ENV_MCP_READ_ONLY,
    ENV_PROJECT_ROOT,
    ENV_PROVIDER_ID,
    ENV_SESSION_ID,
    ENV_TASK_ID,
    McpExecutionContext,
    resolve_mcp_context,
)
from cortexshift.mcp.facade import McpApplicationFacade
from cortexshift.mcp.models import (
    CheckpointResult,
    CreateCheckpointResult,
    DecisionResult,
    ProjectContextResult,
    TaskMutationResult,
)
from cortexshift.mcp.server import create_mcp_server, run_mcp_server

__all__ = [
    "ENV_MCP_READ_ONLY",
    "ENV_PROJECT_ROOT",
    "ENV_PROVIDER_ID",
    "ENV_SESSION_ID",
    "ENV_TASK_ID",
    "CheckpointResult",
    "CreateCheckpointResult",
    "DecisionResult",
    "McpApplicationFacade",
    "McpExecutionContext",
    "ProjectContextResult",
    "TaskMutationResult",
    "create_mcp_server",
    "resolve_mcp_context",
    "run_mcp_server",
]

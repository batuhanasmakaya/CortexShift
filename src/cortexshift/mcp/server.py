"""MCPServer initialization and stdio execution for CortexShift."""

from mcp.server.mcpserver import MCPServer

from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.mcp.context import McpExecutionContext
from cortexshift.mcp.facade import McpApplicationFacade
from cortexshift.mcp.resources import register_resources
from cortexshift.mcp.tools import register_tools

SERVER_NAME = "cortexshift"
SERVER_TITLE = "CortexShift"

SERVER_INSTRUCTIONS = """\
CortexShift exposes structured project and task state.
Use repository files and live Git as the highest authority.
Use write tools after meaningful milestones to keep canonical Task state current.
Do not record trivial edits as milestones.
Checkpoint meaningful engineering decisions and reported test status.
"""


def create_mcp_server(
    context: McpExecutionContext,
    store: SQLiteStateStore,
) -> MCPServer:
    """Create and configure a CortexShift MCPServer instance for the given context."""
    server = MCPServer(
        name=SERVER_NAME,
        title=SERVER_TITLE,
        instructions=SERVER_INSTRUCTIONS,
    )

    facade = McpApplicationFacade(context=context, store=store)
    register_tools(server=server, facade=facade, context=context)
    register_resources(server=server, facade=facade)

    return server


def run_mcp_server(
    context: McpExecutionContext,
    store: SQLiteStateStore,
) -> None:
    """Run the CortexShift MCP server over stdio until EOF or process termination.

    Invariants:
    - Standard output is reserved strictly for the MCP wire protocol.
    - Diagnostics and logs route exclusively to standard error.
    """
    server = create_mcp_server(context=context, store=store)
    server.run(transport="stdio")

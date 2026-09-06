"""MCP resource definitions and registration for CortexShift."""

import json

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ResourceError

from cortexshift.domain.errors import CortexShiftError
from cortexshift.mcp.facade import McpApplicationFacade


def register_resources(server: MCPServer, facade: McpApplicationFacade) -> None:
    """Register read-only MCP resource views backed by McpApplicationFacade."""

    @server.resource("cortexshift://project", mime_type="application/json")
    def get_project_resource() -> str:
        """Read-only view of project and bounded task/session summary."""
        try:
            return facade.get_project_context().model_dump_json(indent=2)
        except (CortexShiftError, ValueError) as err:
            raise ResourceError(f"Failed to read project resource: {err}") from err

    @server.resource("cortexshift://task", mime_type="application/json")
    def get_task_resource() -> str:
        """Read-only view of the canonical bound task."""
        try:
            return json.dumps(facade.get_current_task(), indent=2)
        except (CortexShiftError, ValueError) as err:
            raise ResourceError(f"Failed to read task resource: {err}") from err

    @server.resource("cortexshift://checkpoint/latest", mime_type="application/json")
    def get_latest_checkpoint_resource() -> str:
        """Read-only view of the latest checkpoint for the bound task."""
        try:
            return facade.get_latest_checkpoint().model_dump_json(indent=2)
        except (CortexShiftError, ValueError) as err:
            raise ResourceError(f"Failed to read checkpoint resource: {err}") from err

    @server.resource("cortexshift://repository", mime_type="application/json")
    def get_repository_resource() -> str:
        """Read-only view of live Git repository status."""
        try:
            return json.dumps(facade.get_repository_status(), indent=2)
        except (CortexShiftError, ValueError) as err:
            raise ResourceError(f"Failed to read repository resource: {err}") from err

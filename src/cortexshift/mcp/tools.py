"""MCP tool definitions and registration for CortexShift."""

from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from cortexshift.domain.errors import CortexShiftError
from cortexshift.mcp.context import McpExecutionContext
from cortexshift.mcp.facade import McpApplicationFacade
from cortexshift.mcp.models import (
    CheckpointResult,
    CreateCheckpointResult,
    DecisionResult,
    ProjectContextResult,
    TaskMutationResult,
)


def register_tools(
    server: MCPServer,
    facade: McpApplicationFacade,
    context: McpExecutionContext,
) -> None:
    """Register read and write MCP tools based on execution context permissions."""

    # -------------------------------------------------------------------------
    # Read Tools (Always available in both managed and unmanaged contexts)
    # -------------------------------------------------------------------------

    @server.tool(
        name="get_project_context",
        description=(
            "Get a structured, bounded overview of the current CortexShift project, "
            "bound task, active session, latest checkpoint, and live repository status."
        ),
    )
    def get_project_context() -> ProjectContextResult:
        try:
            return facade.get_project_context()
        except (CortexShiftError, ValueError) as err:
            raise ToolError(f"Failed to get project context: {err}") from err

    @server.tool(
        name="get_current_task",
        description=(
            "Get the complete canonical task record currently bound to this session, "
            "including objective, requirements, completed work, remaining work, and known issues."
        ),
    )
    def get_current_task() -> dict[str, Any]:
        try:
            return facade.get_current_task()
        except (CortexShiftError, ValueError) as err:
            raise ToolError(f"Failed to get current task: {err}") from err

    @server.tool(
        name="get_latest_checkpoint",
        description=(
            "Get the latest milestone, session-end, or recovery checkpoint for the bound task. "
            "Returns null if no checkpoint exists."
        ),
    )
    def get_latest_checkpoint() -> CheckpointResult:
        try:
            return facade.get_latest_checkpoint()
        except (CortexShiftError, ValueError) as err:
            raise ToolError(f"Failed to get latest checkpoint: {err}") from err

    @server.tool(
        name="get_repository_status",
        description=(
            "Inspect live Git repository status (branch, HEAD commit, dirty state, "
            "staged/unstaged/untracked files, diff shortstat) without modifying state."
        ),
    )
    def get_repository_status() -> dict[str, Any]:
        try:
            return facade.get_repository_status()
        except (CortexShiftError, ValueError) as err:
            raise ToolError(f"Failed to get repository status: {err}") from err

    # -------------------------------------------------------------------------
    # Write Tools (Registered only for managed, non-read-only sessions)
    # -------------------------------------------------------------------------

    if not context.can_mutate():
        return

    @server.tool(
        name="set_current_work",
        description=(
            "Update the description of what the agent is currently working on. "
            "Pass null or an empty string to clear."
        ),
    )
    def set_current_work(current_work: str | None = None) -> TaskMutationResult:
        try:
            return facade.set_current_work(current_work)
        except (CortexShiftError, ValueError) as err:
            raise ToolError(f"Failed to set current work: {err}") from err

    @server.tool(
        name="mark_completed",
        description=(
            "Record completed work items on the canonical task. "
            "Deduplicates items, appends to completed, and removes exact matches "
            "from remaining. Does NOT mark the entire task completed."
        ),
    )
    def mark_completed(items: list[str]) -> TaskMutationResult:
        try:
            return facade.mark_completed(items)
        except (CortexShiftError, ValueError) as err:
            raise ToolError(f"Failed to mark completed: {err}") from err

    @server.tool(
        name="add_remaining",
        description=(
            "Add new remaining work items to the canonical task. "
            "Deduplicates items and preserves ordering."
        ),
    )
    def add_remaining(items: list[str]) -> TaskMutationResult:
        try:
            return facade.add_remaining(items)
        except (CortexShiftError, ValueError) as err:
            raise ToolError(f"Failed to add remaining items: {err}") from err

    @server.tool(
        name="record_issue",
        description=(
            "Record a known technical issue, blocker, or edge case on the canonical task."
        ),
    )
    def record_issue(items: list[str]) -> TaskMutationResult:
        try:
            return facade.record_issue(items)
        except (CortexShiftError, ValueError) as err:
            raise ToolError(f"Failed to record issue: {err}") from err

    @server.tool(
        name="record_decision",
        description=(
            "Record an important architectural or technical decision. "
            "Captures an immutable milestone checkpoint containing the decision "
            "and a live Git working tree snapshot."
        ),
    )
    def record_decision(decision: str) -> DecisionResult:
        try:
            return facade.record_decision(decision)
        except (CortexShiftError, ValueError) as err:
            raise ToolError(f"Failed to record decision: {err}") from err

    @server.tool(
        name="create_checkpoint",
        description=(
            "Create a manual milestone checkpoint for the bound session. "
            "Captures canonical task state, live Git snapshot, decisions, and "
            "reported (unverified) test execution status."
        ),
    )
    def create_checkpoint(
        decisions: list[str] | None = None,
        test_summary: str | None = None,
        note: str | None = None,
    ) -> CreateCheckpointResult:
        try:
            return facade.create_checkpoint(
                decisions=decisions,
                test_summary=test_summary,
                note=note,
            )
        except (CortexShiftError, ValueError) as err:
            raise ToolError(f"Failed to create checkpoint: {err}") from err

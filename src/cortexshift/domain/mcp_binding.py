"""Trusted managed-session binding handed to a provider-spawned MCP server.

A provider CLI, not CortexShift, spawns the CortexShift MCP server. The binding below is
the only channel through which CortexShift's own launch path states which project, task,
CortexShift session, provider, and execution mode that server is bound to. It is minted
exclusively from persisted state at launch time and is never assembled from provider or
model input.
"""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, field_validator

from cortexshift.domain.provider import ProviderId
from cortexshift.domain.session import Session

ENV_PROJECT_ROOT = "CORTEXSHIFT_PROJECT_ROOT"
ENV_TASK_ID = "CORTEXSHIFT_TASK_ID"
ENV_SESSION_ID = "CORTEXSHIFT_SESSION_ID"
ENV_PROVIDER_ID = "CORTEXSHIFT_PROVIDER_ID"
ENV_MCP_READ_ONLY = "CORTEXSHIFT_MCP_READ_ONLY"

#: Every variable the managed binding owns. A launch specification may add environment of
#: its own, but never these: the binding is the single authority over execution context.
BINDING_ENV_VARS = (
    ENV_PROJECT_ROOT,
    ENV_TASK_ID,
    ENV_SESSION_ID,
    ENV_PROVIDER_ID,
    ENV_MCP_READ_ONLY,
)


class McpSessionBinding(BaseModel):
    """Canonical managed execution context for one provider launch.

    The MCP server re-validates every field against SQLite before granting write tools, so
    this object asserts an identity rather than a permission.
    """

    model_config = ConfigDict(frozen=True)

    project_root: Path
    task_id: str
    session_id: str
    provider_id: ProviderId
    read_only: bool = False

    @field_validator("task_id", "session_id")
    @classmethod
    def _reject_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Managed MCP binding identifiers cannot be blank.")
        return cleaned

    @classmethod
    def from_session(
        cls,
        session: Session,
        project_root: Path,
        read_only: bool = False,
    ) -> "McpSessionBinding":
        """Mint a binding from a persisted Session, the only trusted source of identity."""
        return cls(
            project_root=project_root,
            task_id=session.task_id,
            session_id=session.id,
            provider_id=session.provider_id,
            read_only=read_only,
        )

    def to_env(self) -> dict[str, str]:
        """Render the binding as the environment variables the MCP server resolves."""
        return {
            ENV_PROJECT_ROOT: str(self.project_root),
            ENV_TASK_ID: self.task_id,
            ENV_SESSION_ID: self.session_id,
            ENV_PROVIDER_ID: str(self.provider_id),
            ENV_MCP_READ_ONLY: "1" if self.read_only else "0",
        }

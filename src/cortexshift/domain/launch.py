"""Domain model representing the launch specification for a native provider process."""

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from cortexshift.domain.provider import ProviderId


class LaunchSpecification(BaseModel):
    """Immutable specification for launching a native provider process.

    Contains the exact argument list, working directory, and metadata needed
    to spawn an interactive or headless provider session.
    """

    model_config = ConfigDict(frozen=True)

    provider_id: ProviderId
    executable: str
    cwd: Path
    argv: list[str]
    native_session_id: str | None = None
    interactive: bool = True
    initial_prompt_supported: bool = True
    prompt_supplied: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_redacted_argv(self) -> list[str]:
        """Return the argument list with any raw prompt replaced with '<prompt>'.

        Preserves the executable and option flags while redacting prompt text.
        """
        if not self.prompt_supplied:
            return list(self.argv)

        # For providers where prompt is passed as the last positional argument
        redacted = list(self.argv)
        if len(redacted) > 1:
            redacted[-1] = "<prompt>"
        return redacted

    def to_redacted_dict(self) -> dict[str, Any]:
        """Return a dictionary representation suitable for logs and dry-run diagnostics.

        Guarantees that raw user prompt contents are never leaked.
        """
        return {
            "provider_id": str(self.provider_id),
            "executable": self.executable,
            "cwd": str(self.cwd),
            "argv": self.to_redacted_argv(),
            "interactive": self.interactive,
            "initial_prompt_supported": self.initial_prompt_supported,
            "prompt_supplied": self.prompt_supplied,
        }

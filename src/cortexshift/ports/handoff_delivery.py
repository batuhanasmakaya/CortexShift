"""Port defining provider-specific delivery of a canonical handoff context package.

The application core knows only the target provider, the rendered canonical context, and
the project root. How that context physically reaches a given native CLI (a positional
initial prompt, or a read-only headless bootstrap followed by a conversation resume) is
encapsulated entirely behind this port, so orchestration services never branch on
provider identity.
"""

from enum import StrEnum
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from cortexshift.domain.launch import LaunchSpecification
from cortexshift.domain.provider import ProviderId


class HandoffDeliveryStrategy(StrEnum):
    """Documented native transport used to deliver a handoff to a provider.

    - DIRECT_INITIAL_PROMPT: the canonical context is passed to the provider's native
      interactive CLI as a single positional initial-prompt argument.
    - PLAN_BOOTSTRAP_THEN_RESUME: the canonical context is ingested by one read-only
      headless planning turn, and the resulting native conversation is then resumed
      interactively.
    """

    DIRECT_INITIAL_PROMPT = "direct_initial_prompt"
    PLAN_BOOTSTRAP_THEN_RESUME = "plan_bootstrap_then_resume"


class HandoffDeliveryPreparation(BaseModel):
    """Outcome of preparing handoff delivery for a target provider."""

    model_config = ConfigDict(frozen=True)

    launch_spec: LaunchSpecification
    native_session_id: str | None = None
    bootstrap_performed: bool = False


@runtime_checkable
class ProviderHandoffAdapter(Protocol):
    """Abstract port for delivering canonical handoff context through a native CLI."""

    @property
    def provider_id(self) -> ProviderId:
        """Canonical provider identifier."""
        ...

    @property
    def display_name(self) -> str:
        """Human-readable provider name."""
        ...

    @property
    def executable(self) -> str:
        """Base name of the provider CLI executable."""
        ...

    @property
    def delivery_strategy(self) -> HandoffDeliveryStrategy:
        """Documented native transport used by this provider."""
        ...

    @property
    def bootstrap_model_turn_required(self) -> bool:
        """Whether delivery consumes one provider model turn before interactive launch."""
        ...

    def prepare_delivery(
        self,
        executable_path: str,
        project_root: Path,
        rendered_context: str,
    ) -> HandoffDeliveryPreparation:
        """Deliver the canonical context and return the interactive launch specification.

        Implementations performing a headless bootstrap must parse only the minimum
        required machine fields and discard the provider response without persisting
        or logging it.

        Raises:
            HandoffDeliveryError: If delivery could not be completed. The error carries a
                safe machine classification and never embeds raw provider output.
        """
        ...

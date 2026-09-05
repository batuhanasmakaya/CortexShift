"""Provider-native identity capabilities, independent of orchestration history."""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class NativeSessionCapabilities:
    supports_exact_resume: bool = False
    can_allocate_native_id_before_launch: bool = False
    can_capture_native_id_during_bootstrap: bool = False
    can_resume_with_followup_context: bool = False
    requires_model_turn_for_handoff_resume: bool = False
    supports_managed_new_session: bool = False


def valid_native_id(value: str | None) -> bool:
    """Reject empty IDs, option injection and control characters; IDs remain opaque."""
    return bool(value and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,255}", value))

"""Domain models for extensible provider identification and capabilities."""

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ProviderId(str):
    """Extensible provider identifier.

    Unlike a closed enum, ProviderId accepts any valid identifier matching
    `^[a-z0-9][a-z0-9_-]{0,63}$`. This enables future agents (e.g., 'claude',
    'codex', 'antigravity', 'kimi', 'aider') without core domain changes.
    """

    _PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

    def __new__(cls, value: str) -> "ProviderId":
        cleaned = value.strip().lower()
        if not cls._PATTERN.match(cleaned):
            raise ValueError(
                f"Invalid provider ID '{value}'. Must match pattern: ^[a-z0-9][a-z0-9_-]{{0,63}}$"
            )
        return super().__new__(cls, cleaned)

    @classmethod
    def __get_pydantic_core_schema__(cls, source_type: Any, handler: Any) -> Any:
        from pydantic_core import core_schema

        def validate(v: Any) -> "ProviderId":
            if isinstance(v, ProviderId):
                return v
            if isinstance(v, str):
                return ProviderId(v)
            raise ValueError(f"Expected string or ProviderId, got {type(v)}")

        return core_schema.no_info_after_validator_function(
            validate,
            core_schema.str_schema(),
            serialization=core_schema.to_string_ser_schema(),
        )


# Standard canonical provider constants for common reference
PROVIDER_CLAUDE = ProviderId("claude")
PROVIDER_CODEX = ProviderId("codex")
PROVIDER_ANTIGRAVITY = ProviderId("antigravity")


class ProviderCapabilities(BaseModel):
    """Declared capabilities of a coding agent provider.

    Defines what operational modes and features an agent CLI supports.
    """

    model_config = ConfigDict(frozen=True)

    provider_id: ProviderId
    display_name: str
    supports_interactive: bool = True
    supports_headless: bool = False
    supports_native_resume: bool = False
    supports_structured_output: bool = False
    supports_mcp: bool = False
    supports_usage_metrics: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

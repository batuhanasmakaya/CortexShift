"""Stable identifier generation and timezone-aware timestamp utilities."""

import uuid
from datetime import UTC, datetime


def generate_id(prefix: str) -> str:
    """Generate a collision-resistant, human-readable prefixed identifier.

    Format: `<prefix>_<uuid4_hex>` (e.g., `proj_1234567890abcdef...`).

    Args:
        prefix: Short entity prefix (e.g., 'proj', 'task', 'sess', 'cp', 'handoff').

    Returns:
        A unique, stable identifier string.
    """
    clean_prefix = prefix.strip().lower()
    return f"{clean_prefix}_{uuid.uuid4().hex}"


def utc_now() -> datetime:
    """Return the current timezone-aware UTC datetime.

    Avoids naive datetimes across all domain and persistence boundaries.
    """
    return datetime.now(UTC)

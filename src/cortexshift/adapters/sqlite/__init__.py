"""SQLite persistence adapter package."""

from cortexshift.adapters.sqlite.migrations import CURRENT_SCHEMA_VERSION, run_migrations
from cortexshift.adapters.sqlite.store import SQLiteStateStore

__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "SQLiteStateStore",
    "run_migrations",
]

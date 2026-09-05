# ADR-0003: Project-Local SQLite Persistence and Task State Architecture

- **Status**: Accepted
- **Date**: 2026-09-05
- **Deciders**: CortexShift Core Architecture Team

---

## Context

Phase 0 established canonical domain models (`Project`, `Task`, `Session`, `Checkpoint`, `Handoff`) and abstract persistence boundaries (`StateStore`). Phase 1 introduced passive provider discovery. However, until now CortexShift possessed no durable memory across process terminations or shell restarts.

When coordinating multiple coding agents (Claude Code, OpenAI Codex, Google Antigravity), tasks outlive any individual session. The orchestrator must persist project identity, record tasks and their requirements/constraints/progress, track the currently active task, and resolve project state when commands are executed from subdirectories.

We need a persistent storage architecture that preserves CortexShift's foundational invariants: local-first, zero cloud dependencies, zero credential storage, and no transcript bloat.

---

## Decisions

We have made the following foundational decisions for project and task persistence in Phase 2:

### 1. Project-Local Runtime Directory (`.cortexshift/`)
State is strictly project-local. Every initialized software repository owns its dedicated runtime directory:
```text
<repository_root>/.cortexshift/
```
No global configuration or central database is created (`~/.cortexshift/` is explicitly rejected in Phase 2). The directory is gitignored to avoid checking machine-local runtime files into repository history.

### 2. Standard-Library SQLite Database (`state.sqlite3`)
The persistent store is a SQLite database located at `.cortexshift/state.sqlite3`.
- Persistence uses Python's standard-library `sqlite3` module exclusively.
- Third-party ORMs (SQLAlchemy, SQLModel, Peewee) are prohibited to eliminate heavy framework overhead and ensure explicit schema control.
- Connection pragmas enforce `PRAGMA foreign_keys = ON;`, `PRAGMA busy_timeout = 5000;`, and `PRAGMA journal_mode = WAL;`.
- Temporary WAL auxiliary files (`state.sqlite3-wal`, `state.sqlite3-shm`) inside `.cortexshift/` are accepted for transaction performance and reader concurrency.

### 3. Explicit Schema Versioning and Forward-Safety
All databases are managed by an ordered, deterministic migration runner:
- Applied schema versions are tracked in the `schema_metadata` table.
- Initial schema version is `1`.
- If CortexShift encounters a database with `schema_version > MAX_SUPPORTED_VERSION`, it refuses to operate and fails safely (`UnsupportedSchemaVersionError`). It never silently downgrades or alters incompatible databases.
- Migrations execute within strict transaction boundaries and roll back on failure.

### 4. Normalized Ancestor Discovery (`ProjectLocator`)
Commands may be invoked from the repository root or any arbitrary child directory. CortexShift traverses the current directory and its parent ancestors looking for `.cortexshift/state.sqlite3`. The nearest initialized ancestor directory wins. Discovery is purely filesystem-based and does NOT invoke or depend on Git.

### 5. Separation of Project Identity and Mutable Runtime State
Project identity (`Project`) is separated from mutable runtime pointers (`ProjectRuntime`):
- Exactly one `Project` row exists per project database.
- Repository paths are resolved and normalized as absolute paths.
- `project_runtime` maintains an `active_task_id` foreign key pointer referencing `tasks(id) ON DELETE SET NULL`.
- This enables exactly one active task at a time while allowing multiple historical tasks.

### 6. Atomic Task Mutations and Transactional Boundaries
All state modifications (task creation, updates, progress additions, activation, completion) execute within transactional boundaries:
- `task start` transactionally inserts the canonical task (`title`, `objective`, `requirements`, `constraints`, `status`, timestamps) and updates `active_task_id`.
- `task complete` transactionally marks the task completed and clears `active_task_id` if the completed task was active.
- Context collections (`requirements`, `constraints`, `completed_items`, `remaining_items`, `known_issues`) are serialized as validated JSON text, preserving list ordering, with progress collections deduplicated on append.
- These persisted task context fields (`objective`, `requirements`, `constraints`, `completed`, `remaining`, `known_issues`, `current_work`) serve as essential structured inputs for future agent handoffs.
- Terminal-state tasks (`completed`, `cancelled`) cannot be reactivated.

### 7. Zero Credential & Zero Transcript Storage
The Phase 2 database schema contains no columns or tables for credentials, API tokens, passwords, or conversational transcripts. The database stores only CortexShift canonical project and task records.

---

## Consequences

### Positive
- **Process resilience**: Tasks and progress persist across process exits, terminal closures, and agent re-runs.
- **Portability**: Standard SQLite file can be inspected, backed up, or copied without server dependencies.
- **Subdirectory ergonomics**: Developers and coding agents can invoke `cortexshift` from nested subdirectories without error.
- **Safety**: Safe failure on incompatible versions prevents database corruption across tool upgrades.

### Negative / Trade-offs
- Ephemeral WAL auxiliary files (`state.sqlite3-wal`, `state.sqlite3-shm`) may be present in `.cortexshift/` while connections are open. (Mitigated by `.gitignore`).
- JSON-encoded collection columns require Pydantic reconstruction and are not individually queryable via relational joins in v1. (Accepted: Phase 2 requires minimal complexity; child tables can be introduced in future migrations if needed).

---

## Alternatives Considered

1. **Flat JSON Files (`.cortexshift/state.json`)**:
   *Rejected*. Prone to race conditions, partial write corruption, lack of transactional guarantees, and difficult migration paths compared to SQLite transactions and PRAGMAs.
2. **Global Database (`~/.cortexshift/global.db`)**:
   *Rejected*. Leaks workspace state across unrelated repositories, creates single-point-of-failure contention, and complicates multi-repo workflows.
3. **Full Relational Normalization (Child tables for requirements, constraints, items)**:
   *Deferred*. Premature complexity for Phase 2. JSON-encoded TEXT columns validated by Pydantic domain models provide clean roundtripping and deterministic serialization without bloated schemas.

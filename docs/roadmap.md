# CortexShift Product Roadmap

This document outlines the phased development roadmap for **CortexShift**.

Each phase builds systematically upon the previous phase without premature complexity. Architectural boundaries established in Phase 0 ensure that later phases can be added without rewriting the core domain.

---

## Phases Overview

```text
Phase 0  ──▶  Phase 1  ──▶  Phase 2  ──▶  Phase 3  ──▶  Phase 4  ──▶  Phase 5
Foundation    Provider      Task State    Git Context   Native        Manual
& Arch        Discovery     & SQLite                    Launch        Handoff
(Complete)    (Complete)    (Complete)    (Complete)    (Complete)    (Complete)

Phase 6  ──▶  Phase 7  ──▶  Phase 8  ──▶  Phase 9  ──▶  Phase 10 ──▶  Future
Native        Checkpoints   MCP Shared    TUI           Public        Experimental
Resume        & Recovery    State                       Release
(Complete)    (Complete)    (Complete)    (Planned)
```

---

## Phase 0 — Foundation & Architecture *(Completed)*

Establish repository foundation, domain models, abstract ports, CLI skeleton, test suite, and architectural invariants.

- **Deliverables**:
  - Valid Python package layout (`cortexshift`).
  - Strict Pydantic domain models (`Project`, `Task`, `Session`, `Checkpoint`, `Handoff`, `GitSnapshot`, `ProviderCapabilities`).
  - Abstract Ports (`ProviderAdapter`, `RepositoryInspector`, `StateStore`).
  - Typer CLI with `version` and `--help`.
  - Comprehensive documentation (`AGENTS.md`, `architecture.md`, `handoff-protocol.md`, `ADR-0001`).
  - Complete test suite, strict mypy typing, Ruff linting, and GitHub Actions CI.

---

## Phase 1 — Provider Discovery *(Completed)*

Implement non-invasive local detection of installed AI coding agent CLIs.

- **Deliverables**:
  - Safe subprocess abstraction (`CommandRunner` port, `SubprocessCommandRunner` adapter).
  - Passive native provider probes for Claude Code (`claude`), OpenAI Codex (`codex`), and Google Antigravity (`agy`).
  - Best-effort version extraction and conservative, passive authentication classification.
  - Model prompt and credential file inspection prevention invariants enforced.
  - `cortexshift doctor` command with Rich human-readable table and machine-readable `--json` output.
  - Provider filtering (`--provider` / `-p`).
  - Ephemeral diagnostic architecture (`DoctorService`, `DoctorReport`).
  - ADR-0002 documentation and automated test suite.

---

## Phase 2 — Project & Task State *(Completed)*

Introduce durable local persistence, schema migrations, and complete task lifecycle management.

- **Deliverables**:
  - Project-local `.cortexshift/state.sqlite3` with standard-library SQLite and WAL mode.
  - Explicit deterministic schema migrations with forward-safety (`schema_metadata`).
  - Nearest ancestor project locator (`ProjectLocator`) without Git dependencies.
  - Idempotent project initialization (`cortexshift init`).
  - Active task pointer mechanics in `project_runtime`.
  - Task lifecycle operations: `cortexshift task start`, `list`, `show`, `update`, `activate`, `complete`.
  - Project and task status overview: `cortexshift status` and `cortexshift status --json`.
  - Process restart durability and workspace isolation verification.
  - ADR-0003 documentation and comprehensive automated test suite.

---

## Phase 3 — Git Context & Repository Awareness *(Completed)*

Connect live Git repositories to CortexShift's context engine with read-only inspection and persistent snapshot history.

- **Deliverables**:
  - Safe, strictly read-only repository inspection using native `git` CLI subprocesses with bounded timeouts and sanitized execution environments (`GIT_TERMINAL_PROMPT=0`, `GIT_PAGER=cat`, `GIT_OPTIONAL_LOCKS=0`).
  - NUL-safe (`-z`) porcelain v1 parser handling spaces, unicode, renames, conflicts, and untracked files.
  - Automatic path scoping for monorepo setups (resolving paths relative to project root) and exclusion of `.cortexshift/` internal state.
  - Branch, commit SHA, dirty state, detached HEAD, and unborn (0 commits) repository detection.
  - Persistent repository snapshots stored in SQLite schema v2 (`git_snapshots` table) across process restarts.
  - CLI command group `cortexshift repo` (`status`, `snapshot`, `snapshots`, `show`) with human formatting and machine-readable `--json` output.
  - Graceful non-blocking fallback (exit code 0) when Git is not installed or when executed in non-Git directories.
  - ADR-0004 documentation and comprehensive unit/integration test suite.

---

## Phase 4 — Native Provider Launch & Session Lifecycle *(Completed)*

Launch external coding agents through non-invasive adapters with raw terminal passthrough, workspace concurrency leasing, and session lifecycle tracking.

- **Deliverables**:
  - Segregated runtime ports (`ProviderRuntimeAdapter`, `InteractiveProcessRunner`, `WorkspaceLeaseManager`, `SessionStore`).
  - Native interactive execution with raw TTY passthrough (`SubprocessInteractiveProcessRunner`), no buffering or scraping.
  - Prohibition of shell execution (`shell=False`) across all provider invocations.
  - Same-working-tree model: provider processes launch with `cwd = project_root` regardless of invocation subdirectory.
  - Project-local exclusive workspace leasing (`FileWorkspaceLease` via `fcntl.flock` on `.cortexshift/agent.lock`) enforcing the single-mutating-agent invariant.
  - Task-centric prerequisite: launch strictly requires an active task (`NoActiveTaskError`).
  - Dry-run preview mode (`cortexshift run <provider> --dry-run` and `--dry-run --json`) with prompt redaction.
  - SQLite schema v3 migration adding `sessions` table and indexes.
  - Session lifecycle tracking (`running`, `completed`, `failed`, `interrupted`), exit code capture, and zero prompt/transcript storage.
  - CLI commands: `cortexshift run <provider>`, `cortexshift session list`, `cortexshift session show <id>`.
  - ADR-0005 documentation and comprehensive unit/integration test suite.

---

## Phase 5 — Canonical Manual Handoff & Agent Switching *(Completed)*

The first end-to-end multi-agent workflow: one Task, multiple coding agents, no manual re-explanation.

- **Deliverables**:
  - `cortexshift switch claude|codex|antigravity` moving the active Task to another agent with full canonical context, plus `--from-session`, `--note`, `--dry-run`, and `--json`.
  - **Outgoing-agent independence**: handoffs derive deterministically from the canonical Project, canonical Task, previous Session metadata, live Git inspection, and a Git snapshot captured at switch time. No outgoing model call is ever made, and the outgoing provider's executable is never resolved — covered by dedicated regression tests.
  - CortexShift Handoff Protocol v1 (`HANDOFF_PROTOCOL_VERSION`), versioned independently of the SQLite schema.
  - Domain refactor into `HandoffPayload` (canonical engineering context) and `HandoffRecord` (orchestration and delivery metadata), with `HandoffStatus` and safe `HandoffFailureCode` classifications.
  - Deterministic `HandoffBuilder` (pure, no I/O, no model) and provider-neutral `HandoffRenderer` with a bounded 48,000-character transport budget, reported truncation counts, and an untruncated persisted payload.
  - Honest unknown state: absent decisions and unverified test status are stated, never invented; a provider exiting 0 is never read as "tests pass".
  - Receiving-agent contract: CortexShift authority order plus explicit startup instructions (read `AGENTS.md`, inspect `git status`/`git diff`, verify recorded completed work, run relevant tests, continue rather than restart).
  - `HandoffStore` persistence port and SQLite schema migration v4 adding the `handoffs` table, with all Phase 1–4 state preserved.
  - `ProviderHandoffAdapter` port isolating delivery variance: direct interactive initial prompt for Claude Code and Codex; read-only plan-mode bootstrap plus native conversation resume for Antigravity.
  - `HeadlessProviderRunner` port and `SubprocessHeadlessProviderRunner` adapter for one bounded, shell-free, TTY-free provider turn.
  - `ProviderSessionLauncher` extracted from `RunService` so `run` and `switch` share one Session lifecycle without nesting advisory locks.
  - Zero-quota inspection: `cortexshift handoff preview <target>` and `cortexshift switch <target> --dry-run` persist nothing and launch nothing.
  - Durable handoff history: `cortexshift handoff list` and `cortexshift handoff show <id>` with `--json`.
  - ADR-0006, an implemented [Canonical Handoff Protocol](handoff-protocol.md), and a flagship Claude → Codex → Antigravity integration test using fake providers with no network and no real model call.

---

## Phase 6 — Native Session Identity, Resume & Return-to-Provider Continuity ✅ COMPLETE

Add exact provider-native history continuity while preserving canonical Task and Handoff authority.

- Claude managed UUID4 allocation and exact resume.
- Codex managed handoff JSONL ID capture, read-only same-thread handoff continuation, then native TUI resume.
- Antigravity known conversation resume and same-conversation plan handoff continuation.
- `cortexshift resume PROVIDER`, `--session`, `--dry-run`, and dry-run-only `--json`.
- Switch auto-reuses eligible target native history; `--new-session` and `--resume-session` override selection.
- New CortexShift invocation on every resume with persisted `resumed_from_session_id`; transactional schema v4 → v5 migration.
- No private provider-store discovery, guessed provider-last behavior, prompt/response persistence, or hidden model turns for plain runs.
- Plain Codex and Antigravity run paths can retain null native IDs; historical untracked or unfinished invocations cannot be exact-resumed.
- Verified: **486 passing tests**, **90% coverage**, Ruff, Ruff format, mypy and all requested CLI help/version/doctor checks. The flagship A → B → C → A → B → C test proves one Task, six invocations, three native identities, five fresh handoffs and five fresh Git snapshots; persistence survives fresh store recreation.
- Real provider E2E is deferred: Claude was installed but unauthenticated, Codex installed/authenticated, and Antigravity missing. Automated verification uses deterministic fake providers.
- [ADR-0007](decisions/ADR-0007-native-session-continuity.md) documents capabilities, transport costs and trade-offs.

---

## Phase 7 — Checkpoints, Crash Recovery & Handoff Enrichment *(Completed)*

Make development state resilient to unexpected terminations (rate limit exhaustion, process crashes, terminal drops).

- **Deliverables**:
  - **Checkpoint Protocol v1**: Immutable `CheckpointRecord` entities with structured `CheckpointPayload` (task snapshot, Git state, source session, decisions, test status with provenance, operator note). Strict character limits enforced at domain boundaries.
  - **Cooperative Milestones**: `cortexshift checkpoint create` captures progress milestones without acquiring the workspace lease, ensuring zero lock contention during productive sessions.
  - **Crash Recovery & Reconciliation**: `cortexshift recover` acquires the workspace lease and reconciles unfinalized sessions (`INITIALIZING`/`RUNNING`), honestly setting `status=interrupted` with `exit_reason=unexpected_termination` and recording `reconciled_at` without fabricating unobserved process end times (`ended_at=None`).
  - **Recovery Checkpoints**: Captures an immutable `RECOVERY` checkpoint observing live Git state to bridge crash recovery into the next provider invocation.
  - **Automatic Session-End Checkpoints**: Deterministically captured on provider exit under the active workspace lease before lease release, with safe error handling to protect session outcomes.
  - **Checkpoint-Enriched Handoffs**: `HandoffBuilder` enriches decisions and test execution status from the newest checkpoint. Unverified tests are explicitly flagged with `reported_unverified` provenance disclaimers.
  - **Persistence & Schema v6**: Forward-safe transactional migration adds `checkpoints` table with 4 indexes, alters `sessions` with `reconciled_at`, and alters `handoffs` with `source_checkpoint_id REFERENCES checkpoints(id) ON DELETE SET NULL`.
  - **CLI Inspection**: `cortexshift checkpoint create`, `list`, `show`, `latest`, and `cortexshift recover` (`--dry-run`, `--json`).
  - **Comprehensive Verification**: 524 passing unit and integration tests with 89% branch/statement coverage. Full crash-recovery and restart simulations verify that state survives across SQLite store recreation.
  - [ADR-0008](decisions/ADR-0008-checkpoint-and-recovery.md) documents architectural decisions and invariants.

---

## Phase 8 — MCP Shared State & Agent Self-Reporting *(Completed)*

Give active coding agents a first-class structured channel to read canonical project/task state and self-report progress mid-flight using the Model Context Protocol (MCP).

- **Deliverables**:
  - **Local Stdio Transport Exclusively**: MCP server (`cortexshift mcp serve`) running over local stdio only. Pure stdout wire protocol with all diagnostics and logs routed strictly to stderr.
  - **Context Binding & Safety**: `McpExecutionContext` resolving project, task, session, provider, and read-only flags from environment variables. Strict binding prevents cross-task state pollution.
  - **Dual Capability Mode**: Managed active sessions receive 10 tools (4 read + 6 write); unmanaged and read-only sessions receive 4 read tools only.
  - **Exposed Tools**:
    - Read: `get_project_context`, `get_current_task`, `get_latest_checkpoint`, `get_repository_status`.
    - Write: `set_current_work`, `mark_completed`, `add_remaining`, `record_issue`, `record_decision`, `create_checkpoint`.
  - **Exposed Resources**: `cortexshift://project`, `cortexshift://task`, `cortexshift://checkpoint/latest`, `cortexshift://repository` (all `application/json`).
  - **Workspace Lease Bypass & WAL Concurrency**: MCP tool executions deliberately bypass the OS advisory lock (`agent.lock`) to avoid self-deadlocks with parent agent processes, relying on SQLite WAL concurrency mode and `check_same_thread=False`.
  - **Provider Integration Transports**: Automatic launch configuration for Claude Code (`--mcp-config <inline JSON>`), OpenAI Codex (`-c` flags), and Google Antigravity (`.agents/mcp_config.json` via `cortexshift mcp setup antigravity`). Headless bootstrap turns run read-only (`CORTEXSHIFT_MCP_READ_ONLY=1`).
  - **Schema Stability**: Retains SQLite schema version strictly at **v6** without migrations.
  - **CLI Commands**: `cortexshift mcp serve`, `cortexshift mcp status` (`--json`), `cortexshift mcp setup antigravity` (`--dry-run`, `--force`).
  - **Quality Gates & Verification**: 572 passing unit and integration tests, strict mypy typing across source and tests, 0 Ruff lint warnings. Flagship workflow verifies multi-agent self-reporting and checkpoint-enriched continuity across Claude and Codex.
  - [ADR-0009](decisions/ADR-0009-mcp-shared-state.md) documents architectural decisions, wire protocol guarantees, and provider transports.

---

## Phase 9 — Terminal User Interface (TUI)

Build an interactive terminal experience using Textual or Rich.

- **Goals**:
  - Visual task dashboard.
  - Real-time session monitoring and interactive agent switcher.

---

## Phase 10 — Public Release Hardening

Prepare CortexShift for public open-source release.

- **Goals**:
  - Cross-platform verification (macOS, Linux, Windows).
  - Standalone binary distributions via PyPI and Homebrew.
  - Public documentation site and contributor onboarding.

---

## Later & Experimental Ideas

The following concepts remain experimental and will be evaluated based on user demand:
- Automatic quota/rate-limit detection and automatic fallback switching.
- Visual desktop application (Electron / Tauri).
- VS Code / JetBrains IDE extensions.
- Adaptive context budgeting and smart semantic compression.
- Optional explicit-opt-in full transcript capture (`capture_transcripts = true`).
- Controlled multi-agent parallel branches and merge workflows.

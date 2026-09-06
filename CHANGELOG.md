# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Phase 9: Interactive Terminal Control Center** (unreleased):
  - Textual TUI (`textual>=8,<9`) exposed through an explicit `cortexshift tui` entrypoint. Plain `cortexshift` still prints help; no existing command semantics change.
  - Overview dashboard: project identity, active Task with structured progress, live repository state, latest Session/Checkpoint/Handoff activity, compact provider availability, and an honest notice when unfinalized sessions exist.
  - Task controls: complete canonical Task rendering plus confirmed modals for set current work, mark completed, add remaining, and record issue, along with task activation from a navigable table.
  - Repository dashboard: live, strictly read-only Git inspection showing branch, HEAD, detached and dirty state, staged/modified/untracked/conflicted files, and diff shortstats. No full diffs, and no Git mutation of any kind.
  - Session history: CortexShift orchestration metadata only, including native session IDs, derived exact resumability, invocation lineage, exit reason/code, and reconciliation timestamps. No provider transcripts are read or displayed.
  - Checkpoint history and MANUAL checkpoint creation from the dashboard, without contending for the workspace lease. Historical snapshots are labelled as historical observations and reported test summaries as *Reported / unverified*.
  - Handoff history with full canonical payload detail, plus zero-quota handoff preview that persists nothing and starts no bootstrap turn.
  - Provider and MCP status: passive discovery results, native resume capability, MCP integration mode per provider, and a guarded action to configure workspace MCP for Antigravity that preserves unrelated servers and refuses to force a conflicting entry.
  - Crash recovery control gated behind a non-mutating `RecoveryService` dry-run preview; reconciliation itself remains the service's responsibility and is refused safely when another agent owns the workspace.
  - TUI provider action handoff: run, resume, and switch exit the Textual application with a structured `TuiExitRequest`; `TuiCoordinator` invokes the run/resume/switch service only after `App.run()` has returned and the terminal has been restored. **No provider TUI is embedded, scraped, multiplexed, or relayed through a pseudo-terminal**, and no PTY or terminal-emulator dependency is introduced.
  - The dashboard holds no workspace lease for its lifetime, so leaving it open never blocks a coding agent. Workspace activity is probed through the OS advisory lock and never inferred from the lock file's existence.
  - Refresh model: a lightweight ~2 second SQLite-only timer surfaces MCP-driven Task updates in an already-open dashboard; live Git runs on startup, on Repository screen entry, and on explicit refresh only. Slow work runs in Textual thread Workers, with exclusive worker groups plus generation tokens so a stale result can never overwrite newer data.
  - Keyboard-first throughout: `1`–`7` navigation, `r` refresh, `c` checkpoint, `w`/`m`/`n`/`i` task progress, `a` activate, `x` provider actions, `p` handoff preview, `g` Antigravity MCP setup, `shift+R` recovery, `?` help, `q` quit, plus Textual's built-in command palette. Status meaning always pairs a glyph with its styling, so nothing depends on colour alone. Usable at 80×24 through 120×40, with the sidebar folding away below 90 columns and a clear message rather than a broken render below 60×12.
  - Architectural Decision Record `ADR-0010-terminal-control-center.md` documenting the framework choice, the alternatives rejected (curses, prompt_toolkit, a custom Rich `Live` UI, PTY embedding, a browser UI), and the trade-offs.
  - 684 passing unit and integration tests at 88% coverage, including headless Textual `run_test()`/`Pilot` suites for navigation, screens, modals, refresh, worker race handling and exit requests; an 80×24 / 100×30 / 120×40 size matrix; and flagship coverage of the full control-center workflow, MCP-driven live state, and dashboard-before-provider launch ordering.

### Changed

- `Task.complete_items()` now owns the canonical "mark completed" rule — append to completed (deduplicated), drop any exactly matching remaining entry, and never complete the Task itself. `McpApplicationFacade.mark_completed` was refactored onto it so MCP agents and the dashboard cannot drift apart. Behaviour is unchanged.
- Task input bounds (`MAX_CURRENT_WORK_CHARS`, `MAX_ITEM_CHARS`, `MAX_ITEMS_PER_CALL`) moved to `cortexshift.domain.task` and are re-exported from `cortexshift.mcp.models`.
- Added `TaskWorkspaceService` to the application layer so path-addressed callers get canonical Task operations without opening a persistence store themselves.

### Fixed

- `RepositoryService` now closes the SQLite connections it opens (injected stores are still left to their owner). The leak predated Phase 9 and was harmless at CLI cadence, but a dashboard inspecting the repository on every refresh made it consequential.

- **Phase 8: MCP Shared State & Agent Self-Reporting** (unreleased):
  - Model Context Protocol (MCP) server integration (`cortexshift mcp serve`) using official Python MCP SDK (`mcp>=2,<3`) over local stdio only. No network listeners, HTTP/SSE endpoints, or sockets.
  - Pure stdout wire protocol: stdout is strictly reserved for valid MCP JSON-RPC protocol frames; all diagnostics, informational logs, and errors are routed to stderr or silenced.
  - Context binding and safety via `McpExecutionContext` resolving project, task, session, provider, and read-only flags from environment variables. Strict binding isolates tool execution to the bound task, preventing cross-task state pollution.
  - Dual-mode capability exposure: managed active sessions receive all 10 tools (4 read + 6 write); unmanaged and read-only sessions receive 4 read tools only. Write tools are not registered on read-only servers.
  - Real-time read tools: `get_project_context`, `get_current_task`, `get_latest_checkpoint`, `get_repository_status`.
  - Real-time write tools: `set_current_work`, `mark_completed` (moves matching items from remaining to completed without marking whole task complete), `add_remaining`, `record_issue`, `record_decision` (persists an automatic decision checkpoint), `create_checkpoint` (milestone checkpoint with explicit reported test provenance).
  - JSON resources: `cortexshift://project`, `cortexshift://task`, `cortexshift://checkpoint/latest`, `cortexshift://repository` (`application/json`).
  - Workspace lease bypass: MCP server operations deliberately bypass the OS advisory lock (`.cortexshift/agent.lock`) to avoid self-deadlocks with the parent agent process, relying on SQLite WAL concurrency mode and `check_same_thread=False`.
  - Automated provider launch integration: Claude Code (`--mcp-config <inline JSON>`), OpenAI Codex (`-c` flags), and Google Antigravity (`.agents/mcp_config.json` via `cortexshift mcp setup antigravity`). Headless bootstrap turns run read-only (`CORTEXSHIFT_MCP_READ_ONLY=1`).
  - Retains SQLite schema version strictly at **v6** without migrations.
  - CLI commands: `cortexshift mcp serve`, `cortexshift mcp status` (`--json`), and `cortexshift mcp setup antigravity` (`--dry-run`, `--force`).
  - Architectural Decision Record `ADR-0009-mcp-shared-state.md` documenting MCP architecture, pure stdout wire protocol, context binding, and provider transports.
  - 572 passing unit and integration tests with strict mypy typing across source and tests, zero Ruff lint errors, and verified flagship cross-agent workflow.

- **Phase 7: Checkpoints, Crash Recovery & Handoff Enrichment** (unreleased):
  - Checkpoint Protocol v1 domain entities: immutable `CheckpointRecord` with structured `CheckpointPayload` (task snapshot, Git state, source session, operator note, engineering decisions, test status with provenance).
  - Strict input boundary limits: `MAX_OPERATOR_NOTE_CHARS = 2000`, `MAX_DECISION_CHARS = 1000`, `MAX_TEST_SUMMARY_CHARS = 1000`. No conversational transcripts, provider reasoning, credentials, or full file diffs stored.
  - Cooperative milestone checkpoints: `cortexshift checkpoint create` captures progress without acquiring the workspace lease, preventing lock contention during active provider sessions.
  - Crash recovery and honest session reconciliation: `cortexshift recover` acquires the exclusive workspace lease, reconciles unfinalized sessions (`INITIALIZING`/`RUNNING`) by setting `status=interrupted`, `exit_reason=unexpected_termination`, and `reconciled_at=<UTC>`, leaving `ended_at=None` to avoid fabricating unobserved process termination times.
  - Recovery checkpoints: Captures an immutable `RECOVERY` checkpoint observing live Git state to bridge crash recovery into the next provider invocation.
  - Automatic session-end checkpoints: Captured deterministically on provider process termination (exit 0, non-zero, or interrupt) under the active workspace lease before lease release. Safe execution ensures repository inspection/persistence warnings do not fail completed sessions.
  - Checkpoint-enriched handoffs: `HandoffBuilder` injects decisions and reported test execution from the newest checkpoint. Unverified tests are explicitly qualified with `reported_unverified` provenance disclaimers.
  - SQLite schema migration **v5 → v6**: Adds `checkpoints` table with 4 indexes (`idx_checkpoints_task_created`, `idx_checkpoints_session`, `idx_checkpoints_snapshot`, `idx_checkpoints_kind`), alters `sessions` with `reconciled_at TEXT`, and alters `handoffs` with `source_checkpoint_id TEXT REFERENCES checkpoints(id) ON DELETE SET NULL`. Fully forward-safe and transactional with rollback.
  - Dedicated CLI commands: `cortexshift checkpoint create`, `list`, `show`, `latest`, and `cortexshift recover` (`--dry-run`, `--json`).
  - Architectural Decision Record `ADR-0008-checkpoint-and-recovery.md` documenting Checkpoint Protocol v1, lease rules, honest reconciliation, and test provenance disclaimers.
  - 524 passing unit and integration tests with 89% coverage, including crash recovery flagship and restart persistence across SQLite store recreation.

- **Phase 6: Native Session Identity, Resume & Return-to-Provider Continuity** (unreleased):
  - `cortexshift resume PROVIDER` with exact CortexShift source selection, dry-run and JSON preview.
  - New invocation lineage via `Session.resumed_from_session_id`; transactional SQLite schema v4 → v5 migration preserves all older records.
  - Claude UUID4 allocation on new sessions and exact resume with fresh handoff context on return.
  - Codex managed native ID capture from a read-only JSONL handoff turn, plus same-thread `exec resume` injection before interactive resume.
  - Antigravity known conversation resume and same-conversation plan handoff on return.
  - Automatic safe target reuse on explicit switch; `--new-session` and `--resume-session` overrides.
  - Session list/show include native ID, lineage and derived exact resumability. Unknown and spawn-failed invocations are never guessed.
  - Six-invocation native-continuity integration coverage, restart persistence, v5 migration/rollback tests and privacy boundaries.
  - ADR-0007 records partial plain-run tracking support, no App Server allocation, model-turn costs and exact-ID-only policy.

- **Phase 5: Canonical Manual Handoff & Agent Switching**:
  - CLI command `cortexshift switch <provider>` moving the active Task to another coding agent with full canonical context, supporting `--from-session`, `--note`, `--dry-run`, and `--json`.
  - CLI command group `cortexshift handoff` with `preview <target>`, `list`, and `show <id>`, each supporting `--json`.
  - **CortexShift Handoff Protocol v1** (`HANDOFF_PROTOCOL_VERSION = 1`), versioned independently of the SQLite schema so canonical fields and prompt formatting can evolve without a database migration.
  - **Outgoing-provider independence**: handoffs are built deterministically from the canonical Project, canonical Task, previous CortexShift Session metadata, live Git inspection, and a Git snapshot captured at switch time. CortexShift makes no outgoing model call and never resolves the outgoing provider's executable, so switching works after the previous agent's quota is exhausted, its process is gone, or its CLI is uninstalled. Covered by dedicated service-level and end-to-end regression tests.
  - Domain refactor of the speculative Phase 0 `Handoff` model into `HandoffPayload` (canonical point-in-time engineering context) and `HandoffRecord` (orchestration and delivery metadata), plus `HandoffStatus` (`prepared`/`delivered`/`failed`), `HandoffFailureCode`, `HandoffGitState`, `HandoffTestStatus`, and `HandoffSourceSession`.
  - Deterministic `HandoffBuilder` (pure; no I/O, no subprocess, no model) deriving files touched from the deduplicated union of staged, modified, untracked, and conflicted Git paths, and deriving the recommended next action from current work → first remaining item → repository inspection.
  - Honest unknown state: `IMPORTANT DECISIONS` and `TEST STATUS` are explicitly reported as unrecorded rather than fabricated. A provider process exiting 0 is never interpreted as "tests pass".
  - Provider-neutral `HandoffRenderer` producing the receiving-agent package with the CortexShift authority order, an explicit startup contract (read `AGENTS.md`, inspect `git status`/`git diff`, verify recorded completed work, run relevant tests, continue rather than restart), and conservative completed / do-not-redo phrasing.
  - **Context budgeting**: deterministic 48,000-character transport bound using simple character and list budgets (no tokenizer, no summarizer, no model, no vector store). Mandatory context is never dropped, omissions are reported with exact counts, and the persisted canonical payload is never truncated — remaining retrievable via `cortexshift handoff show ID --json`.
  - Control-character and path safety: untrusted task text and Git filenames are escaped while preserving valid Unicode, and file-path entries are explicitly framed to the receiving agent as data rather than instructions.
  - **Git snapshot at switch time**, captured under the exclusive workspace lease, which is held continuously from repository observation through receiving-provider runtime. Missing Git and non-Git projects continue with an explicit canonical marker and no snapshot row; an unexpected Git probe error fails the switch safely rather than shipping an unreliable observation.
  - New persistence port `HandoffStore` and SQLite schema migration **v3 → v4** adding the `handoffs` table (canonical validated JSON payload, foreign keys, indexes), preserving all Phase 1–4 project, task, active-task, Git snapshot, and session state.
  - New delivery port `ProviderHandoffAdapter` isolating all provider transport variance from orchestration:
    - **Claude Code** — direct interactive initial prompt as a single argument.
    - **Codex** — direct interactive initial prompt as a single argument (never `codex exec`).
    - **Antigravity** — read-only `--mode=plan` headless bootstrap that ingests the canonical context, then interactive resume of that same conversation via `--conversation <id>`.
  - New execution port `HeadlessProviderRunner` with `SubprocessHeadlessProviderRunner`: one bounded, shell-free, TTY-free provider turn with a model-turn-appropriate timeout and no output logging.
  - Antigravity bootstrap parses only `conversation_id` and `status`, binds the conversation to the receiving Session's `native_session_id`, and discards the provider response, reasoning, and usage without persisting, logging, or printing them. Permission-bypass and accept-edits flags are never used, and no TUI keystrokes are automated.
  - `ProviderSessionLauncher` extracted from `RunService` so `run` and `switch` share one Session lifecycle implementation without nesting advisory workspace locks.
  - Handoff delivery and target Session outcome are modelled as distinct concerns: context can be `delivered` while the receiving session later ends `failed`. Delivery failures record safe machine classifications (`target_provider_missing`, `bootstrap_failed`, `bootstrap_timeout`, `bootstrap_invalid_output`, `spawn_failed`, `workspace_locked`) and never store raw provider stderr.
  - Source Session selection from durable CortexShift records only, preferring the most recent meaningful session on the active task, with explicit `--from-session` override and validation (`NoSourceSessionError`, `SessionTaskMismatchError`, `SameProviderSwitchError`).
  - `switch` never mutates task progress: it does not mark work completed, alter the remaining list, clear known issues, or complete the task, even on a clean provider exit.
  - Zero-quota inspection surfaces: `cortexshift handoff preview` and `cortexshift switch --dry-run` persist nothing, launch nothing, and — for Antigravity — never perform the bootstrap model turn.
  - Architectural Decision Record `ADR-0006-canonical-agent-handoff.md`, and `docs/handoff-protocol.md` promoted from future design to an implemented contract.
  - Flagship integration test exercising the full fake Claude → Codex → Antigravity workflow against a disposable real Git repository, plus restart persistence, live-versus-historical, context budget, command-injection, outgoing-provider-unavailable, and workspace-lease regression coverage. No network, no provider subscription, no real model call.

- **Phase 4: Native Provider Launch & Session Lifecycle**:
  - Segregated runtime ports `ProviderRuntimeAdapter`, `InteractiveProcessRunner`, `WorkspaceLease`, `WorkspaceLeaseManager`, and `SessionStore`.
  - Concrete provider runtime adapters for Claude Code (`ClaudeRuntimeAdapter`), Codex (`CodexRuntimeAdapter`), and Antigravity (`AntigravityRuntimeAdapter`).
  - Interactive process runner (`SubprocessInteractiveProcessRunner`) launching native provider CLIs with direct raw TTY stdio passthrough, finite signal handling, and zero subprocess output scraping or buffering.
  - Prohibition of shell execution (`shell=False`) across all provider invocations with pre-tokenized argument vectors.
  - Canonical same-working-tree model enforcement (`cwd = project_root`) from any invocation subdirectory.
  - Project-local exclusive workspace lease (`FileWorkspaceLease`) utilizing POSIX `fcntl.flock` and Windows `msvcrt.locking` on `.cortexshift/agent.lock`, enforcing the single-mutating-agent invariant per project with authoritative OS file-descriptor semantics (lock file existence on disk never blocks acquisition; deletion advice prohibited; stale DB running records do not block acquisition).
  - Strict TTY contract requiring both stdin and stdout to be interactive for native launches (`stdin.isatty() and stdout.isatty()`), while permitting headless dry runs (`--dry-run`).
  - Active task prerequisite enforcement (`NoActiveTaskError`) preventing launches without a clear task context.
  - SQLite schema migration v3 adding the `sessions` table and indexes on `task_id`, `started_at`, and `provider_id`.
  - Session lifecycle tracking and state transitions (`running`, `completed`, `failed`, `interrupted`) with preserved exit codes and timestamps.
  - Hardened lifecycle contracts: generic non-zero exits strictly map to `PROCESS_CRASHED` without inferring quota/rate limits; spawn failures map to `SPAWN_FAILED` and immediately release the workspace lease.
  - Strict privacy guarantees: zero prompts, conversations, transcripts, or auth credentials stored in SQLite.
  - Dry-run preview capability (`cortexshift run <provider> --dry-run` and `--dry-run --json`) with prompt argument redaction (`<prompt>`).
  - CLI command `cortexshift run <provider>` with `--prompt`, `--dry-run`, and `--json` flags.
  - CLI command group `cortexshift session` with subcommands `list` and `show <id>` supporting formatted tables and machine-readable `--json` output.
  - Domain models `LaunchSpecification`, `SessionStatus`, `SessionExitReason`, and domain error hierarchy (`ProviderNotFoundError`, `UnknownProviderError`, `UnsupportedPromptError`, `WorkspaceLockedError`, `TerminalRequiredError`, `SessionNotFoundError`).
  - Architectural Decision Record `ADR-0005-native-provider-runtime.md`.
  - Comprehensive unit and integration test suite covering process lifecycle, dry-run redaction, cross-process concurrency locks, nested directory scoping, spawn failures, stale DB resilience, and persistence durability.

- **Phase 3: Git Context & Repository Awareness**:
  - Pure NUL-safe (`-z`) porcelain v1 parser (`parse_porcelain_status`) handling spaces, unicode, renames, conflicts, untracked files, and excluding `.cortexshift/` internal state.
  - Native `git` CLI repository inspector adapter (`GitRepositoryInspector`) adhering to strictly read-only execution with sanitized environment (`GIT_TERMINAL_PROMPT=0`, `GIT_PAGER=cat`, `GIT_OPTIONAL_LOCKS=0`) and 10s timeouts.
  - Monorepo and nested project path scoping, translating Git repository paths relative to project root.
  - Comprehensive status detection: branch, commit SHA, dirty state, detached HEAD, unborn (0 commits) repositories, and diff shortstat summaries.
  - Domain models `GitSnapshot`, `RepositoryInspection`, `RepositoryInspectionStatus` and domain error hierarchy (`RepositoryInspectionError`, `GitNotInstalledError`, `NotAGitRepositoryError`, `GitProbeTimeoutError`, `GitProbeError`, `SnapshotNotFoundError`).
  - Abstract ports `RepositoryInspector` and `RepositorySnapshotStore`.
  - SQLite schema migration v2 adding `git_snapshots` table with foreign key to `projects(id)` and query indexes.
  - Persistent snapshot storage in `SQLiteStateStore` implementing `RepositorySnapshotStore`.
  - Application layer `RepositoryService` orchestrating live inspection and persistent snapshot workflows.
  - CLI command suite `cortexshift repo` with subcommands `status`, `snapshot`, `snapshots`, `show` supporting human output and machine-readable `--json`.
  - Safe, non-blocking fallback (exit code 0) for environments without Git or non-Git projects on `cortexshift repo status`.
  - Architectural Decision Record `ADR-0004-git-repository-context.md`.
  - Automated unit and integration test suite with 100% type safety and zero external Git dependencies.

- **Phase 2: Persistent Project & Task State**:
  - Project-local runtime directory `.cortexshift/` with SQLite persistence (`state.sqlite3`).
  - Standard-library `sqlite3` adapter (`SQLiteStateStore`) with WAL mode, foreign key enforcement, and busy timeouts without third-party ORMs.
  - Lightweight deterministic schema migration runner (`schema_metadata`) with forward-safety and rollback protection.
  - Filesystem-based ancestor project root locator (`ProjectLocator`) supporting nested subdirectories without Git dependencies.
  - Idempotent project initialization service and `cortexshift init` command.
  - Task lifecycle service (`TaskService`) managing durable creation, progression updates, activation, and completion.
  - Single active task pointer management in `project_runtime` table with foreign key constraints.
  - Project status service and `cortexshift status` command with Rich human table and machine-readable `--json` format.
  - Task CLI commands: `cortexshift task start`, `list`, `show`, `update`, `activate`, `complete` with machine-readable `--json` surfaces on `status`, `task list`, and `task show`.
  - Canonical task contract preservation: durable storage and restart survival of `objective`, `requirements`, `constraints`, `completed`, `remaining`, `known_issues`, and `current_work` as future handoff inputs.
  - Domain error hierarchy (`ProjectNotInitializedError`, `TaskNotFoundError`, `NoActiveTaskError`, `TaskNotActivatableError`, `UnsupportedSchemaVersionError`, `StateCorruptionError`).
  - Invariant enforcement: zero credential persistence, zero transcript storage, zero destructive auto-reset.
  - Architectural Decision Record `ADR-0003-project-local-persistence.md`.
  - Comprehensive unit and integration test suite covering process restart persistence, isolation, and migrations.

- **Phase 1: Native Provider Discovery & `cortexshift doctor`**:
  - Safe subprocess command execution abstraction (`CommandRunner` port, `SubprocessCommandRunner` adapter) with argument lists, finite timeouts, and error handling without `shell=True`.
  - Passive native provider probes for Claude Code (`claude`), OpenAI Codex (`codex`), and Google Antigravity (`agy`).
  - Best-effort version detection and passive authentication status classification (`claude auth status`, `codex login status`).
  - Strict preservation of privacy and quotas: never sends model prompts (specifically never `agy -p ...`), never reads vendor credential storage, never leaks auth tokens.
  - Diagnostic domain models: `AuthenticationStatus`, `PlatformInfo`, `ProviderDiagnostic`, `DoctorReport`.
  - Application layer `DoctorService` orchestrating provider discovery and privacy-preserving platform metadata collection.
  - `cortexshift doctor` command with Rich terminal formatting and machine-readable `--json` output.
  - Provider filtering via `--provider` / `-p` with validation for unknown provider IDs.
  - Ephemeral diagnostic design without state persistence or disk mutation in Phase 1.
  - Architectural Decision Record `ADR-0002-safe-provider-discovery.md`.
  - Comprehensive automated unit and integration tests with regression guards for prompt and credential invariants.

## [0.1.0] - 2026-09-05

### Added

- Initial repository structure and foundational architecture (Phase 0).
- Pure domain models using Pydantic v2:
  - `Project` entity with collision-resistant identifier (`proj_<uuid>`).
  - `Task` entity with lifecycle status, objectives, requirements, constraints, and progress tracking.
  - `Session` entity with session lifecycle, provider tracking, and exit reasons.
  - `Checkpoint` entity for durable task state capture and disaster recovery.
  - `Handoff` canonical payload modeling the full cross-agent handoff contract.
  - `GitSnapshot` data model for repository and working tree state.
  - `ProviderCapabilities` and extensible `ProviderId` type supporting arbitrary agent providers.
- Abstract ports for clean architectural boundaries:
  - `ProviderAdapter` port defining native CLI orchestration lifecycle.
  - `RepositoryInspector` port for repository inspections and Git state queries.
  - `StateStore` port for local persistence abstraction.
- Initial Typer CLI application skeleton with `cortexshift version` and `--help`.
- Multi-agent development governance via `AGENTS.md`.
- Comprehensive architectural documentation (`docs/architecture.md`, `docs/handoff-protocol.md`, `docs/roadmap.md`).
- Architectural Decision Record `ADR-0001-core-architecture.md`.
- GitHub Actions CI workflow for linting, type-checking, and automated test execution.

# ADR-0005: Native Provider Launch, Workspace Leasing, and Session Lifecycle

- **Status**: Accepted
- **Date**: 2026-09-05
- **Deciders**: CortexShift Core Architecture Team

---

## Context

Phases 0 through 3 established CortexShift's architectural foundation, passive provider discovery, persistent task and project models, and Git repository awareness. Phase 4 introduces the first execution capability: starting and managing native coding-agent processes.

CortexShift coordinates three first-class providers:
- **Claude Code** (`claude`)
- **Codex** (`codex`)
- **Antigravity** (`agy`)

Launching these agents requires balancing conflicting concerns:
1. **Interactive Fidelity**: Coding agents feature rich terminal user interfaces (TUIs) requiring raw TTY passthrough, cursor controls, and direct terminal escape sequences. Buffering, pipe-wrapping, or pseudo-terminal scraping degrades provider UX and introduces fragility.
2. **Safety & Concurrency**: Two coding agents mutating the same working tree simultaneously cause corrupt edits, index collisions, and merge catastrophes. CortexShift must enforce the single-mutating-agent invariant.
3. **Privacy & Security**: Provider prompts, conversation transcripts, API credentials, and internal auth tokens must never be captured, logged, or persisted in CortexShift state.
4. **Clean Hexagonal Layering**: Application orchestration (`RunService`) and domain logic (`LaunchSpecification`, `Session`) must remain strictly provider-agnostic, with all CLI differences encapsulated behind runtime ports.

---

## Decisions

We have made the following foundational architectural decisions for native provider launch and session management in Phase 4:

### 1. Direct TTY Passthrough via `SubprocessInteractiveProcessRunner`
CortexShift launches native provider processes using standard library `subprocess.Popen` with inherited standard input, output, and error streams:
- Provider TUIs take direct control of the user's terminal window.
- CortexShift does not scrape, buffer, capture, or redact provider output during execution.
- Interactive launch requires a full terminal environment: both `sys.stdin.isatty()` and `sys.stdout.isatty()` must evaluate to `True`. If either is non-interactive, launch is rejected with `TerminalRequiredError`, advising use of `--dry-run` (which requires no TTY).

### 2. Strict Prohibition of Shell Execution (`shell=False`)
All process launches execute with `shell=False`:
- Executable path and arguments are passed strictly as pre-tokenized vectors (`argv: list[str]`).
- Prohibits string concatenation or shell expansions, eliminating command injection vulnerabilities even if user prompts contain shell metacharacters (`;`, `&&`, `|`, `` ` ``).

### 3. Canonical Same-Working-Tree Enforcement
In accordance with the same-working-tree architectural invariant:
- Irrespective of the subdirectory from which `cortexshift run <provider>` is called, the child process is launched with `cwd = project_root`.
- Subdirectory paths are resolved upwards to the enclosing project root via `ProjectLocator`.

### 4. Workspace Lease via OS Advisory Lock (`FileWorkspaceLease`)
To enforce the single-mutating-agent invariant per project:
- A non-blocking exclusive advisory lock is acquired on `<project_root>/.cortexshift/agent.lock` prior to launching the provider process.
- Implemented using POSIX `fcntl.flock` (with Windows fallback via `msvcrt.locking`).
- If another session holds the lock, launch fails immediately with `WorkspaceLockedError` and exit code 1.
- The OS advisory lock on the open file descriptor is authoritative; the mere existence of the lock file on disk does NOT indicate an active lock. Users should never be instructed to delete `.cortexshift/agent.lock` as normal error recovery.
- Stale database session records (e.g. from an ungraceful shutdown leaving status `running` in SQLite) do not impede workspace leasing; only the live OS advisory lock controls exclusivity.
- Lock release is guaranteed via context managers and `try...finally` blocks upon child process exit, interruption, or spawn failure.
- Independent projects maintain distinct lock files and do not block one another.

### 5. Active Task Requirement
CortexShift is task-centric:
- Launching a provider requires an active Task in the project (`active_task_id`).
- If no task is active, launch aborts immediately with `NoActiveTaskError`, displaying instructions to run `cortexshift task start`.
- CortexShift never silently invents or auto-activates tasks.

### 6. Provider Runtime Adapter Port & Launch Contracts
Provider variance is strictly isolated behind `ProviderRuntimeAdapter`:
- **Claude Code (`ClaudeRuntimeAdapter`)**: Invokes native `claude`. If an initial prompt is provided, it is passed as a trailing argument: `["claude", "<prompt>"]`.
- **Codex (`CodexRuntimeAdapter`)**: Invokes native `codex`. If an initial prompt is provided, it is passed as a trailing argument: `["codex", "<prompt>"]`.
- **Antigravity (`AntigravityRuntimeAdapter`)**: Invokes native `agy`. In interactive mode, initial prompts are rejected with `UnsupportedPromptError` to respect Antigravity's interactive session contract.

### 7. SQLite Schema Migration v3 (`sessions` table)
Session execution history is persisted in SQLite via schema version 3:
- Table `sessions` stores: `id`, `task_id`, `provider_id`, `native_session_id`, `status`, `started_at`, `ended_at`, `exit_reason`, `exit_code`, `metadata`.
- Foreign key constraint: `task_id REFERENCES tasks(id) ON DELETE CASCADE`.
- Indexes: `idx_sessions_task_id`, `idx_sessions_started_at`, `idx_sessions_provider_id`.
- Forward-safe, transactional migration (`_migrate_v3`) preserves all existing Phase 1/2/3 project, task, and Git snapshot records.

### 8. Strict Privacy: Zero Prompt or Transcript Persistence
In accordance with zero-credential and structured-canonical-state invariants:
- CortexShift never stores prompt strings, conversational transcripts, or provider credentials in SQLite.
- Dry-run previews (`--dry-run` and `--dry-run --json`) redact prompt arguments to `<prompt>` and indicate `prompt_supplied: true`.
- Native provider authentication remains entirely delegated to each tool's native configuration.

### 9. Signal Handling & Lifecycle Durability
- `Session` is initialized and persisted with status `running` immediately before process spawn.
- Normal child exit (code 0) transitions status to `completed` with reason `normal_completion`.
- Non-zero child exit transitions status to `failed` with reason `process_crashed` and preserves the exact exit code.
- Phase 4 strictly maps generic non-zero exit codes to `process_crashed` and does not infer `quota_exhausted` or `rate_limited` without structured provider error signals.
- Spawn errors (e.g. missing binary or execution permission failure) transition status to `failed` with reason `spawn_failed`, immediately release the workspace lease, and bubble the error to CLI.
- Keyboard interrupts (`SIGINT` / `Ctrl+C`) caught during execution transition status to `interrupted` with reason `user_interrupted` and exit code 130.
- All session records persist durably across store reopens.

---

## Consequences

### Positive
- Users can run their preferred native coding agent seamlessly through CortexShift without altering their existing CLI auth or configuration.
- Single-mutating-agent invariant prevents destructive concurrent workspace edits.
- Workspace lease releases cleanly even across crashed processes or signal interruptions.
- Audit trail of sessions (start time, end time, provider, status, exit code) is available via `cortexshift session list` and `cortexshift session show`.
- Zero prompts or credentials captured or logged.

### Negative / Trade-offs
- Because stdio is directly inherited by the child process, CortexShift cannot observe live token usage or native session identifiers in Phase 4 unless exposed via provider hooks or post-run artifacts in future phases.
- Non-interactive / headless provider execution is deferred to Phase 6.

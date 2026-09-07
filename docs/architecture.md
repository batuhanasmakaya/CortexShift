# CortexShift Architecture

> **Switch agents. Keep the context.**

CortexShift is a provider-agnostic, local developer tool that allows a software development task to move seamlessly between AI coding agents (such as Claude Code, OpenAI Codex, Google Antigravity, and future agents) without losing critical development context.

---

## 1. Core Problem & Product Goal

Modern AI coding agents operate as self-contained conversational silos. When a developer hits a rate limit, quota ceiling, or capability boundary with one agent (e.g. Claude Code), switching to another agent (e.g. Codex or Antigravity) today requires tedious manual context transfer: explaining the architecture, repeating constraints, listing modified files, and recounting which tests failed.

CortexShift solves this problem by decoupling the **Task** from any ephemeral AI **Session**.

```text
Traditional Model (Siloed & Fragile):
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│ Claude Code  │     │ OpenAI Codex │     │ Antigravity  │
│ Conversation │     │ Conversation │     │ Conversation │
└──────┬───────┘     └──────┬───────┘     └──────┬───────┘
       │                    │                    │
   [Context Lost]       [Context Lost]       [Context Lost]

CortexShift Model (Persistent Task Core):
                  ┌────────────────────┐
                  │    CortexShift     │
                  │  Persistent Task   │
                  └─────────┬──────────┘
                            │
       ┌────────────────────┼────────────────────┐
       ▼                    ▼                    ▼
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│ Claude Code  │────▶│ OpenAI Codex │────▶│ Antigravity  │
│   (Worker)   │     │   (Worker)   │     │   (Worker)   │
└──────────────┘     └──────────────┘     └──────────────┘
       │                    │                    │
       └────────────────────┴────────────────────┘
                            │
              SAME LOCAL REPOSITORY / WORKTREE
```

---

## 2. Core Terminology

- **Project**: The overarching software repository and its durable conventions, architecture rules, and invariants.
- **Task**: The primary unit of work in CortexShift. A persistent, stateful objective comprising requirements, constraints, completed items, in-flight work, decisions, and known issues.
- **Session**: A bounded execution window during which a specific coding agent works on a task. Sessions are transient; tasks endure.
- **Provider**: An external coding agent ecosystem or CLI (e.g., Claude Code, OpenAI Codex, Google Antigravity).
- **Canonical State**: The structured, authoritative representation of a task maintained by CortexShift, independent of any provider's internal conversation format.
- **Checkpoint**: A periodic or on-demand snapshot of task progress (done, current, next, decisions, issues, files, tests) enabling recovery from unexpected terminations.
- **Handoff**: A structured payload delivered to an incoming agent summarizing the canonical state, accompanied by a mandate to inspect the actual repository.

---

## 3. The Source-of-Truth Hierarchy

A fundamental architectural invariant of CortexShift is that **agent claims are advisory, not definitive**. An outgoing agent may hallucinate, report stale test results, or misunderstand repository state.

All agents operating within CortexShift adhere to this truth hierarchy:

```text
▲ Highest Authority
│ 1. Actual Repository Files (The Filesystem is Reality)
│ 2. Git State (HEAD commit, working tree diffs, branch)
│ 3. Verified Command & Test Execution Results (Empirical verification)
│ 4. CortexShift Canonical Task State (Persisted task record)
│ 5. Previous Agent Summaries & Handoff Notes (Advisory only)
▼ Lowest Authority
```

> **Handoff Directive**: Handoff documents instruct incoming agents:
> *"The handoff package is advisory. The local repository files and verified command outputs are the ultimate source of truth. Always inspect files and verify critical assumptions before proceeding."*

---

## 4. Memory Scopes

CortexShift models memory across three explicit scopes to prevent context pollution:

```text
┌────────────────────────────────────────────────────────┐
│ PROJECT MEMORY                                         │
│ • Identity, durable architectural invariants           │
│ • Coding conventions, prohibited patterns, tech stack   │
│ • Long-term decisions (ADRs)                           │
├────────────────────────────────────────────────────────┤
│ TASK MEMORY                                            │
│ • Objective, specific requirements, active constraints  │
│ • Work completed, in-flight work, remaining backlog    │
│ • Task-scoped architectural decisions & known defects  │
├────────────────────────────────────────────────────────┤
│ SESSION MEMORY                                         │
│ • Provider identifier (e.g., "claude", "antigravity")   │
│ • Provider-native session/conversation ID              │
│ • Start/end timestamps, exit reason, raw logs          │
└────────────────────────────────────────────────────────┘
```

These three scopes are strictly separated in domain entities and must never be conflated.

---

## 5. Architectural Invariants

1. **Task Ownership**: CortexShift owns the task lifecycle. No provider session owns the task.
2. **Native-Agent-First**: CortexShift never attempts to emulate or rebuild agent CLIs. It orchestrates official native CLIs (e.g. `claude`, `codex`, `antigravity`) via non-invasive adapters.
3. **Provider-Agnostic Core**: The core business and domain layers must contain zero provider-specific conditionals. All provider mechanics are encapsulated behind the `ProviderAdapter` port.
4. **Same-Working-Tree Model**: For initial versions, all agents execute sequentially against the same local repository worktree. There is no repository copying or remote synchronization.
5. **One Mutating Agent at a Time**: To avoid conflicting writes and corrupted state, exactly one coding agent has mutation access to the workspace at any time. Concurrent multi-agent execution is explicitly out of scope.
6. **Local-First & Zero Telemetry**: CortexShift runs 100% locally. It requires no cloud backend, external databases, or remote telemetry.
7. **Zero Credential Persistence**: CortexShift never requests, reads, stores, or handles provider API tokens, SSH keys, or secrets. Native CLIs manage their own authentication mechanisms.
8. **Structured Canonical State over Transcripts**: Full conversational transcripts are bulky, proprietary, model-dependent, and prone to context limits. CortexShift distills actionable task state rather than transplanting raw conversational transcripts (`capture_transcripts = false` by default).

---

## 6. Hexagonal Architecture & Dependency Boundaries

CortexShift follows Ports and Adapters (Hexagonal Architecture) to ensure absolute isolation between domain logic, persistence, and provider tooling:

```text
  Interface Adapters
   ├── CLI Layer (Typer / Rich)
   ├── MCP Layer (local stdio server)
   └── TUI Layer (Textual control center)
         │
         ▼
  Application Layer (Use-Cases / Workflows)
         │
         ▼
  Ports Layer (Abstract Protocols)
   ├── ProviderAdapter / ProviderRuntimeAdapter
   ├── ProviderHandoffAdapter
   ├── RepositoryInspector
   ├── StateStore / SessionStore / HandoffStore
   └── InteractiveProcessRunner / HeadlessProviderRunner
         ▲
         │ (implements)
  Adapters Layer (Concrete Implementations)
   ├── providers/ (Claude, Codex, Antigravity)
   ├── persistence/ (SQLite, FileSystem)
   └── repository/ (Git CLI / pygit)

  ──────────────────────────────────────────
  Domain Layer (Pure Entities & Value Objects)
   (Project, Task, Session, Checkpoint, HandoffPayload, HandoffRecord,
    GitSnapshot, LaunchSpecification, ProviderCapabilities)
   * Domain has NO dependencies on outer layers *
```

### Dependency Rules:
- `domain` depends only on Python standard libraries and Pydantic v2. It never imports `cli`, `application`, or `adapters`.
- `ports` define interfaces using domain types.
- `application` coordinates workflows using `domain` and `ports`.
- `adapters` implement `ports` and may use third-party tools or external CLIs.
- `cli` invokes `application` services and formats output using Rich.
- `mcp` and `tui` are peer interface adapters: they invoke the same `application` services, never reimplement their rules, and never reach past them into persistence, Git, or provider transports.

---

## 7. Native Provider Discovery Flow (Phase 1)

CortexShift discovers and diagnoses locally available coding-agent CLIs through passive inspection:

```text
CLI (`cortexshift doctor` / `--json`)
 │
 ▼
DoctorService (Application)
 │
 ▼
ProviderDiscoveryPort (Ports)
 │ (implemented by)
BuiltinProviderDiscovery (Adapters)
 │
 ├── ClaudeProviderProbe (`claude --version`, `claude auth status`)
 ├── CodexProviderProbe (`codex --version`, `codex login status`)
 └── AntigravityProviderProbe (`agy --version`)
 │
 ▼
CommandRunner Port (SubprocessCommandRunner)
 │ (shell=False, argument arrays, finite timeout)
 ▼
Native Executables (PATH / OS Subprocess)
```

### Discovery Principles:
- **Doctor != Health-Check-by-Model-Call**: `cortexshift doctor` performs passive environment inspection only. It NEVER sends model prompts, executes headless runs, or consumes token quotas to determine authentication or health.
- **Zero Credential Inspection**: CortexShift never reads local credential files (`~/.claude/`, `~/.codex/`, `~/.gemini/`) or Keychain records.
- **Safe Subprocess Execution**: External commands are invoked as argument lists without `shell=True`, bound by finite timeouts, with sensitive command output discarded after classification.
- **Support for Uncertainty**: When a provider does not expose a non-interactive auth status check (e.g., Antigravity `agy`), CortexShift explicitly and honestly reports `Authentication: Unknown`.

---

## 8. Persistence Architecture (Phase 2)

Phase 2 introduces durable, project-local persistence for CortexShift projects, tasks, and runtime states using a lightweight SQLite database.

```text
CLI (`cortexshift init`, `status`, `task`)
 │
 ▼
Application Services
 │
 ├── ProjectLocator (ancestor discovery for .cortexshift/state.sqlite3)
 ├── ProjectInitializationService
 ├── TaskService
 └── ProjectStatusService
 │
 ▼
Persistence Port (`StateStore`)
 │
 ▼
SQLiteStateStore (Adapters)
 │ (stdlib sqlite3, WAL mode, foreign keys, schema migrations)
 ▼
.cortexshift/state.sqlite3
```

### Persistence Invariants & Design Principles:
- **Project-Local Only**: State resides exclusively inside `.cortexshift/state.sqlite3` at the project root. There is zero global database (`~/.cortexshift/`), zero cloud storage, and zero telemetry.
- **Nearest Ancestor Project Discovery**: Commands executed from nested subdirectories automatically discover the nearest initialized ancestor containing `.cortexshift/state.sqlite3`. Discovery operates strictly on filesystem paths and does NOT invoke or depend on Git.
- **Standard Library SQLite without ORM**: Built using Python's standard `sqlite3` module. No SQLAlchemy, SQLModel, or external ORM dependencies.
- **Connection Safety & Concurrency**: Connections enable `PRAGMA foreign_keys = ON;`, `PRAGMA busy_timeout = 5000;`, and `PRAGMA journal_mode = WAL;`. Temporary `-wal` and `-shm` files reside in `.cortexshift/` and are gitignored.
- **Explicit Schema Versioning & Migrations**: Schema migrations are tracked in `schema_metadata`. Version 1 defines `projects`, `tasks`, and `project_runtime`. If CortexShift opens a database with a higher version than supported, it fails safely (`UnsupportedSchemaVersionError`) and refuses to modify the state.
- **Transactional State Transitions**: Multi-step state changes (creating tasks, activating tasks, completing tasks) execute within strict transaction boundaries with automatic rollback on error.
- **Separation of Project vs. Task vs. Runtime State**:
  - `projects`: Durable identity, name, canonical absolute repository path, and created timestamp.
  - `tasks`: Durable canonical task state — including title, objective, requirements, constraints, status, progress (`completed`, `remaining`), in-flight current work, known issues, and timestamps — serving as essential structured input for future agent handoffs.
  - `project_runtime`: Mutable runtime pointers, specifically `active_task_id` (exactly one active task pointer per project).
- **Zero Credential & Zero Transcript Storage**: No columns or tables store API keys, tokens, passwords, or conversational transcripts.

---

## 9. Repository Awareness & Git Context (Phase 3)

Phase 3 connects live Git repository inspection and snapshot persistence to CortexShift's context engine, providing an empirical source of development truth.

```text
CLI (`cortexshift repo status`, `snapshot`, `snapshots`, `show`)
 │
 ▼
RepositoryService (Application)
 │
 ├── Live Inspection Flow:
 │    │
 │    ▼
 │   RepositoryInspector Port
 │    │ (implemented by)
 │   GitRepositoryInspector (Adapters)
 │    │ (native git CLI, sanitized env, 10s timeouts)
 │    ▼
 │   Native Git Subprocess (`git status -z`, `git diff --shortstat`, etc.)
 │    │
 │    ▼
 │   parse_porcelain_status (pure NUL-safe parser, monorepo scoping, .cortexshift/ exclusion)
 │
 └── Persistence Flow:
      │
      ▼
     RepositorySnapshotStore Port
      │ (implemented by)
     SQLiteStateStore (Adapters)
      │ (schema v2 `git_snapshots`, foreign keys, query indexes)
      ▼
     .cortexshift/state.sqlite3
```

### Git Inspection Invariants & Design Principles:
- **Native-Agent-First & Read-Only**: CortexShift invokes the host's native `git` executable via `CommandRunner`. All Git interactions are strictly read-only and non-mutating (`git status`, `git branch`, `git rev-parse`, `git diff --shortstat`). CortexShift never stages, commits, resets, or cleans files.
- **Sanitized Execution Environment**: Subprocesses run with `GIT_TERMINAL_PROMPT=0`, `GIT_PAGER=cat`, and `GIT_OPTIONAL_LOCKS=0` to prevent interactive credential hangs, terminal pagers, or background lock contentions.
- **NUL-Delimited Porcelain Status**: Parses `git status --porcelain=v1 -z --untracked-files=all` using pure string parsing. NUL (`\0`) separation guarantees deterministic handling of filenames containing spaces, quotes, newlines, and Unicode characters. Renames (`R dest\0orig\0`) and conflicts are cleanly parsed.
- **Monorepo & Project Scoping**: Translates Git paths relative to the CortexShift project root. Changes outside the project are filtered out.
- **Private State Exclusion**: The parser automatically excludes `.cortexshift/` runtime state so SQLite databases are never reported as untracked files.
- **Historical Observation Semantics**: Stored `GitSnapshot` records in SQLite schema v2 (`git_snapshots`) are immutable records of state at capture time. Live repository inspection outranks stored snapshots in the truth hierarchy.
- **Graceful Degradation**: If Git is not installed or the directory is not a Git repository, `cortexshift repo status` exits cleanly (exit code 0) with diagnostic output.

---

## 10. Native Provider Runtime & Session Lifecycle (Phase 4)

Phase 4 introduces native provider process execution, workspace concurrency management, and session lifecycle persistence:

```text
CLI (`cortexshift run <provider> [--prompt ...] [--dry-run]`, `cortexshift session list|show`)
 │
 ▼
RunService (Application)
 │
 ├── Validation & Context Resolution:
 │    ├── ProjectLocator (resolves project root from any subdirectory)
 │    ├── Active Task Check (strictly requires active task)
 │    ├── TTY Validation (checks interactive terminal)
 │    └── Provider Runtime Adapter Registry
 │
 ├── Launch Specification Flow:
 │    │
 │    ▼
 │   ProviderRuntimeAdapter Port (`ClaudeRuntimeAdapter`, `CodexRuntimeAdapter`, `AntigravityRuntimeAdapter`)
 │    │ (encapsulates CLI argv contracts & prompt rules)
 │    ▼
 │   LaunchSpecification (domain value object; argv, cwd=project_root, shell=False)
 │
 ├── Concurrency Control Flow:
 │    │
 │    ▼
 │   WorkspaceLease Port (`FileWorkspaceLease`)
 │    │ (non-blocking OS advisory lock via fcntl.flock on .cortexshift/agent.lock)
 │    ▼
 │   Single-Mutating-Agent Guarantee per Project
 │
 ├── Execution Flow:
 │    │
 │    ▼
 │   InteractiveProcessRunner Port (`SubprocessInteractiveProcessRunner`)
 │    │ (direct TTY stdio passthrough, signal trapping)
 │    ▼
 │   Native Provider Process (`claude`, `codex`, `agy`)
 │
 └── Persistence Flow:
      │
      ▼
     SessionStore Port (implemented by SQLiteStateStore)
      │ (schema v3 `sessions` table, status transitions, exit codes)
      ▼
     .cortexshift/state.sqlite3
```

### Runtime Invariants & Design Principles:
- **Direct Terminal Passthrough & Strict TTY Contract**: Inherits raw terminal stdio directly without pipe wrapping, buffering, or TUI scraping, allowing coding agents to present rich TUIs and receive keyboard inputs natively. Interactive launch strictly requires both `stdin.isatty()` and `stdout.isatty()`. Non-TTY environments can safely simulate launch via `--dry-run`.
- **Strictly `shell=False`**: Process execution uses explicitly tokenized argument arrays (`argv: list[str]`). Zero shell expansion; immune to command injection.
- **Canonical Same-Working-Tree Execution**: Irrespective of which subdirectory invoked the command, the native provider process is launched with `cwd = project_root`.
- **Authoritative OS Workspace Lease**: Enforces single-mutating-agent invariant per project using OS-level advisory locks (`fcntl.flock` / `msvcrt.locking` on `.cortexshift/agent.lock`). The OS file-descriptor lock is authoritative; existence of the lock file on disk does not imply an active lease, and users are never advised to delete it. Stale database session records never block acquiring a free lease. Fails cleanly with `WorkspaceLockedError` if another session holds the lock.
- **Active Task Prerequisite**: Launch strictly requires an active Task; CortexShift never creates or activates tasks implicitly.
- **Dry-Run Simulation**: `--dry-run` and `--dry-run --json` display launch parameters without executing processes, requiring a TTY, or mutating session tables; prompts are always redacted as `<prompt>`.
- **Zero Prompt, Transcript, or Credential Storage**: Database never stores prompt texts, conversation transcripts, or API keys. Native provider authentication is preserved intact.
- **Durable Session State (Schema v3)**: Tracks session lifecycle (`running`, `completed`, `failed`, `interrupted`), exit codes, and timestamps. Generic non-zero exits strictly map to `process_crashed`; spawn failures map to `spawn_failed` and immediately release the lease. Phase 4 never infers `quota_exhausted` or `rate_limited` from generic non-zero exits.

---

## 11. Canonical Handoff & Manual Agent Switching (Phase 5)

Phase 5 delivers CortexShift's central product promise: one Task, multiple coding agents, no need to manually re-explain the work.

```text
CLI (`cortexshift switch <provider>`, `cortexshift handoff preview|list|show`)
 │
 ▼
SwitchService (Application)
 │
 │  Validation: project → active Task → source Session → target provider →
 │              same-provider rejection → target executable → TTY → workspace lease
 │
 ▼
                        ┌── Task State
                        │
                        ├── Source Session
                        │
SwitchService ──────────┼── Live Git Inspection
                        │
                        └── Git Snapshot
                              │
                              ▼
                       HandoffBuilder
                              │
                              ▼
                     Canonical Handoff v1
                              │
                    ┌─────────┴─────────┐
                    ▼                   ▼
               HandoffStore      HandoffRenderer
                                      │
                                      ▼
                              Delivery Strategy
                         ┌────────┬────────────┐
                         ▼        ▼            ▼
                      Claude    Codex    Antigravity
                         │        │            │
                         └────────┴────────────┘
                                  │
                                  ▼
                        ProviderSessionLauncher
                         (target CortexShift Session)
```

### Phase 5 Components

- **`SwitchService`** (application): orchestrates the whole handoff. Renders no Rich output, knows nothing about Typer, contains no SQL, and constructs no provider-specific commands.
- **`HandoffBuilder`** (application): pure, deterministic construction of the canonical `HandoffPayload` from durable state. No I/O, no provider, no model.
- **`HandoffRenderer`** (application): one provider-neutral renderer producing the bounded receiving-agent package. Provider transport differs; engineering context does not, so there are deliberately not three near-duplicate templates.
- **`ProviderSessionLauncher`** (application): the shared Session lifecycle extracted from `RunService` in Phase 5. It assumes the caller already resolved project/task and already holds the workspace lease, so `switch` never nests a second advisory lock. Both `run` and `switch` use it.
- **`select_source_session`** (application): chooses the most recent *meaningful* Session on the active task, reading only durable CortexShift records — never provider transcript history.
- **`HandoffStore`** (port): a narrow, cohesive persistence port. The Phase 2 `StateStore` interface is deliberately not re-expanded; one SQLite adapter implements several ports.
- **`ProviderHandoffAdapter`** (port): encapsulates all delivery variance, so the application core carries no `if target == "antigravity":` branching.
- **`HeadlessProviderRunner`** (port) / **`SubprocessHeadlessProviderRunner`** (adapter): one bounded, non-interactive, shell-free provider turn with a model-turn-appropriate timeout. Distinct from the short-timeout diagnostic `CommandRunner`.

### Handoff Invariants & Design Principles

- **The outgoing agent is never required.** Handoffs derive deterministically from the canonical Project, canonical Task, previous Session metadata, live Git inspection, and the snapshot captured at switch time. No outgoing model call is ever made, and the source provider's executable is never even resolved. This is the whole point: the user switches *because* the previous agent became unusable.
- **Structured state, never transcript transplantation.** CortexShift does not read, convert, or replay provider conversation storage, and never scrapes a TUI.
- **Handoff protocol versioning.** `HANDOFF_PROTOCOL_VERSION = 1` is persisted per handoff and is independent of the SQLite schema version, so canonical fields and prompt formatting evolve separately from database migrations.
- **Record vs. payload.** `HandoffPayload` holds point-in-time engineering context; `HandoffRecord` wraps it with orchestration and delivery metadata.
- **Honest unknown state.** Absent decision and test records are stated as unknown rather than invented. A provider exiting 0 is never read as "tests pass".
- **Advisory completed work.** Recorded completed items inform `COMPLETED` and `DO NOT REDO` conservatively; the receiving agent is told to verify, never to skip verification, and never to restart.
- **Live capture under the lease.** The workspace lease is held continuously from repository observation through target provider runtime, so no other CortexShift agent can invalidate the observation before the receiving agent starts.
- **Git optionality with honest markers.** `git_not_installed` and `not_git_repository` continue the handoff with an explicit marker and no snapshot row; fake `GitSnapshot` rows are never created. An unexpected `probe_error` fails the switch safely instead of shipping an unreliable observation.
- **Bounded transport, complete storage.** Rendering is capped at 48,000 characters using deterministic character and list budgets — no tokenizer, no summarizer, no model. Mandatory context is never dropped; omissions are always reported with exact counts; the persisted payload is never truncated and remains retrievable via `cortexshift handoff show ID --json`.
- **Rendered prompts are never persisted.** They are transport representations, re-derived from canonical state on demand.
- **Delivery ≠ session outcome.** `HandoffStatus` (`prepared`/`delivered`/`failed`) describes whether context arrived; the target Session remains the source of truth for how the receiving coding session ended. Failure codes are safe machine classifications that never embed raw provider output.
- **Switch never mutates task progress.** Not even a clean exit 0 marks work complete; CortexShift does not infer business progress from process status.
- **Manual switching only.** No quota parsing, no automatic provider selection, no automatic termination or auto-switching. The user runs `cortexshift switch TARGET` explicitly.
- **Historical handoff semantics.** A stored handoff is an immutable record of what was true at capture time. Live repository inspection always outranks it, exactly as with `GitSnapshot`.

### Provider Delivery Strategies

| Provider | Strategy | Bootstrap model turn |
| :--- | :--- | :--- |
| Claude Code | `direct_initial_prompt` — `claude --session-id UUID <context>` or `claude --resume ID <context>` | No |
| Codex | `read_only_bootstrap_then_resume` — `exec --sandbox read-only --json <context>` (or exact `exec resume`), then `resume ID` | Yes — one read-only turn |
| Antigravity | `plan_bootstrap_then_resume` | Yes — one read-only planning turn |

Antigravity uses two documented native capabilities in sequence: a read-only headless plan turn (`agy --mode=plan -p <context> --output-format json`) that ingests the context, followed by an interactive resume of that same conversation (`agy --conversation <id>`). Only `conversation_id` and `status` are parsed; the response is discarded. Permission-bypass flags are never used, and CortexShift never automates TUI keystrokes — so the user may need to approve continuation in the resumed UI, which the CLI states plainly.

See [ADR-0006](decisions/ADR-0006-canonical-agent-handoff.md) and the [Canonical Handoff Protocol](handoff-protocol.md) for the full contract.

---

## 12. Native Session Continuity (Phase 6)

```text
                    CortexShift Task
                          │
             ┌────────────┼────────────┐
             ▼            ▼            ▼
          Claude        Codex     Antigravity
          native A      native B      native C
             │            │            │
             ▼            ▼            ▼
        CS Sessions   CS Sessions   CS Sessions
        1 → 7 → 12    2 → 8 → 13   4 → 9 → 14
```

A provider-native conversation is not a CortexShift Session. The Task owns canonical work state; handoffs carry that state across providers; native IDs add same-provider history continuity. One native ID can belong to many sequential invocation records. Schema v5 adds nullable `Session.resumed_from_session_id`, a self-referencing FK with `ON DELETE SET NULL`. It does not manufacture IDs for legacy records or store a redundant resumability flag.

`ResumeService` resolves project/task/runtime adapter through the existing run infrastructure, selects exact native history, acquires the workspace lease, creates a linked invocation, and delegates lifecycle finalization to `ProviderSessionLauncher`. No handoff builder or repository inspector is involved in plain resume. Provider-specific argv is built behind the optional `ProviderNativeSessionAdapter` port. A small immutable `NativeSessionCapabilities` value describes partial provider support; unsupported runtimes need no placeholder operations.

`SwitchService` uses native capabilities to select a prior target invocation on the same active Task. The newest eligible `started_at` wins across the complete Session history. Exact resumability requires a safe ID, exact-resume support, and finalized process exit evidence; spawn failures and unfinished records are excluded. The default auto policy resumes eligible targets with a fresh handoff; absent eligible history it uses managed-new delivery. `--new-session` and `--resume-session` provide mutually exclusive overrides. Every invocation remains distinct and the outgoing source is still the latest meaningful work Session.

All provider transport variance remains behind runtime and handoff adapters. Claude preallocates UUID4; Codex captures JSONL identity during a read-only turn; Antigravity captures the plan-bootstrap conversation ID. Returning Codex/Antigravity bootstraps must confirm the same requested ID. Errors never trigger a fresh-conversation fallback. Old Session rows remain unchanged.

Fresh canonical handoff context explicitly supersedes stale native assumptions while preserving the repository/Git/test truth hierarchy. Native conversation memory cannot replace the Task. No private provider storage is read; no transcripts, rendered context, bootstrap responses, credentials, or SDKs are introduced. Interactive stdio passes through directly and both stdin/stdout must be TTYs. All actual runs hold the OS advisory workspace lease; dry runs create no canonical state and perform no provider work.

Plain Codex and Antigravity launches may have unknown IDs. App Server empty-thread-to-CLI continuity is not assumed; arbitrary user prompts never trigger hidden bootstrap turns. See [ADR-0007](decisions/ADR-0007-native-session-continuity.md) for exact contracts and limitations.

---

## 13. Checkpoints, Crash Recovery & Handoff Enrichment (Phase 7)

```text
Agent Active in Workspace
  │
  ├─► Periodic / Milestone: cortexshift checkpoint create (No lease acquired, zero contention)
  │
  ▼
Unexpected Termination (Quota, SIGKILL, Terminal close, Machine crash)
  │ (Workspace lease released via OS advisory lock mechanics)
  ▼
Operator / Agent invokes: cortexshift recover
  │
  ├─► Acquires exclusive workspace lease (.cortexshift/agent.lock)
  ├─► Identifies unfinalized sessions (INITIALIZING, RUNNING)
  ├─► Honestly reconciles: status=interrupted, exit_reason=unexpected_termination
  │   Records reconciled_at=<UTC>, leaves ended_at=None
  ├─► Inspects live Git working tree and commit state
  ├─► Creates immutable RECOVERY checkpoint
  └─► Releases workspace lease
  ▼
Next Agent Switch: cortexshift switch <target>
  │
  ├─► Ingests latest checkpoint (RECOVERY, MANUAL, or SESSION_END)
  ├─► Aggregates decisions across the task's whole checkpoint history
  ├─► Enriches handoff with decisions & reported test status (provenance disclaimer)
  └─► Delivers enriched handoff package to incoming provider
```

### Checkpoint Protocol v1
Checkpoints are stored as immutable `CheckpointRecord` records wrapping a structured `CheckpointPayload`:
- **`task`**: Snapshot of canonical task metadata, objective, status, and completion state.
- **`git`**: Observed live commit SHA, branch, detached HEAD, dirty flag, modified files, diff summaries.
- **`source_session`**: Details of the session that generated the checkpoint.
- **`decisions`**: Bounded engineering decisions (`MAX_DECISION_CHARS = 1000`). Literal to this checkpoint: a checkpoint never absorbs an earlier one's decisions, and handoffs recover task-level durability by aggregating across the task's checkpoint history instead.
- **`test_status`**: Reported test execution outcome with explicit `reported_unverified` provenance.
- **`operator_note`**: Contextual human or agent note (`MAX_OPERATOR_NOTE_CHARS = 2000`).

### Persistence Port: `CheckpointStore`
Persistence boundaries are encapsulated behind `CheckpointStore`, implemented on `SQLiteStateStore` in schema v6:
- `checkpoints` table with FK constraints to `projects`, `tasks`, `sessions`, and `git_snapshots`.
- Four dedicated indexes: `idx_checkpoints_task_created`, `idx_checkpoints_session`, `idx_checkpoints_snapshot`, `idx_checkpoints_kind`.
- `sessions.reconciled_at TEXT` tracks honest reconciliation timestamps.
- `handoffs.source_checkpoint_id TEXT REFERENCES checkpoints(id) ON DELETE SET NULL` links handoffs to their source checkpoint.

### Honest Reconciliation Invariants
1. **Never Fabricate Process End Times**: Stale sessions record `reconciled_at`, preserving `ended_at = None`.
2. **Never Fabricate Test Verification**: Checkpoint test summaries are qualified as unverified reports in handoff payloads.
3. **Historical State Never Outranks Live Truth**: Live Git inspection and verified commands always outrank historical checkpoint observations.
4. **Cooperative Milestones Without Lease**: Manual checkpoint creation never contends for the workspace lease.
5. **Crash Recovery Under Exclusive Lease**: Recovery operations strictly require exclusive workspace leasing.

See [ADR-0008](decisions/ADR-0008-checkpoint-and-recovery.md) for detailed decisions.

---

## 14. MCP Shared State & Agent Self-Reporting (Phase 8)

Phase 8 provides an active coding agent with a first-class structured communication channel into CortexShift via the Model Context Protocol (MCP). Rather than treating agents as black-box subprocesses that can only receive initial context and exit, agents can now dynamically query state and self-report progress mid-flight.

```text
               Active Provider Session (Claude / Codex / Antigravity)
                                         │
                                         ▼ spawns child process
                     python -m cortexshift mcp serve (Local stdio only)
                                         │
                         ┌───────────────┴───────────────┐
                         ▼                               ▼
                 [Read Operations]               [Write Operations]
           • get_project_context           • set_current_work
           • get_current_task              • mark_completed
           • get_latest_checkpoint         • add_remaining
           • get_repository_status         • record_issue
           • cortexshift://project         • record_decision
           • cortexshift://task            • create_checkpoint
           • cortexshift://checkpoint/latest
           • cortexshift://repository
                         │                               │
                         └───────────────┬───────────────┘
                                         ▼
                            McpApplicationFacade
                                         │ (Bypasses workspace lease; uses WAL concurrency)
                                         ▼
                                  SQLiteStateStore
```

### Architectural Principles for MCP

1. **Local Stdio Transport Exclusively**: CortexShift MCP operates strictly via local subprocess stdio. No network listeners, HTTP/SSE endpoints, or sockets are created, maintaining zero telemetry and local-first privacy.
2. **Pure Stdout Wire Protocol**: Standard output (`stdout`) is reserved strictly and exclusively for valid MCP JSON-RPC protocol frames. All diagnostics, informational logs, and CLI output are routed to `stderr` or silenced.
3. **Strict Context Binding**: Server binds to `McpExecutionContext` resolved from environment variables (`CORTEXSHIFT_PROJECT_ROOT`, `CORTEXSHIFT_TASK_ID`, `CORTEXSHIFT_SESSION_ID`, `CORTEXSHIFT_PROVIDER_ID`, `CORTEXSHIFT_MCP_READ_ONLY`). Mutations affect only the task assigned at launch, isolating state against active-task changes in SQLite.
4. **Explicit Binding Delivery**: The provider CLI, not CortexShift, spawns the MCP server, and each provider decides how much of its own environment that server inherits. CortexShift therefore declares the binding inside the per-launch MCP configuration it generates rather than relying on inheritance. The binding is minted once, in `ProviderSessionLauncher`, from the persisted `Session`; provider and model input never contribute to it.
5. **Dual Capability Exposure**: Managed writable sessions receive all 10 tools (4 read, 6 write). Unmanaged or read-only sessions receive only 4 read tools; write tools are not registered on the server.
6. **Workspace Lease Bypass**: MCP operations deliberately bypass the OS advisory workspace lease (`agent.lock`), as the lease is already held by the parent agent process. SQLite WAL mode provides multi-process safe atomic concurrency.
7. **Thread Safety**: FastMCP dispatches sync tool and resource callbacks across worker threads (`anyio.to_thread.run_sync`). `SQLiteStateStore` handles multi-threaded operations safely with `check_same_thread=False`.

### Provider Integration Transports

- **Claude Code**: Integrated via `--mcp-config <inline JSON>` on session launch. A managed launch adds the binding as the server's `env` object.
- **OpenAI Codex**: Integrated via `-c mcp_servers.cortexshift...` inline CLI configuration arguments. A managed launch adds `-c mcp_servers.cortexshift.env.<VAR>=...` overrides, because Codex starts MCP servers with a sanitized environment plus the server's declared `env` table.
- **Google Antigravity**: Configured in workspace via `.agents/mcp_config.json`, supported by `cortexshift mcp setup antigravity`. A workspace file is written once and is not per-launch, so it carries no session binding; Antigravity MCP therefore keeps whatever execution mode its own environment resolves to.

See [ADR-0009](decisions/ADR-0009-mcp-shared-state.md) for detailed decisions.

---

## 15. Interactive Terminal Control Center (Phase 9)

Phase 9 adds CortexShift's first persistent human-facing interface: a keyboard-driven Textual dashboard over the state CortexShift already owns. It is a control center, not a new product surface — no new backend, no second persistence system, no replacement for the CLI, and not a terminal emulator for the coding agents.

The dashboard is a third adapter beside the CLI and the MCP server. All three call the same application services:

```text
                 ┌──── CLI (Typer / Rich)
                 │
Application ◄────┼──── MCP (local stdio server)
Services         │
                 └──── TUI / Textual
                         │
                         ▼
                   presentation state
```

```text
cortexshift tui
 │
 ▼
TuiCoordinator (locates the project, requires a TTY)
 │
 ▼
CortexShiftApp (Textual)
 │
 ▼
TuiFacade  ──▶ read models (TuiStateSnapshot, TuiTaskModel, TuiRepositoryModel, …)
 │
 ├── ProjectStatusService      project identity, active task summary
 ├── TaskWorkspaceService      canonical Task reads and progress mutations
 ├── SessionService            CortexShift orchestration history
 ├── CheckpointService         checkpoint history and MANUAL capture
 ├── HandoffService            canonical handoff history
 ├── RepositoryService         live, strictly read-only Git inspection
 ├── RecoveryService           crash-recovery preview and reconciliation
 ├── SwitchService             handoff preview and switch dry run
 └── DoctorService             passive provider discovery
 │
 ▼
Domain / Ports ──▶ Adapters / SQLite / Git
```

### Sections

`Overview` (default), `Task`, `Repository`, `Sessions`, `Checkpoints`, `Handoffs`, `Providers`, plus a `Help` modal. Number keys `1`–`7` jump directly to a section; `r` refreshes; `c` captures a checkpoint; `w`/`m`/`n`/`i` drive task progress; `x` opens the provider action palette; `p` previews a handoff; `g` configures workspace MCP for Antigravity; `shift+R` runs crash recovery; `?` opens help; `q` quits. Every action is reachable without a mouse, and Textual's built-in command palette (`ctrl+p`) exposes the same actions semantically.

### Refresh model

- **Persisted state** refreshes on a lightweight ~2 second timer. It is SQLite-only: no Git, no provider probe, no subprocess. Its purpose is that an agent self-reporting through MCP appears in an open dashboard with no operator action.
- **Live Git** runs on startup, on entry to the Repository screen, and on explicit refresh only. `git status` is never polled on a timer.
- **Provider discovery** runs on startup and on explicit refresh, with a short in-memory cache for the life of the process. Nothing is persisted.
- All slow work runs in Textual thread Workers. Refresh races are resolved by exclusive worker groups plus a generation token checked on the UI thread, so a stale result can never overwrite newer data.

### Provider launch: the terminal is released first

CortexShift never embeds, wraps, scrapes, multiplexes, or emulates a provider's terminal UI. Choosing run, resume, or switch exits the Textual application with a structured `TuiExitRequest`; only after `App.run()` has returned does the coordinator invoke the provider service.

```text
Textual App
   │
   │ returns TuiExitRequest
   ▼
terminal restored
   │
   ▼
Run / Resume / Switch Service
   │
   ▼
Native provider TUI (owns the terminal)
```

**Why native provider TUIs are not embedded.** Relaying a provider's terminal through a pseudo-terminal would contradict the native-agent-first and direct-passthrough invariants established in Phase 4, and it degrades precisely what makes those agents usable: full-screen redraws, mouse handling, bracketed paste, resize propagation, and colour fidelity. It would also place CortexShift in the position of observing agent conversations, which the zero-transcript invariant forbids. Releasing the terminal is simpler, more faithful to the agents, and more honest about what CortexShift is: an orchestrator, not a multiplexer. Consequently no PTY or terminal-emulator dependency exists (`pexpect`, `ptyprocess`, `pyte`, tmux wrappers), and a regression test asserts none is introduced.

### Control-center invariants

- **The dashboard holds no workspace lease.** An open dashboard must never block a coding agent. Operations requiring exclusivity (run, resume, switch, recover) acquire the lease inside their own services, which reject unsafe attempts. Reading and cooperative task/checkpoint editing keep working while another agent owns the workspace.
- **Workspace activity is probed, never inferred.** A non-blocking acquire-and-release of the OS advisory lock is authoritative; the presence of `.cortexshift/agent.lock` on disk proves nothing, and users are never told to delete it.
- **Data authority is labelled honestly**: repository inspection is *Live*; checkpoints and handoffs are *Historical observation*; reported test summaries are *Reported / unverified*; an unfinalized session row is *Last-known*, never asserted as crashed. Progress is derived only from structured task items; with no denominator the dashboard says "No structured progress yet" rather than inventing a percentage.
- **Read-only with respect to the repository and source.** No Git mutation, no full diffs, no source editor, no shell panel, no transcripts.
- **No new persistence.** SQLite remains at schema **v6**; all dashboard state is ephemeral.
- **Local terminal only.** No Textual Web, no browser serving, no localhost listener, no telemetry.

### Shared canonical rules

"Mark completed" — append to `completed` (deduplicated), drop any exactly matching `remaining` entry, and never complete the Task itself — lives once, as `Task.complete_items()` in the domain. Both the MCP write path and the dashboard call it, so an agent and an operator cannot drift apart. `TaskWorkspaceService` gives path-addressed callers Task operations without opening a store themselves, keeping persistence out of the TUI entirely.

See [ADR-0010](decisions/ADR-0010-terminal-control-center.md) for the framework choice, alternatives considered, and detailed trade-offs.

---

## 16. What is Explicitly Out of Scope for Initial Phases

To maintain strict engineering focus, the following are explicitly out of scope for Phase 0 through Phase 9:
- Parallel multi-agent editing
- Cloud sync, hosted dashboards, or team sharing
- Direct LLM API calling or prompt engineering inside the core
- Raw conversation transcript transplantation
- Vector databases, semantic search, or RAG frameworks
- Electron or GUI applications (CLI/TUI first)
- Non-Git version control systems
- Network-based MCP transports (HTTP/SSE/WebSockets)
- Embedded provider terminal UIs, PTY relaying, or terminal multiplexing
- Browser-served or remote terminal interfaces (Textual Web and equivalents)
- Git mutation, source editing, or an embedded shell inside CortexShift



## Release distribution and compatibility

CLI / MCP / TUI call application services over domain/ports, implemented by
SQLite, read-only Git, and native provider adapters. The source and installed
package use the same architecture, project-local state, and exclusive workspace
lease for native runs. The initial public alpha is distributed as a Python
wheel/sdist, primarily through pipx. Schema v6 and Handoff/Checkpoint Protocol v1
remain unchanged. Published migrations and protocol meanings are immutable.
See [ADR-0011](decisions/ADR-0011-release-and-distribution.md).

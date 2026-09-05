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
  CLI Layer (Typer / Rich)
         │
         ▼
  Application Layer (Use-Cases / Workflows)
         │
         ▼
  Ports Layer (Abstract Protocols)
   ├── ProviderAdapter
   ├── RepositoryInspector
   └── StateStore
         ▲
         │ (implements)
  Adapters Layer (Concrete Implementations)
   ├── providers/ (Claude, Codex, Antigravity)
   ├── persistence/ (SQLite, FileSystem)
   └── repository/ (Git CLI / pygit)

  ──────────────────────────────────────────
  Domain Layer (Pure Entities & Value Objects)
   (Project, Task, Session, Checkpoint, Handoff, GitSnapshot, ProviderCapabilities)
   * Domain has NO dependencies on outer layers *
```

### Dependency Rules:
- `domain` depends only on Python standard libraries and Pydantic v2. It never imports `cli`, `application`, or `adapters`.
- `ports` define interfaces using domain types.
- `application` coordinates workflows using `domain` and `ports`.
- `adapters` implement `ports` and may use third-party tools or external CLIs.
- `cli` invokes `application` services and formats output using Rich.

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

## 10. Checkpoints & Resilient Recovery

AI coding sessions terminate abruptly due to rate limits, context exhaustion, network timeouts, or user interruptions. Waiting for an agent to generate an exit handoff is unreliable.

CortexShift's checkpointing model guarantees recovery:
- **Canonical Structure**: Every checkpoint captures `DONE`, `CURRENT`, `NEXT`, `DECISIONS`, `ISSUES`, `FILES`, and `TESTS`.
- **Unexpected Exit Recovery**: When an agent exits prematurely without generating a final handoff, CortexShift synthesizes an emergency handoff package from the latest valid checkpoint and the current Git status.

---

## 11. What is Explicitly Out of Scope for Initial Phases

To maintain strict engineering focus, the following are explicitly out of scope for Phase 0 and initial milestones:
- Parallel multi-agent editing
- Cloud sync, hosted dashboards, or team sharing
- Direct LLM API calling or prompt engineering inside the core
- Raw conversation transcript transplantation
- Vector databases, semantic search, or RAG frameworks
- Electron or GUI applications (CLI/TUI first)
- Non-Git version control systems

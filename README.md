# CortexShift

> **Switch agents. Keep the context.**

[![CI](https://github.com/cortexshift/cortexshift/actions/workflows/ci.yml/badge.svg)](https://github.com/cortexshift/cortexshift/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python: 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Status: Pre-Alpha](https://img.shields.io/badge/status-pre--alpha-orange.svg)](#status)

CortexShift is a provider-agnostic local developer tool that allows a software-development task to move between coding agents (such as **Claude Code**, **OpenAI Codex**, **Google Antigravity**, and future agents) without losing meaningful development context.

---

## The Problem

A developer starts a complex feature implementation using Claude Code. Claude inspects the repository, makes architectural decisions, edits several files, discovers a failing test, and fixes it. Then, Claude hits a rate limit or hourly quota ceiling.

Today, the developer must manually repeat everything to Codex or Antigravity: the original requirements, architectural decisions, files touched, remaining items, and known issues.

CortexShift solves this problem by establishing a persistent **Task** abstraction that outlives any ephemeral agent session:

```text
USER
  │
  ▼
PROJECT
  │
  ▼
TASK (Canonical State)
  │
  ├───────────────┬───────────────┐
  ▼               ▼               ▼
Claude Code      Codex        Antigravity
  │               │               │
  └───────────────┴───────────────┘
                  │
          SAME LOCAL REPO
```

---

## Status: Pre-Alpha

> [!NOTE]
> CortexShift is currently in **Phase 5 (Canonical Manual Handoff & Agent Switching)**. A single Task can now move between Claude Code, Codex, and Antigravity through `cortexshift switch`, carrying deterministic canonical context. Crucially, the outgoing agent does **not** need to still be available: handoffs are derived from durable local state and live Git, never from an outgoing model call.

### What Works Today (Phase 5)

```text
✓ native provider discovery
✓ persistent Task context
✓ live Git context
✓ historical Git snapshots
✓ native provider launch
✓ CortexShift Session history
✓ deterministic canonical handoff generation
✓ Claude → Codex switching
✓ Codex → Claude switching
✓ Antigravity handoff bootstrap + native conversation resume
✓ persisted handoff history
```

- **Canonical Handoff & Agent Switching (Phase 5)**:
  - Move the active task to another coding agent with `cortexshift switch claude|codex|antigravity`.
  - **The outgoing agent is never required.** Handoffs are built deterministically from the canonical Project, canonical Task, previous CortexShift Session metadata, live Git inspection, and a Git snapshot captured at switch time. CortexShift makes **no outgoing model call** and never even resolves the outgoing provider's executable — so switching works after Claude's quota is exhausted, its process is gone, or its CLI is uninstalled.
  - CortexShift Handoff Protocol v1: a provider-neutral canonical payload (project, objective, requirements, constraints, completed, current work, remaining, decisions, files touched, test status, known issues, Git state, do-not-redo, recommended next action) plus orchestration metadata, persisted in SQLite schema v4.
  - Every receiving agent is given the CortexShift authority order (repository files → live Git → verified test results → canonical task state → historical summaries) and an explicit startup contract: read `AGENTS.md`, inspect `git status` and `git diff`, verify recorded completed work rather than trusting it, run relevant tests, and continue rather than restart.
  - Honest unknown state: absent decisions and unverified test status are stated as unknown, never invented. A provider exiting with code 0 is never read as "tests pass".
  - Deterministic bounded context (48,000 characters) with reported truncation counts; the persisted canonical payload is never truncated and stays retrievable via `cortexshift handoff show ID --json`.
  - Live Git captured under the exclusive workspace lease, held continuously from repository observation through receiving-provider runtime. Non-Git projects hand off with an explicit marker; an unexpected Git probe error fails the switch safely instead of shipping an unreliable observation.
  - Claude Code and Codex receive the context directly as their native interactive initial prompt (argument arrays, `shell=False`, one argument, no `-p` / `codex exec`, no model or permission overrides).
  - Antigravity uses a two-stage documented native flow: one **read-only plan-mode bootstrap** that ingests the context, then an interactive resume of that same conversation (`agy --conversation <id>`). Only `conversation_id` and `status` are parsed; the response is discarded. No permission bypass, no TUI keystroke automation.
  - Zero-cost inspection: `cortexshift handoff preview <target>` and `cortexshift switch <target> --dry-run` persist nothing, launch nothing, and consume no model quota — including for Antigravity, where the dry run never performs the bootstrap turn.
  - Durable handoff history: `cortexshift handoff list` and `cortexshift handoff show <id>`, with `--json` on every surface.
  - `switch` never mutates task progress: it does not mark work complete, alter the backlog, clear issues, or complete the task, even on a clean exit.
- **Native Provider Launch & Session Lifecycle (Phase 4)**:
  - Launch native coding agent interactive CLIs directly (`cortexshift run claude`, `cortexshift run codex`, `cortexshift run antigravity`).
  - Raw TTY direct passthrough without scraping, buffering, pipe-wrapping, or modifying provider TUIs.
  - Same-working-tree model enforcement: launches provider process with `cwd = project_root` even when invoked from deep nested subdirectories.
  - Task-centric prerequisite: strictly requires an active Task; never silently guesses or launches without a task.
  - Workspace leasing via OS-level exclusive advisory locks (`.cortexshift/agent.lock`) enforcing the single-mutating-agent invariant per project.
  - Clean dry-run preview mode (`cortexshift run <provider> --dry-run` and `--dry-run --json`) with automatic prompt redaction (`<prompt>`).
  - Durable session history persisted in SQLite schema v3 (`sessions` table), tracking status (`running`, `completed`, `failed`, `interrupted`), exit codes, and timestamps.
  - Zero prompt, transcript, or credential storage.
  - CLI session inspection: `cortexshift session list` and `cortexshift session show <id>` with tabular and `--json` outputs.
- **Git Context & Repository Awareness (Phase 3)**:
  - Safe, strictly read-only repository inspection using native `git` CLI subprocesses with bounded timeouts and sanitized execution environments (`GIT_TERMINAL_PROMPT=0`, `GIT_PAGER=cat`, `GIT_OPTIONAL_LOCKS=0`).
  - NUL-safe (`-z`) porcelain v1 parser handling spaces, unicode, renames, conflicts, and untracked files.
  - Automatic path scoping for monorepo setups (resolving paths relative to project root) and exclusion of `.cortexshift/` internal state.
  - Detection of branch, commit SHA, dirty state, detached HEAD, unborn (0 commits) repositories, and diff shortstat summaries.
  - Persistent repository snapshots stored in SQLite schema v2 (`git_snapshots` table) across process restarts.
  - CLI commands: `cortexshift repo status`, `cortexshift repo snapshot`, `cortexshift repo snapshots`, `cortexshift repo show <id>`, with human formatting and machine-readable `--json` flags.
  - Graceful non-blocking fallback (exit code 0) when Git is not installed or when executed in non-Git directories.
- **Persistent Project & Task State (Phase 2)**:
  - Initialize project-local state area (`.cortexshift/state.sqlite3`) via `cortexshift init`.
  - Persist canonical project identity, root path, and metadata.
  - Create and manage durable tasks via `cortexshift task start`, `list`, `show`, `update`, `activate`, `complete`.
  - Maintain exactly one active task pointer per project.
  - Inspect project status, active task, and progress counters via `cortexshift status` and `cortexshift status --json`.
  - Discover project roots automatically from nested child subdirectories without relying on Git.
  - Versioned SQLite database schema with forward-safe migrations.
  - Complete state durability across terminal exits and process restarts.
- **Native Provider Discovery (Phase 1)**:
  - Discover supported native coding-agent CLIs (`claude`, `codex`, `agy` for Antigravity).
  - Safely inspect provider versions and passive authentication status where supported.
  - Run environment health check via `cortexshift doctor` and `cortexshift doctor --json`.
  - Zero model prompt executions and zero credential inspections.
- **Foundational Architecture (Phase 0)**:
  - Core domain models (`Project`, `Task`, `Session`, `Checkpoint`, `Handoff`, `GitSnapshot`, `LaunchSpecification`).
  - Strict abstract ports (`CommandRunner`, `ProviderDiscoveryPort`, `ProviderRuntimeAdapter`, `InteractiveProcessRunner`, `WorkspaceLeaseManager`, `SessionStore`, `RepositoryInspector`, `StateStore`).
  - Multi-agent development contract ([`AGENTS.md`](AGENTS.md)).
  - Architecture specifications, ADRs ([`ADR-0001`](docs/decisions/ADR-0001-core-architecture.md), [`ADR-0002`](docs/decisions/ADR-0002-safe-provider-discovery.md), [`ADR-0003`](docs/decisions/ADR-0003-project-local-persistence.md), [`ADR-0004`](docs/decisions/ADR-0004-git-repository-context.md), [`ADR-0005`](docs/decisions/ADR-0005-native-provider-runtime.md)).
  - 100% type-checked code via strict `mypy`, formatted via `ruff`, with comprehensive unit and integration tests.

### What Does NOT Work Yet

```text
✗ general native provider session resume through CortexShift
✗ automatic outgoing-agent summaries
✗ automatic quota detection
✗ automatic switching
✗ automatic periodic checkpoints
✗ MCP shared state
✗ transcript transplantation
```

- ✗ General native provider session resume (`cortexshift session resume`, `run --resume`) is not implemented (planned for Phase 6). The Antigravity conversation ID captured during a handoff is a transport implementation detail of that provider's delivery strategy, not a general resume feature.
- ✗ Automatic outgoing-agent summaries are deliberately absent — and always will be as a *requirement*. CortexShift must work when the outgoing agent cannot answer.
- ✗ Automatic quota/rate-limit detection and automatic provider switching (explicitly out of scope; switching is manual).
- ✗ Automatic periodic checkpoints and disaster recovery (planned for Phase 7).
- ✗ Model Context Protocol (MCP) shared state (planned for Phase 8).
- ✗ Transcript transplantation between providers (deliberately never — see [ADR-0006](docs/decisions/ADR-0006-canonical-agent-handoff.md)).


---

## The Core Workflow

```bash
# 1. Record the task, then launch Claude Code on it
cortexshift task start --title "OAuth2 PKCE" \
  --objective "Refactor the auth layer to support OAuth2 PKCE" \
  --requirement "Support refresh token rotation" \
  --constraint "No new third-party dependencies"

cortexshift run claude

# Claude works, makes edits, runs tests...
# Then Claude exits, or its quota becomes unusable.

# 2. Switch to Codex. Codex receives canonical context automatically
#    and continues the SAME CortexShift Task.
cortexshift switch codex

# 3. Switch again. Antigravity receives the same task continuity
#    and continues from the current repository state.
cortexshift switch antigravity
```

The receiving agent is given:
- The original objective, requirements, and constraints
- What is recorded as completed vs. what remains, and what is in flight
- Files changed according to live Git, plus branch, HEAD, and dirty state
- Known issues and a deterministic recommended next action
- The CortexShift authority order, and an explicit instruction to verify against the repository and run relevant tests rather than trust the handoff

**Claude does not need to still be running — or even installed.** CortexShift builds the handoff from its own durable state and live Git, so a switch works precisely when the previous agent has become unusable.

Inspect any handoff without spending a single token:

```bash
cortexshift handoff preview codex        # exactly what the next agent would receive
cortexshift switch codex --dry-run       # what the switch would do, nothing performed
```

---

## Architectural Principles

1. **Repository Truth Over Agent Claims**: The repository filesystem and verified test executions outrank agent summaries. Handoffs are advisory; agents must inspect reality.
2. **Task-Centric Core**: The Task belongs to CortexShift, not to any vendor conversation session.
3. **Native-Agent-First**: We orchestrate native provider CLIs through adapters rather than re-implementing them.
4. **Provider-Agnostic**: Zero vendor-specific conditionals in core domain or application layers.
5. **Same-Working-Tree**: Agents work sequentially against the same local repository.
6. **Single Mutating Agent**: Only one coding agent actively mutates a workspace at a time.
7. **Local-First & Private**: Zero mandatory cloud services, telemetry, or external databases. CortexShift never captures or manages provider credentials.
8. **Structured Canonical State**: We pass concise, structured handoff packages rather than bloated conversational transcripts (`capture_transcripts = false`).
9. **The Outgoing Agent Is Never Required**: Handoffs are derived deterministically from durable local state and live Git. CortexShift never asks a departing agent to summarize its work, because the moment you most need to switch is the moment it can no longer answer.

---

## Getting Started (Local Development)

### Prerequisites
- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/) (recommended)

### Installation

```bash
# Clone the repository
git clone https://github.com/cortexshift/cortexshift.git
cd cortexshift

# Sync dependencies and create virtual environment
uv sync
```

### CLI Verification

```bash
# Display help
uv run cortexshift --help

# Display version
uv run cortexshift version

# Inspect installed coding agents (Phase 1)
uv run cortexshift doctor
uv run cortexshift doctor --json

# Initialize project-local state (Phase 2)
uv run cortexshift init

# Start a persistent task with objective, requirements, and constraints
uv run cortexshift task start \
  --title "Implement screen understanding" \
  --objective "Add screen-understanding support while preserving provider boundaries." \
  --requirement "Support standard image formats" \
  --constraint "No direct cloud API calls"

# Check durable project status
uv run cortexshift status
uv run cortexshift status --json

# Update task progress
uv run cortexshift task update \
  --work "Developing SQLite adapter" \
  --add-completed "Created schema migrations" \
  --add-remaining "Add integration tests"

# List all tasks
uv run cortexshift task list
uv run cortexshift task list --json

# Inspect active task details
uv run cortexshift task show
uv run cortexshift task show --json

# Mark active task completed
uv run cortexshift task complete

# Inspect live Git repository status (Phase 3)
uv run cortexshift repo status
uv run cortexshift repo status --json

# Capture and persist a repository snapshot
uv run cortexshift repo snapshot
uv run cortexshift repo snapshot --json

# List historical snapshots
uv run cortexshift repo snapshots
uv run cortexshift repo snapshots --limit 5

# Show specific snapshot details
uv run cortexshift repo show snap_<id>
uv run cortexshift repo show snap_<id> --json

# Launch native provider interactive session on active task (Phase 4)
uv run cortexshift run claude
uv run cortexshift run codex --prompt "Investigate failing tests"
uv run cortexshift run antigravity

# Preview launch specification without executing (dry run)
uv run cortexshift run claude --dry-run
uv run cortexshift run claude --prompt "Sensitive instruction" --dry-run --json

# List historical provider execution sessions
uv run cortexshift session list
uv run cortexshift session list --json
uv run cortexshift session list --limit 5

# Show specific session details
uv run cortexshift session show sess_<id>
uv run cortexshift session show sess_<id> --json

# Preview the canonical context a target agent would receive (Phase 5)
# Persists nothing, launches nothing, consumes zero model quota.
uv run cortexshift handoff preview codex
uv run cortexshift handoff preview codex --json

# Describe a switch without performing it
# For Antigravity this never runs the plan-mode bootstrap turn.
uv run cortexshift switch codex --dry-run
uv run cortexshift switch codex --dry-run --json
uv run cortexshift switch antigravity --dry-run

# Hand the active task to another coding agent and launch it with full context
uv run cortexshift switch codex
uv run cortexshift switch claude
uv run cortexshift switch antigravity

# Hand off from a specific historical session, with an optional operator note
uv run cortexshift switch codex --from-session sess_<id>
uv run cortexshift switch codex --note "Mind the flaky integration test"

# Inspect persisted handoff history
uv run cortexshift handoff list
uv run cortexshift handoff list --json
uv run cortexshift handoff show handoff_<id>
uv run cortexshift handoff show handoff_<id> --json
```

> [!NOTE]
> **Antigravity handoffs perform one read-only model turn.** Because Antigravity's native
> interactive startup does not accept a direct initial prompt, `cortexshift switch antigravity`
> first runs a single read-only plan-mode bootstrap (`agy --mode=plan …`) so the context is
> ingested, then reopens that same conversation in the native TUI. This may consume Antigravity
> usage; your explicit `switch antigravity` authorizes it, and `--dry-run` never performs it.
> CortexShift does not automate TUI keystrokes, so you may need to review the prepared
> continuation plan and continue from the resumed UI.

### Running Tests and Quality Checks

```bash
# Run pytest with test coverage
uv run pytest

# Run Ruff linter and formatter checks
uv run ruff check .
uv run ruff format --check .

# Run Mypy strict type checking
uv run mypy
```

---

## Documentation

- [Architecture Overview](docs/architecture.md)
- [Canonical Handoff Protocol](docs/handoff-protocol.md)
- [Roadmap](docs/roadmap.md)
- [ADR-0001: Core Architecture](docs/decisions/ADR-0001-core-architecture.md)
- [ADR-0002: Safe Provider Discovery](docs/decisions/ADR-0002-safe-provider-discovery.md)
- [ADR-0003: Project-Local Persistence](docs/decisions/ADR-0003-project-local-persistence.md)
- [ADR-0004: Git Repository Context](docs/decisions/ADR-0004-git-repository-context.md)
- [ADR-0005: Native Provider Launch & Session Lifecycle](docs/decisions/ADR-0005-native-provider-runtime.md)
- [ADR-0006: Canonical Agent Handoff & Manual Provider Switching](docs/decisions/ADR-0006-canonical-agent-handoff.md)
- [Agent Contributor Contract](AGENTS.md)
- [Contributing Guide](CONTRIBUTING.md)

---

## License

CortexShift is licensed under the [MIT License](LICENSE).

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
> CortexShift is currently in **Phase 2 (Persistent Project & Task State)**. It provides durable project-local task persistence and native provider discovery, but does not yet launch agents or switch tasks.

### What Works Today (Phase 2)
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
  - Core domain models (`Project`, `Task`, `Session`, `Checkpoint`, `Handoff`, `GitSnapshot`).
  - Strict abstract ports (`CommandRunner`, `ProviderDiscoveryPort`, `ProviderAdapter`, `RepositoryInspector`, `StateStore`).
  - Multi-agent development contract ([`AGENTS.md`](AGENTS.md)).
  - Architecture specifications, ADRs ([`ADR-0001`](docs/decisions/ADR-0001-core-architecture.md), [`ADR-0002`](docs/decisions/ADR-0002-safe-provider-discovery.md), [`ADR-0003`](docs/decisions/ADR-0003-project-local-persistence.md)).
  - 100% type-checked code via strict `mypy`, formatted via `ruff`, with comprehensive unit and integration tests.

### What Does NOT Work Yet
- ✗ Git context capture, diff analysis, and repository snapshots (planned for Phase 3).
- ✗ Launching coding agents in interactive or headless modes (planned for Phase 4).
- ✗ Agent switching or executing multi-agent workflows (`cortexshift switch`) (planned for Phase 5).
- ✗ Automatic context handoffs between agents (planned for Phase 5).
- ✗ Resuming native agent sessions (planned for Phase 6).
- ✗ Checkpoint automation and disaster recovery (planned for Phase 7).
- ✗ Model Context Protocol (MCP) server integration (planned for Phase 8).


---

## Conceptual Vision

Once fully implemented, the developer workflow will look like this:

```bash
# 1. Start a task and launch Claude Code
cortexshift run claude "Refactor auth layer to support OAuth2 PKCE"

# Claude works, makes edits, runs tests...
# If Claude reaches a usage limit:

# 2. Switch to OpenAI Codex without losing context
cortexshift switch codex

# Codex receives the canonical handoff, inspects the repo, and continues...

# 3. Switch to Google Antigravity
cortexshift switch antigravity
```

The receiving agent instantly understands:
- The original objective, requirements, and constraints
- What was completed vs. what remains
- Important decisions made and their rationale
- Files touched and test suite status
- Things NOT to redo or rollback

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
```

### Running Tests and Quality Checks

```bash
# Run pytest with test coverage
uv run pytest

# Run Ruff linter and formatter checks
uv run ruff check .
uv run ruff format --check .

# Run Mypy strict type checking
uv run mypy src
```

---

## Documentation

- [Architecture Overview](docs/architecture.md)
- [Canonical Handoff Protocol](docs/handoff-protocol.md)
- [Roadmap](docs/roadmap.md)
- [ADR-0001: Core Architecture](docs/decisions/ADR-0001-core-architecture.md)
- [Agent Contributor Contract](AGENTS.md)
- [Contributing Guide](CONTRIBUTING.md)

---

## License

CortexShift is licensed under the [MIT License](LICENSE).

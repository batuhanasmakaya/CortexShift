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

> [!WARNING]
> CortexShift is currently in **Phase 0 (Foundation & Architecture)**. It is in active early development and not yet ready for production use.

### What Works Today (Phase 0)
- Core domain model definitions (`Project`, `Task`, `Session`, `Checkpoint`, `Handoff`, `GitSnapshot`, `ProviderCapabilities`).
- Pure abstract ports (`ProviderAdapter`, `RepositoryInspector`, `StateStore`).
- Initial CLI skeleton (`cortexshift --help`, `cortexshift version`, `python -m cortexshift --help`).
- Multi-agent development contract ([`AGENTS.md`](AGENTS.md)).
- Architecture specifications and canonical handoff protocol ([`docs/`](docs/)).
- 100% type-checked code via strict `mypy`, formatted via `ruff`, with comprehensive unit and integration tests.

### What Does NOT Work Yet
- Native CLI launching or switching between Claude, Codex, or Antigravity (planned for Phases 4 & 5).
- Provider discovery or `cortexshift doctor` (planned for Phase 1).
- Local SQLite persistence and task management commands (`cortexshift init`, `cortexshift task`, `cortexshift status`) (planned for Phase 2).
- Automatic Git state inspection subprocesses (planned for Phase 3).
- Model Context Protocol (MCP) server (planned for Phase 8).

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

# Run via python module
uv run python -m cortexshift --help
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

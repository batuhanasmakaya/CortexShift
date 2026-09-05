# CortexShift Product Roadmap

This document outlines the phased development roadmap for **CortexShift**.

Each phase builds systematically upon the previous phase without premature complexity. Architectural boundaries established in Phase 0 ensure that later phases can be added without rewriting the core domain.

---

## Phases Overview

```text
Phase 0  ──▶  Phase 1  ──▶  Phase 2  ──▶  Phase 3  ──▶  Phase 4  ──▶  Phase 5
Foundation    Provider      Task State    Git Context   Native        Manual
& Arch        Discovery     & SQLite                    Launch        Handoff
(Complete)    (Complete)    (Complete)    (Complete)    (Next)

Phase 6  ──▶  Phase 7  ──▶  Phase 8  ──▶  Phase 9  ──▶  Phase 10 ──▶  Future
Native        Checkpoints   MCP Server    TUI           Public        Experimental
Resume        & Recovery                                Release
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

## Phase 4 — Native Provider Launch

Launch external coding agents through non-invasive adapters.

- **Goals**:
  - Implement concrete adapters for Claude Code, Codex, and Antigravity.
  - Orchestrate interactive terminal sessions and headless runs.
  - Pass initial workspace context to launched CLIs.

---

## Phase 5 — Manual Handoff

Complete the first end-to-end multi-agent workflow.

- **Goals**:
  - Switch from Claude Code to OpenAI Codex or Google Antigravity seamlessly:
    ```bash
    cortexshift switch codex
    ```
  - Compile the canonical handoff package into a structured injection prompt.
  - Verify that the incoming agent can inspect the repo and continue the task without context degradation.

---

## Phase 6 — Native Session Persistence

Track native session identifiers to allow returning to prior sessions when supported.

- **Goals**:
  - Capture provider session/thread IDs.
  - Resume existing native sessions when supported by the provider CLI.

---

## Phase 7 — Checkpoints & Resilient Recovery

Automate checkpointing during active sessions to safeguard against abrupt session termination.

- **Goals**:
  - Background checkpoint synthesis.
  - Disaster recovery command to restore context after quota or process crashes.

---

## Phase 8 — MCP Integration

Expose CortexShift capabilities and state to agents via Model Context Protocol.

- **Goals**:
  - Lightweight local MCP server.
  - Tools for active agents to query task requirements, log decisions, and update task status directly.

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

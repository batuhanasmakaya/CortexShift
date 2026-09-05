# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

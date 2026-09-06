# Contributing to CortexShift

Thank you for your interest in contributing to **CortexShift**! We are building a local, provider-agnostic developer tool that lets software development tasks transition smoothly between coding agents without context loss.

Whether you are a human developer or an AI coding agent, please review these guidelines.

---

## Development Environment Setup

CortexShift targets **Python 3.12+** and uses **[`uv`](https://docs.astral.sh/uv/)** as its primary package and project manager.

### 1. Obtain the source

Work from a local checkout or extracted source distribution. A canonical public
repository URL has not yet been configured; no clone URL is assumed.

### 2. Install Dependencies

Using `uv`:

```bash
uv sync --locked
```

This creates a local `.venv` and installs all runtime and development dependencies.

---

## Quality & Verification Commands

We maintain strict standards for code quality, typing, and testing. Run the following checks before opening a pull request or completing an agent task:

### 1. Code Formatting & Linting

```bash
uv run ruff check .
uv run ruff format --check .
```

To automatically fix safe issues:

```bash
uv run ruff check --fix .
uv run ruff format .
```

### 2. Type Checking

Mypy is configured in strict mode:

```bash
uv run mypy
```

### 3. Automated Tests & Coverage

```bash
uv run pytest
```

### 4. CLI Smoke Testing

```bash
uv run cortexshift --help
uv run cortexshift version
uv run python -m cortexshift --help
```

---

## Architectural Principles & Boundaries

All contributors must respect the core architectural layers:

```text
src/cortexshift/
├── domain/       # Pure business entities & value objects (zero external frameworks)
├── ports/        # Protocols & abstract interfaces defining contracts
├── application/  # Orchestration, use-cases, and business workflows
├── adapters/     # Implementations of ports (CLI wrappers, persistence, etc.)
└── cli/          # Typer/Rich command-line interface entrypoints
```

### Critical Guidelines

1. **Dependency Inversion**: Dependencies point inward:
   - `domain` must never import from `adapters`, `cli`, or third-party infrastructure frameworks.
   - `ports` define interfaces using domain types.
   - `adapters` implement `ports`.
2. **Provider Isolation**: Provider-specific logic (for Claude Code, Codex, Antigravity, etc.) must reside exclusively in `src/cortexshift/adapters/providers/`. Never introduce provider conditionals in `domain` or `application`.
3. **No Credential Storage**: CortexShift never stores, reads, or manages provider API keys. Native CLIs handle their own auth.
4. **Minimal Runtime Dependencies**: Do not introduce heavy AI frameworks (LangChain, LlamaIndex), vector databases, or ORMs.
5. **Truth Hierarchy**: Real repository files, Git state, and verified test executions outrank agent summaries.
6. **Architectural Decision Records (ADRs)**: Any architectural change, new invariant, or cross-cutting dependency addition requires an ADR in `docs/decisions/`.

---

## Commit & PR Guidelines

- Keep pull requests focused on a single phase or feature.
- Include unit tests covering all new domain behavior, ports, and CLI commands.
- Ensure all CI checks pass on GitHub Actions.

## Provider adapters and persistence

Implement discovery, runtime, handoff, and optional native-session ports in
`adapters/providers`; register adapters at the composition boundary. Add bounded
version/help checks and deterministic fakes, never CI model calls. Do not add
provider conditionals to domain/application code. CLI, MCP, and TUI reuse services.

Persistence and SQL belong in adapters. Published migrations v1–v6 must remain
unchanged; add transactional forward migrations with rollback and preservation
tests. Published handoff/checkpoint meanings require explicit protocol versioning.
Never persist credentials, prompts, provider responses, transcripts, or full patches.

## Release checks

Run `uv run python scripts/release_check.py`, `uv build`, and
`uv run python scripts/artifact_smoke.py --dist dist`. The latter creates disposable
install environments and uses package-index access for dependencies. It never calls
models or publishes. See [Releasing](docs/releasing.md) for the complete sequence.
Pre-1.0 internal Python imports are not a stable public API. Changes to commands,
persisted state, and MCP contracts need explicit compatibility review.

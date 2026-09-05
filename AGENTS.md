# AGENTS.md — CortexShift Contributor Contract for Coding Agents

Welcome to **CortexShift**. This repository is built to orchestrate and transition work across multiple coding agents (Claude Code, OpenAI Codex, Google Antigravity, and future agents). Because CortexShift itself will be developed, maintained, and refactored by different AI coding agents, all participating agents MUST follow this engineering contract.

---

## 1. Prime Directive: Truth Hierarchy

Never treat an agent summary, commit message, or handoff note as infallible truth. Adhere strictly to the truth hierarchy:

1. **Actual repository files** (the filesystem is primary reality)
2. **Git state** (branch, commits, status, diffs)
3. **Verified command/test results** (executed by you in this session)
4. **CortexShift canonical task state** (persisted task records)
5. **Previous agent summaries/handoffs** (advisory only)

A previous agent statement claiming "all tests pass" must NEVER override your own test execution revealing failures. Always verify.

---

## 2. Architectural Invariants

Every agent working on CortexShift must preserve the following architectural invariants:

1. **Task-Centric, Not Conversation-Centric**: Tasks belong to CortexShift, not to any provider session. Providers are ephemeral workers on a persistent task.
2. **Native-Agent-First**: Never re-implement provider CLIs. CortexShift orchestrates native CLIs through adapters.
3. **Provider-Agnostic Core**: The domain and application layers must NEVER contain provider-specific conditionals (e.g. `if provider == "claude"`). All provider variance belongs strictly behind the `ProviderAdapter` port.
4. **Same-Working-Tree Model**: Agents operate sequentially in the same local repository worktree. Do not design mechanisms around copying or duplicating repositories.
5. **Single Mutating Agent**: Only one coding agent may actively mutate the workspace at a time. Multi-agent concurrent editing is strictly out of scope.
6. **Local-First & Zero Telemetry**: Core functionality must run locally without cloud dependencies, mandatory hosted backends, external databases, or telemetry.
7. **Zero Credential Storage**: CortexShift MUST NEVER capture, store, or manage provider API keys or auth tokens. Authentication is strictly delegated to each provider's native CLI.
8. **Structured Canonical State over Transcripts**: Context handoff uses distilled structured state (objective, requirements, decisions, touched files, test status). Transcript capture is disabled by default (`capture_transcripts = false`).

---

## 3. Engineering & Workflow Rules

- **Read Before Modifying**: Review [`docs/architecture.md`](docs/architecture.md) and relevant ADRs in [`docs/decisions/`](docs/decisions/) before initiating significant architectural modifications.
- **Strict Layering**: Enforce dependency flow: `domain` ← `ports` ← `application` ← `cli` / `adapters`. Domain must never import CLI, concrete adapters, or third-party infrastructure frameworks.
- **Persistence Boundaries**: Domain, application, and CLI layers must NEVER contain SQL statements or SQLite dependencies. All persistence logic belongs strictly behind ports in adapters (`SQLiteStateStore`).
- **Data Integrity & Non-Destruction**: Never automatically wipe, reset, or silently overwrite user database state. Migrations must be forward-safe, deterministic, and transactional. If state is incompatible, fail safely.
- **Private State Area**: `.cortexshift/` is local private runtime state. It must never store provider credentials, auth tokens, or conversational transcripts.
- **Minimal Dependencies**: Do not introduce heavy frameworks (no LangChain, LlamaIndex, vector DBs, Redis, ORMs, or cloud SDKs). Rely on Python stdlib, Pydantic, Typer, and Rich.
- **Tests Are Mandatory**: Every new domain behavior, CLI command, and adapter must include automated, deterministic unit tests. Run `pytest`, `ruff check .`, and `mypy src` before declaring work complete.
- **Scope Discipline**: Strictly keep changes scoped to the current phase and requested task. Do NOT prematurely implement future roadmap phases (e.g., do not add provider execution or database schemas until requested).
- **Document Changes**: When modifying architecture or adding cross-cutting features, record an Architectural Decision Record (ADR) in `docs/decisions/` and update relevant documentation.
- **Never Silently Alter Invariants**: Any change to foundational rules requires explicit rationale, consensus, and an updated ADR.

---

## 4. Key Documentation Links

- [Architecture Overview](docs/architecture.md)
- [Canonical Handoff Protocol](docs/handoff-protocol.md)
- [Project Roadmap](docs/roadmap.md)
- [Architecture Decisions (ADRs)](docs/decisions/ADR-0001-core-architecture.md)

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
9. **Git Snapshot Authority vs. Reality**: Stored `GitSnapshot` records are immutable historical observations of what was true at capture time. They are never proof of current working tree reality once subsequent changes occur. Live inspection via `git status` outranks any stored snapshot.
10. **Strictly Read-Only Git Execution**: CortexShift's repository inspection must never mutate the repository's version control state. All repository inspection commands (`git status`, `git branch`, `git rev-parse`, `git diff --shortstat`) must be strictly read-only (no `git add`, `git commit`, `git checkout`, `git reset`, `git clean`, etc.).
11. **Direct Terminal Passthrough & Zero Prompt Storage**: Native provider interactive sessions directly inherit terminal stdio without pipe-wrapping, buffering, or TUI scraping. CortexShift must never persist user prompts, conversational transcripts, or provider auth tokens.
12. **Mandatory Workspace Lease**: All mutating agent operations strictly require an exclusive project workspace lease (`FileWorkspaceLease` via OS advisory locking on `.cortexshift/agent.lock`) to enforce single-mutating-agent execution. The OS advisory lock (`fcntl.flock` / `msvcrt.locking`) on the open file descriptor is authoritative; the mere existence of the lock file on disk does not imply an active lease, and users must never be instructed to delete the lock file. Stale database session records never impede acquiring a free OS lock.
13. **Handoff Generation Must Not Require the Outgoing Provider**: Canonical handoffs are derived deterministically from durable local state (canonical Project, canonical Task, previous Session metadata, live Git inspection, persisted Git snapshot). CortexShift must NEVER require the outgoing agent to summarize its work, launch, or even be installed — the moment a user most needs to switch is the moment the previous agent can no longer answer. No outgoing model call may ever become a mandatory handoff step.
14. **Historical Handoff State Never Substitutes for Live Repository Truth**: A stored `HandoffRecord` is an immutable observation of what was true at capture time. Live repository files, live Git state, and verified test results always outrank it. Never present recorded completed items as proven, and never present a stored Git snapshot as current truth.
15. **Unknown State Stays Explicitly Unknown**: Where CortexShift has no verified record (currently `IMPORTANT DECISIONS` and `TEST STATUS`), encode the absence honestly. Never infer that tests pass because a provider process exited 0.
16. **Provider-Specific Handoff Delivery Belongs Behind Adapters**: All transport variance lives behind `ProviderHandoffAdapter`. Orchestration services must never branch on provider identity.
17. **Never Persist Rendered Prompts or Provider Responses**: Canonical structured state is persisted; rendered provider prompts, provider bootstrap responses, transcripts, and hidden reasoning are not. Rendered packages are transport representations, re-derived deterministically from canonical state.

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
  - [ADR-0001: Core Architecture](docs/decisions/ADR-0001-core-architecture.md)
  - [ADR-0002: Safe Provider Discovery](docs/decisions/ADR-0002-safe-provider-discovery.md)
  - [ADR-0003: Project-Local Persistence](docs/decisions/ADR-0003-project-local-persistence.md)
  - [ADR-0004: Git Repository Context](docs/decisions/ADR-0004-git-repository-context.md)
  - [ADR-0005: Native Provider Launch & Session Lifecycle](docs/decisions/ADR-0005-native-provider-runtime.md)
  - [ADR-0006: Canonical Agent Handoff & Manual Provider Switching](docs/decisions/ADR-0006-canonical-agent-handoff.md)

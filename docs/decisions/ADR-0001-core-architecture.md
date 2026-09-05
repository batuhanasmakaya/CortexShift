# ADR-0001: Core Architecture and Foundational Invariants

- **Status**: Accepted
- **Date**: 2026-09-05
- **Deciders**: CortexShift Core Architecture Team

---

## Context

Software developers are increasingly utilizing AI coding agents such as Claude Code, OpenAI Codex, and Google Antigravity to build software. However, each coding agent maintains an isolated conversational context. When a developer encounters usage limits, quota exhaustion, or agent-specific blind spots, switching to a different agent forces the developer to manually restate the project context, explain what was already completed, re-run tests, and clarify architectural constraints.

Previous attempts at multi-agent coordination often attempt to transplant raw conversation transcripts or build custom LLM wrapper agents. We need an architecture that solves this problem reliably across disparate coding agents.

---

## Decisions

We have made the following fundamental architectural decisions for CortexShift:

### 1. Task-Centric Rather Than Conversation-Centric
The primary unit of persistence and state in CortexShift is the **Task**, not an AI chat conversation. Coding agents are ephemeral workers assigned to a task. Sessions and conversation threads may expire, but the task and its canonical state endure.

### 2. Native-Agent-First via Provider Adapters
CortexShift will not build its own coding agent from scratch, nor will it wrap LLM inference APIs directly. It orchestrates the official native CLIs provided by vendors (e.g. `claude`, `codex`, `antigravity`). All vendor-specific integration logic resides behind a `ProviderAdapter` port.

### 3. Provider-Agnostic Core
The domain and application layers must contain no provider-specific logic, hardcoded strings, or conditional branching (e.g., `if provider == "claude"`). The provider identifier is extensible, allowing new agents to be supported without modifying the core.

### 4. Local-First & Zero Credential Storage
CortexShift operates entirely on the developer's local machine. It requires no cloud backend, external databases, or remote telemetry. CortexShift never reads, stores, or manages provider API keys; each provider's native CLI manages its own authentication.

### 5. Structured Canonical State Over Transcript Transplantation
Instead of dumping voluminous, vendor-specific conversation transcripts between models (which wastes token budgets and causes attention degradation), CortexShift distills actionable development memory into a structured canonical handoff (objectives, requirements, constraints, decisions, touched files, test status).

### 6. Repository, Git, and Test Truth Outranks Agent Summaries
A strict truth hierarchy is established:
1. Actual repository files
2. Git state
3. Verified command/test results
4. Canonical task state
5. Previous agent summaries/handoffs
Handoffs are explicitly advisory; incoming agents are instructed to verify claims against the filesystem and test suite before proceeding.

### 7. Sequential Single-Mutating-Agent Initial Model
To eliminate file concurrency bugs, conflicting writes, and Git merge conflicts, only one agent may mutate the workspace at any time. Multi-agent concurrent editing is explicitly out of scope for early versions.

### 8. Python 3.12+ for Orchestration Core
Python 3.12+ was selected for its rich CLI ecosystem (Typer, Rich), powerful validation framework (Pydantic v2), standard library SQLite support, cross-platform portability, and rapid iteration capabilities, managed via modern `uv`.

---

## Consequences

### Positive
- High resilience: unexpected agent crashes do not destroy task progress.
- Clean separation: adding a new coding agent requires only writing a new adapter implementing `ProviderAdapter`.
- Security & privacy: zero sensitive credentials touch CortexShift storage; transcripts are not captured by default.
- Minimal token consumption: structured handoffs consume far fewer tokens than multi-megabyte chat logs.
- Reliability: empirical verification prevents compounding hallucinations across agent handoffs.

### Negative / Trade-offs
- Subtle agent reasoning nuances not captured in the structured checkpoint may be lost compared to full conversational replay.
- Initial single-agent sequential execution precludes parallel swarm workflows (a deliberate trade-off for stability).

---

## Alternatives Considered

1. **Transcript Transplantation (Prompt Injection of Raw History)**:
   *Rejected*. Different providers use distinct conversational schemas, tool formats, and token limits. Feeding 100,000 tokens of Claude conversation into Codex is prohibitively expensive, unreliable, and causes severe context degradation.
2. **Custom Multi-Agent Framework (LangChain / AutoGen / CrewAI)**:
   *Rejected*. These frameworks attempt to manage LLM API calls directly rather than leveraging official, state-of-the-art developer CLIs like Claude Code or Antigravity with their native terminal workflows, tool sandboxing, and MCP ecosystems.
3. **Rust / Go for Core Implementation**:
   *Considered*. While Rust or Go provide single-binary distribution benefits, Python 3.12+ with `uv` provides the fastest velocity, mature terminal tooling (Typer/Rich/Textual), robust typing, and the best ecosystem alignment for developer tools in this space. Standalone binary packaging can be added later via PyInstaller or uv.

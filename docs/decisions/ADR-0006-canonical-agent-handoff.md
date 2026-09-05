# ADR-0006: Canonical Agent Handoff & Manual Provider Switching

- **Status**: Accepted
- **Date**: 2026-09-05
- **Deciders**: CortexShift Core Architecture Team

---

## Context

Phases 0 through 4 established CortexShift's architecture, passive provider discovery, durable project and task state, read-only Git awareness, and native provider launch with session lifecycle tracking. Phase 5 delivers the product's central promise:

```text
One Task.
Multiple coding agents.
No need to manually re-explain the work.
```

The real user problem is specific and unforgiving:

```text
Claude works
↓
Claude quota is exhausted
↓
Claude may no longer be able to answer
↓
user wants to switch to Codex
```

Any design where the handoff is produced *by the outgoing agent* fails exactly when the user needs it most. If CortexShift asked Claude to summarize its work before switching, then a quota-exhausted, crashed, or uninstalled Claude would make the handoff impossible. The whole feature would be unavailable precisely in its motivating scenario.

At the same time, the three supported providers do not accept context the same way. Claude Code and Codex expose a documented interactive initial prompt; Antigravity's documented interactive startup does not offer the same direct positional path. A naïve solution would either scatter provider conditionals through the core or resort to brittle terminal automation.

---

## Decisions

### 1. Structured Canonical State, Not Transcript Transplantation

CortexShift does not copy, convert, parse, or replay provider conversations. It never reads Claude's hidden history, Codex's history database, or Antigravity's conversation storage; it never scrapes a provider TUI, and it never persists terminal transcripts or hidden reasoning.

```text
provider-specific conversation
        ↓
    not canonical

CortexShift structured state
        +
   live repository
        ↓
 canonical continuity
```

Structured state is portable across vendors, stable across model and CLI versions, dramatically smaller than a transcript, and free of the privacy hazards that raw conversation capture carries.

### 2. The Outgoing Provider Is Never Required

Handoff generation is deterministic from durable, local state only:

- the canonical Project,
- the canonical Task,
- previous CortexShift Session metadata,
- live repository/Git inspection,
- the Git snapshot persisted at handoff time.

No outgoing model call is a mandatory (or even optional) step of a handoff. `SwitchService` never resolves the *source* provider's executable, so a Claude installation that is gone entirely does not impede a switch to Codex. This is covered by dedicated regression tests at both the service and end-to-end level.

Future checkpoint and MCP phases may enrich handoffs *while* an agent is still active. Phase 5 must — and does — work after the outgoing provider is already gone.

### 3. Deterministic Handoff Builder — No LLM Inside CortexShift

`HandoffBuilder` is pure: no I/O, no subprocess, no model. `RECOMMENDED NEXT ACTION` is derived by a fixed priority (current work → first remaining item → inspect the repository). `FILES TOUCHED` is the deduplicated union of staged, modified, untracked, and conflicted paths from live Git inspection, in deterministic order; source files are never opened to populate it. CortexShift adds no summarizer, no embeddings, no vector store, and no AI SDK.

### 4. Honest Unknown State

CortexShift has no durable record of architectural decisions or verified test results yet, so Phase 5 states that plainly rather than inventing it:

```text
IMPORTANT DECISIONS
No structured decisions are recorded in CortexShift state.

TEST STATUS
No verified test result is recorded in CortexShift state.
The receiving agent must run relevant tests before relying on previous claims.
```

Critically, a provider process exiting with code 0 is **not** evidence that project tests passed, and is never rendered as such.

### 5. Completed Work Is Advisory, Never a Licence to Skip Verification

Task `completed` items inform both `COMPLETED` and `DO NOT REDO`, phrased conservatively: these items are *recorded as completed in CortexShift canonical state*, and the receiving agent must verify them against the repository before depending on them. The receiving agent is never told to skip verification, and is never told to restart the task either.

### 6. Live Git Capture Under the Workspace Lease

An actual switch observes the repository as late as possible before the receiving provider starts, with the exclusive workspace lease held continuously across the whole window:

```text
acquire exclusive workspace lease
↓ live Git inspection
↓ persist Git snapshot (only when Git is ready)
↓ build canonical handoff
↓ persist handoff
↓ deliver context and launch the receiving provider
↓ (lease released only after the receiving session ends)
```

The lease is never released between snapshot capture and target launch, so no other CortexShift agent can invalidate the observation in between.

Git remains optional. `git_not_installed` and `not_git_repository` continue the handoff with an explicit canonical marker and no snapshot row — fake `GitSnapshot` rows are never created. An unexpected `probe_error` fails the switch safely rather than shipping a context package whose repository observation may be unreliable.

### 7. Handoff Protocol Version, Independent of Schema Version

`HANDOFF_PROTOCOL_VERSION = 1` ("CortexShift Handoff Protocol v1") is persisted with every handoff and is deliberately **not** the SQLite schema version. Canonical field sets and prompt formatting can evolve independently of database migrations, and vice versa.

### 8. Record vs. Payload Separation

The speculative Phase 0 `Handoff` model conflated orchestration metadata with engineering context. Phase 5 refactors it rather than duplicating it:

- **`HandoffPayload`** — the canonical, provider-neutral, point-in-time engineering context.
- **`HandoffRecord`** — orchestration and delivery metadata (project, task, source/target session and provider, Git snapshot reference, status, timestamps, failure code) wrapping one payload.

### 9. Rendered Provider Prompts Are Never Persisted

Only canonical structured data is stored. Fully rendered Claude, Codex, and Antigravity prompts are transport representations, re-derived deterministically from canonical state whenever needed. This lets prompt formatting improve later without rewriting historical canonical data, and keeps a large, redundant blob out of the database.

### 10. Bounded Transport Rendering, Complete Canonical Storage

`HandoffRenderer` enforces a deterministic character budget (`MAX_RENDERED_CONTEXT_CHARS = 48_000`) using simple character and list budgets — no tokenizer, no summarizer, no model. High-priority context (objective, requirements, constraints, current work, remaining, known issues, source session summary, Git state, the truth hierarchy, and the startup contract) is never dropped. High-volume lists (large completed lists, very large changed-file lists) are truncated last, in a fixed priority order, always reporting how many items were omitted:

```text
... 73 additional completed items omitted from injected context.
```

The persisted `HandoffPayload` is never truncated. When the transport rendering was bounded, the receiving agent is told it can fetch the complete structured package locally with `cortexshift handoff show HANDOFF_ID --json`.

### 11. Provider-Specific Delivery Behind Adapters

The application core knows only the target provider, the rendered canonical context, and the project root. `ProviderHandoffAdapter` encapsulates all transport variance, so `SwitchService` contains no `if target == "antigravity":` branching.

- **Claude Code** — `[claude, <handoff-context>]`
- **Codex** — `[codex, <handoff-context>]`
- **Antigravity** — read-only plan bootstrap, then conversation resume (below)

All launches use argument arrays with `shell=False`. The entire handoff always remains exactly one subprocess argument, so hostile task text or filenames cannot escape the argv boundary. CortexShift never overrides the user's model, permission mode, sandbox, or reasoning-effort settings, never uses `claude -p` or `codex exec` for ordinary handoff delivery, and never imports a transcript.

### 12. Antigravity: Read-Only Plan Bootstrap, Then Conversation Resume

Because Antigravity's documented interactive startup provides no equivalent positional initial-prompt path, CortexShift uses two documented native capabilities in sequence:

```text
Stage 1 (headless, read-only):
  agy --mode=plan -p "<handoff-context>" --output-format json

Stage 2 (native interactive):
  agy --conversation <conversation_id>
```

The bootstrap prompt explicitly instructs the model to ingest context, remain read-only, modify no files, run no mutating commands, and produce a continuation plan, noting that the same conversation resumes immediately in the native UI. Plan mode is required here because the first turn's purpose is context ingestion and planning, never unattended mutation. `--dangerously-skip-permissions` and accept-edits behavior are never used.

Only `conversation_id` and `status` are parsed from the JSON envelope. The response body, reasoning, usage, and tool details are discarded — never persisted, never logged, never printed. On `status == SUCCESS` with a non-empty conversation ID, that ID becomes the receiving CortexShift Session's `native_session_id` and drives the interactive resume, so the user lands in the native Antigravity TUI with the handoff already loaded rather than in a second, unrelated conversation.

### 13. No TUI Keystroke Automation

CortexShift does not emulate keystrokes, use Expect-style screen automation, or scrape and replay a provider TUI. The honest consequence is that after the read-only plan bootstrap, Antigravity may require the user to approve or continue from the resumed native UI. CortexShift states this in its output rather than hiding it. Brittle terminal automation and unsafe unattended workspace mutation are both worse trade-offs.

### 14. Handoff Delivery and Session Outcome Are Distinct

`HandoffStatus` (`prepared` / `delivered` / `failed`) describes whether context reached the receiving provider. The target `Session` remains the source of truth for how the receiving coding session itself ended. A handoff can be `delivered` while its target session later ends `failed` — those are not contradictory, and the model reflects that.

Delivery failures record a safe machine classification (`target_provider_missing`, `bootstrap_failed`, `bootstrap_timeout`, `bootstrap_invalid_output`, `spawn_failed`, `workspace_locked`) and never store raw provider stderr or auth metadata.

### 15. Switch Never Mutates Task Progress

`switch` does not mark current work completed, alter the remaining list, clear known issues, or complete the task — not even when the receiving provider exits 0. CortexShift does not yet understand provider output well enough to infer business progress, so only handoff, session, and Git-snapshot orchestration state changes.

### 16. Explicit Manual Switching Only

Phase 5 is manual. CortexShift does not parse provider quota or rate-limit messages, does not choose a next provider, does not terminate a running provider, and does not auto-invoke a switch. The user runs `cortexshift switch TARGET` explicitly.

### 17. Handoff Is Advisory; The Live Repository Is Authoritative

Every receiving-agent package communicates the permanent CortexShift authority order and states plainly that the handoff is advisory, that recorded completed items are not proof, and that a stored Git snapshot is not current truth once time has passed.

---

## Consequences

### Positive

- The product works in its actual motivating scenario: switching *after* the outgoing agent is unusable or gone.
- Handoffs are reproducible, inspectable, diffable, and cheap; generating and previewing one costs zero model quota.
- The canonical payload is provider-neutral, so adding a fourth agent means writing one delivery adapter, not a fourth prompt template.
- Historical handoffs remain immutable observations, preserving an audit trail of how a task moved between agents.
- Prompt formatting can improve later without invalidating stored history.
- Privacy posture is unchanged: no credentials, no transcripts, no rendered prompts, no provider responses, no full diffs, no Git remote URLs, no environment dumps.

### Negative / Trade-offs

- Structured state carries less nuance than a full conversation. Undocumented reasoning from the outgoing agent is genuinely lost; the receiving agent compensates by verifying against the repository. Later checkpoint and MCP phases can enrich this while agents are active.
- `IMPORTANT DECISIONS` and `TEST STATUS` are honestly empty in Phase 5. This is deliberate — a fabricated decision log would be worse than none — but it does mean early handoffs carry less than they eventually will.
- An Antigravity switch consumes one read-only planning turn of Antigravity usage before the interactive UI opens. The user's explicit `switch antigravity` authorizes that transport step; `--dry-run` never performs it.
- Antigravity handoffs are semi-automatic: the user may need to approve continuation in the resumed TUI.
- The transport rendering is bounded by character budgets rather than true token counts, so it is approximate by design.

---

## Alternatives Considered

**Ask the outgoing agent to write a summary before exiting.** Rejected as the primary mechanism. It fails exactly when quota is exhausted or the process has crashed — the motivating case. It also makes handoff quality depend on a model's willingness and remaining budget.

**Transplant the provider transcript.** Rejected. Transcript formats are proprietary, undocumented, model- and version-dependent, frequently enormous, and privacy-hostile. Cross-vendor conversion is a maintenance treadmill, and pasting one agent's conversation into another degrades attention rather than transferring understanding.

**Emulate keystrokes into the Antigravity TUI.** Rejected. Screen automation is brittle across versions and terminal sizes, and it would mean CortexShift clicking through permission prompts on the user's behalf.

**Silently drop the handoff for Antigravity, or make the user paste it manually.** Rejected. The first breaks the product promise invisibly; the second reintroduces exactly the manual re-explanation CortexShift exists to remove.

**Run the Antigravity bootstrap in an edit-capable mode.** Rejected. Unattended workspace mutation during a transport step is unsafe, and plan mode matches the first turn's actual purpose: ingest and plan.

**Persist the rendered provider prompt alongside the payload.** Rejected. It is redundant with canonical state, freezes historical data to today's prompt format, and stores a large blob whose only consumer is a process that already ran.

**Use a tokenizer or an LLM summarizer for context budgeting.** Rejected. It would add a heavy dependency or an AI SDK, make rendering non-deterministic, and cost quota during an operation that must stay free and reliable.

**Store the handoff payload across many typed SQL columns.** Rejected in favor of canonical validated JSON text in a single `payload` column. `HandoffPayload` is already a strictly typed Pydantic schema, so JSON keeps the storage extensible without a brittle, ever-widening column set — and without `repr`, `pickle`, or `eval`.

**Extend the Phase 2 `StateStore` interface with handoff methods.** Rejected. A cohesive `HandoffStore` port keeps interfaces narrow; one SQLite adapter implements several persistence ports while the application and domain layers stay storage-agnostic.

**Let `switch` fall back to `run` when no prior session exists.** Rejected. Silently reinterpreting a handoff request as a fresh launch hides a real mistake. `switch` fails clearly and points at `cortexshift run <provider>`.

---

## Related Decisions

- [ADR-0001: Core Architecture](ADR-0001-core-architecture.md)
- [ADR-0003: Project-Local Persistence](ADR-0003-project-local-persistence.md)
- [ADR-0004: Git Repository Context](ADR-0004-git-repository-context.md)
- [ADR-0005: Native Provider Launch & Session Lifecycle](ADR-0005-native-provider-runtime.md)

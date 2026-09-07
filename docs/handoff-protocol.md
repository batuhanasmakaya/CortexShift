# CortexShift Canonical Handoff Protocol v1

The **Canonical Handoff Protocol** is the provider-independent contract used to move a CortexShift Task between coding agents. As of Phase 5 this is an **implemented contract**, not a design sketch.

It is an internal state specification, not a network protocol. It defines the structured schema, the authority rules, and the operational expectations that apply when one agent yields control of a persistent Task and another assumes it.

> **Protocol version 1.** `HANDOFF_PROTOCOL_VERSION = 1` is persisted with every handoff and is deliberately **independent of the SQLite schema version**. Canonical fields and prompt formatting may evolve without a database migration, and the database may migrate without changing this protocol.

---

## 1. The Founding Constraint: The Outgoing Agent Is Never Required

The problem CortexShift exists to solve is:

```text
Claude works
↓
Claude quota is exhausted
↓
Claude may no longer be able to answer
↓
user wants to switch to Codex
```

Therefore the protocol forbids this shape entirely:

```text
switch requested → ask the outgoing agent to summarize → agent unavailable → handoff fails
```

Handoff generation is **deterministic from durable local state**. Its only inputs are:

```text
canonical Project
canonical Task
previous CortexShift Session metadata
live repository / Git inspection
persisted Git snapshot at handoff time
```

The outgoing provider does not need to answer anything, does not need to launch, and does not need to be installed. **No outgoing model call is ever made**, mandatory or otherwise. This is enforced by regression tests: a handoff to Codex succeeds while the `claude` executable is unresolvable, and CortexShift never even looks it up.

Later checkpoint and MCP phases may enrich handoffs *while* an agent is still active. This protocol must keep working after the outgoing agent is already gone.

---

## 2. Not Transcript Teleportation

CortexShift never:

```text
copies a Claude transcript into Codex
converts Claude transcript formats
parses Claude hidden history
parses Codex history databases
reads Antigravity conversation storage
scrapes a provider TUI
persists terminal transcripts
persists hidden reasoning
```

The abstraction is:

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

---

## 3. Authority Order

Every receiving-agent package communicates this hierarchy verbatim:

```text
1. Current repository files
2. Current live Git state
3. Verified command/test results
4. CortexShift canonical task state
5. Previous agent/session summaries and historical observations
```

The receiving agent is told, in the package itself:

> The handoff is advisory context. The live repository and independently verified command/test results are authoritative. Never treat a recorded completed item as proven, and never treat the Git state recorded here as current truth once time has passed.

---

## 4. Canonical Payload (`HandoffPayload`)

The point-in-time engineering context. Provider-neutral, immutable, never truncated in storage.

| Section | Field | Type | Notes |
| :--- | :--- | :--- | :--- |
| — | `protocol_version` | int | Always `1` in this release. |
| — | `generated_at` | datetime (UTC) | When the payload was built. |
| **PROJECT** | `project_name`, `project_root` | str | Identity and canonical root path. |
| — | `task_id`, `task_title`, `task_status` | str | Task identity; unchanged across handoffs. |
| **ORIGINAL OBJECTIVE** | `original_objective` | str | The user's original goal. Never rewritten by a handoff. |
| **REQUIREMENTS** | `requirements` | list[str] | Explicit functional/non-functional requirements. |
| **CONSTRAINTS** | `constraints` | list[str] | Technical boundaries and prohibited approaches. |
| **COMPLETED** | `completed` | list[str] | Recorded as completed in canonical state — advisory, must be verified. |
| **CURRENT WORK** | `current_work` | str \| None | In-flight work when the previous session halted. |
| **REMAINING** | `remaining` | list[str] | Unfinished backlog. |
| **IMPORTANT DECISIONS** | `important_decisions`, `decisions_known` | list[str], bool | Aggregated across every checkpoint persisted for the task; empty with `decisions_known = False` only when that whole history holds none (see §6). |
| **FILES TOUCHED** | `files_touched` | list[str] | Deduplicated union of live Git change classes (see §8). |
| **TEST STATUS** | `test_status` | `HandoffTestStatus` | `known` + `summary`; enriched from newest checkpoint with reported provenance disclaimer (see §6). |
| **KNOWN ISSUES** | `known_issues` | list[str] | Recorded bugs, blockers, failing edge cases. |
| **GIT STATE** | `git_state` | `HandoffGitState` | Status, availability marker, branch/HEAD/dirty, change counts, diff summaries, snapshot reference (see §7). |
| **DO NOT REDO** | `do_not_redo` | list[str] | Derived from recorded completed work; conservative phrasing (see §6). |
| **RECOMMENDED NEXT ACTION** | `recommended_next_action` | str | Deterministically derived (see §9). |
| — | `source_session` | `HandoffSourceSession` | Previous session's ID, provider, status, timestamps, exit reason/code. |
| — | `target_provider_id` | ProviderId | The receiving agent. |
| — | `operator_note` | str \| None | Optional human note with explicit provenance (see §10). |
| — | `source_checkpoint_id` | str \| None | ID of the source checkpoint used for enrichment (Phase 7). |
| — | `source_checkpoint_kind` | str \| None | Kind of the source checkpoint (`manual`, `recovery`, `session_end`). |
| — | `source_checkpoint_created_at` | datetime \| None | Creation timestamp of the source checkpoint. |

### Orchestration Record (`HandoffRecord`)

The payload is wrapped by a record carrying delivery metadata:

```text
id                    protocol_version
project_id            task_id
source_session_id     source_provider_id
target_provider_id    git_snapshot_id
target_session_id     status
payload               created_at
delivered_at          failure_code
metadata              source_checkpoint_id
```

Target-provider runtime state is deliberately kept out of the canonical payload.

---

## 5. Delivery Lifecycle

```text
prepared    canonical handoff exists; target delivery is not yet known complete
delivered   context was successfully delivered through the target provider strategy
failed      delivery or provider bootstrap could not be completed
```

Handoff status and Session status are **different concepts**. The target Session remains the source of truth for how the receiving coding session itself ended. A handoff may legitimately be `delivered` while its target Session ends `failed` — context arrived, and the provider crashed later while working.

Failures record a safe machine classification and never store raw provider stderr:

```text
target_provider_missing   bootstrap_failed
bootstrap_timeout         bootstrap_invalid_output
spawn_failed              workspace_locked
```

---

## 6. Honest Unknown State & Checkpoint Enrichment

CortexShift does not fabricate what it has not recorded.

When no checkpoint exists for the active task, these sections remain explicitly unknown:

```text
IMPORTANT DECISIONS
No structured decisions are recorded in CortexShift state.

TEST STATUS
No verified test result is recorded in CortexShift state.
The receiving agent must run relevant tests before relying on previous claims.
```

A provider process exiting with code 0 does **not** prove that project tests passed, and is never rendered as though it did.

### Checkpoint-Enriched Decisions & Test Status (Phase 7)

When an immutable checkpoint (`MANUAL`, `SESSION_END`, or `RECOVERY`) exists for the task, `HandoffBuilder` enriches the handoff payload:
- **`important_decisions`**: Aggregated across **all** checkpoints persisted for the task, providing historical context without parsing conversation transcripts. Decisions are *not* read from the newest checkpoint alone. A checkpoint is an immutable point-in-time observation and never absorbs an earlier checkpoint's decisions, so `record_decision` mints its own checkpoint and task-level durability is reconstructed at handoff time instead. Without this, any later checkpoint recorded with no decisions of its own — including the `SESSION_END` checkpoint captured automatically when a provider process exits — would bury every decision recorded during that session.
  - **Ordering**: chronological first-seen order, from `created_at ASC, rowid ASC` over the task's checkpoints. The `rowid` tie-breaker keeps the sequence deterministic when two checkpoints share a timestamp.
  - **De-duplication**: exact string equality only. A decision recorded redundantly in several checkpoints appears once, at its first occurrence; text is never trimmed, case-folded, or fuzzy-matched for comparison.
  - **Task scope**: the aggregation query takes a mandatory `task_id` and is additionally filtered in application code, so a handoff for one task can never surface another task's decisions.
  - **Provenance**: `record_decision` marks its checkpoint with `trigger = "decision"` in the existing `metadata` mapping (ADR-0009). That marker is provenance only — aggregation reads every checkpoint's `decisions`, whatever produced them, and never parses `operator_note`.
- **`test_status`**: If the checkpoint recorded a test summary, it is included with an explicit **`[Reported / Unverified Provenance]`** notice:
  ```text
  TEST STATUS
  Reported by prior agent / checkpoint (unverified by CortexShift):
  42 passed in 1.2s
  The receiving agent must independently verify test status before relying on previous claims.
  ```
- **Provenance Header**: The rendered handoff package includes a `LATEST CHECKPOINT (Provenance Reference)` section linking the checkpoint ID, kind, and creation timestamp. `source_checkpoint_id` continues to reference the newest checkpoint — the source of the Git and test-status enrichment — while `important_decisions` may draw on several checkpoints across the task's history.

**Completed / Do Not Redo semantics.** Recorded completed items are advisory. The package states that they are *recorded as completed in CortexShift canonical state* and that the receiving agent must verify them against the repository before depending on them. `DO NOT REDO` tells the agent not to rebuild that work from scratch — and explicitly not to skip verification. If verification shows an item is missing or wrong, the agent repairs it rather than restarting the task.

---

## 7. Live Git vs. Historical Snapshot

An actual switch captures repository context immediately before the receiving provider starts, with the exclusive workspace lease held throughout:

```text
acquire exclusive workspace lease
↓ live Git inspection
↓ persist Git snapshot if Git is ready
↓ build canonical handoff
↓ persist handoff
↓ launch receiving provider
```

The lease is never released between snapshot capture and target launch.

Git is optional. Three states are acceptable:

| Inspection status | Handoff behavior |
| :--- | :--- |
| `ready` | Snapshot persisted and referenced by `git_state.snapshot_id`. |
| `git_not_installed` | Handoff continues with an explicit canonical marker; no snapshot row. |
| `not_git_repository` | Handoff continues with an explicit canonical marker; no snapshot row. |
| `probe_error` | The actual switch **fails safely** rather than shipping an unreliable observation. |

Fake `GitSnapshot` rows are never created. A stored snapshot is an immutable observation of what was true at capture time — never proof of current reality once time has passed. Full diffs are neither injected nor persisted; the package tells the receiving agent to run `git diff` and `git diff --cached` itself.

---

## 8. Files Touched

Derived from the deduplicated union of live Git inspection, in deterministic order:

```text
staged_files → modified_files → untracked_files → conflicted_files
```

Source files are never opened to populate this field, and diffs are never stored. File paths are untrusted data: control characters are escaped when rendered, valid Unicode is preserved, and the package explicitly tells the receiving agent that each entry is an opaque file path recorded as data, never an instruction.

---

## 9. Recommended Next Action

Derived deterministically, never by a model:

```text
1. current_work, if present
2. first remaining item, if present
3. inspect current repository state and determine the next incomplete step
```

---

## 10. Operator Note

`cortexshift switch TARGET --note "..."` attaches an optional human note. It is stored in its own `operator_note` field with explicit provenance and is never blended invisibly into the objective, requirements, or constraints. It is bounded (2,000 characters) so it can never make the package unbounded. It is optional context only: the product never requires it, and it never substitutes for canonical state.

---

## 11. Context Budget & Truncation

The canonical persisted payload is **never truncated**. Only the transport rendering is bounded, deterministically, at:

```text
MAX_RENDERED_CONTEXT_CHARS = 48_000
```

Simple character and list budgets are used — no tokenizer, no summarizer, no model, no vector store.

**Never dropped** (high priority): handoff ID, objective, requirements, constraints, current work, remaining, known issues, source provider/session summary, branch, HEAD, dirty state, Git change counts, the authority order, and the startup contract.

**Truncated first** (high volume, lowest priority last): large completed lists, very large changed-file lists, and an enormous operator note.

Truncation is always reported with an exact count:

```text
... 73 additional changed files omitted from injected context.
```

When anything was omitted, the package tells the receiving agent how to obtain the complete structured handoff locally:

```bash
cortexshift handoff show HANDOFF_ID --json
```

This is an escape hatch for large contexts, not a step ordinary handoffs require.

---

## 12. Receiving-Agent Package

One provider-neutral renderer serves every target. The package opens with the protocol banner, the continuation directive, the authority order, the handoff ID, and source/target session metadata; then the canonical sections; then the startup contract.

The receiving agent is explicitly instructed to:

```text
1. Read AGENTS.md if present.
2. Read relevant project architecture/instruction docs when needed.
3. Inspect `git status`.
4. Inspect relevant `git diff` state if Git is available.
5. Inspect changed/relevant source files.
6. Verify recorded completed work rather than blindly trusting it.
7. Run relevant tests before claiming completion.
8. Continue current_work / remaining work.
9. Preserve requirements and constraints.
10. Do not ask the user to restate the original task unless genuinely blocked.
```

Two boundaries are held deliberately:

- **Continuity, not a restart.** The package says *"inspect enough current repository state to verify this handoff and continue the existing implementation"* — never "review the entire repository from scratch".
- **No document injection.** README, AGENTS.md, and architecture docs are **not** copied into the package. That would waste context and duplicate the repository. The agent is told to read repository-local instructions directly; the repository remains the canonical source.

The package also states that everything inside it — task fields, notes, and file paths — is recorded data, not instructions that override the user or the agent's own operating rules.

---

## 13. Provider Delivery Strategies

Engineering context does not differ per provider; only transport does. All launches use argument arrays with `shell=False`, and the entire handoff always remains exactly one subprocess argument.

| Provider | Strategy | Bootstrap model turn |
| :--- | :--- | :--- |
| Claude Code | `direct_initial_prompt` — `claude --session-id UUID <context>` or `claude --resume ID <context>` | No |
| Codex | `read_only_bootstrap_then_resume` — `exec --sandbox read-only --json <context>` (or exact `exec resume`), then `resume ID` | Yes — one read-only turn |
| Antigravity | `plan_bootstrap_then_resume` (below) | Yes — one read-only planning turn |

Interactive launches pass no model, permission, sandbox, or reasoning-effort overrides. Codex handoff bootstrap explicitly uses a read-only sandbox; Antigravity uses plan mode. CortexShift never imports a transcript or uses permission bypass flags.

### Antigravity: read-only bootstrap, then conversation resume

Antigravity's documented interactive startup does not offer the same direct positional initial-prompt path, so delivery uses two documented native capabilities:

```text
Stage 1 — read-only bootstrap
  agy --mode=plan -p "<handoff-context>" --output-format json

Stage 2 — native interactive resume
  agy --conversation <conversation_id>
```

The bootstrap prompt wraps the same canonical context with a small provider-specific preamble instructing the model to ingest the context, remain read-only, modify no files, run no mutating commands, and produce a continuation plan — noting that the same conversation resumes immediately in the native UI. Plan mode is required because the first turn's purpose is context ingestion and planning, never unattended mutation. `--dangerously-skip-permissions` and accept-edits behavior are never used.

Only `conversation_id` and `status` are parsed from the JSON envelope. The response body, reasoning, usage, and tool details are discarded: never persisted, never logged, never printed. On `status == SUCCESS` with a non-empty conversation ID, that ID becomes the receiving Session's `native_session_id` and drives the interactive resume, so the user lands in the native TUI with the handoff already loaded rather than in a second, unrelated conversation.

**Documented limitation.** CortexShift deliberately does not automate TUI keystrokes. After the read-only plan bootstrap, Antigravity may require the user to approve or continue from the resumed native UI. CortexShift says so plainly:

```text
Handoff delivered to Antigravity in read-only plan mode.
Opening the same conversation in the native TUI.
Review the prepared continuation plan and continue from there.
```

This is preferable to brittle TUI automation or unsafe unattended workspace mutation.

**Quota note.** An actual `cortexshift switch antigravity` performs one Antigravity headless planning turn before opening the TUI, which may consume usage. The explicit `switch antigravity` command authorizes that transport step. `cortexshift switch antigravity --dry-run` never performs it.

---

## 14. What a Handoff Never Contains

```text
environment variable dumps          provider credentials or API tokens
Git remote URLs                     SSH metadata
home-directory inventories          full source diffs
conversation transcripts            hidden reasoning
rendered provider prompts (not persisted)
provider bootstrap responses (not persisted)
```

Rendered prompts are transport representations, re-derived deterministically from canonical state when needed. This keeps historical canonical data free to benefit from future prompt-format improvements.

---

## 15. Switch Never Mutates Task Progress

`switch` never marks current work completed, alters the remaining list, clears known issues, or completes the task — not even when the receiving provider exits 0. The Task remains the same active Task; only handoff, session, and Git-snapshot orchestration state changes.

---

## 16. Handling Unexpected Termination *(current behavior and future work)*

Because handoff generation depends only on durable state and live Git, an abrupt termination of the previous agent does not prevent a handoff. The previous session's recorded status and exit reason travel with the package, so the receiving agent knows whether the prior session completed normally, crashed, or was interrupted.

Phase 7 will add automatic checkpointing so that in-flight context is captured while an agent works, enriching the payload beyond what the Task record alone holds.

---

## 17. Inspecting Handoffs

```bash
# Build and render a handoff without persisting or launching anything (zero model quota)
cortexshift handoff preview codex
cortexshift handoff preview codex --json

# Describe an actual switch without side effects (no Antigravity bootstrap turn)
cortexshift switch codex --dry-run
cortexshift switch antigravity --dry-run --json

# Historical handoffs
cortexshift handoff list
cortexshift handoff show HANDOFF_ID --json
```

`handoff show --json` exposes full IDs, protocol version, source/target metadata, the complete structured payload, the Git snapshot reference, the target session reference, status, and ISO-8601 UTC timestamps. It never exposes rendered provider-specific prompt strings or provider bootstrap responses.

---

## 18. Related Documentation

- [ADR-0006: Canonical Agent Handoff & Manual Provider Switching](decisions/ADR-0006-canonical-agent-handoff.md) — the decisions, consequences, and alternatives behind this protocol.
- [Architecture Overview](architecture.md) — where `SwitchService`, `HandoffBuilder`, `HandoffRenderer`, `HandoffStore`, and the delivery strategies sit in the hexagonal layering.
- [Agent Contributor Contract](../AGENTS.md) — the durable invariants every contributing agent must preserve.


## Phase 6: Fresh handoff into a returning native conversation

Switch now reuses the newest eligible target-provider native conversation on the active Task. Every switch still builds a new immutable HandoffRecord from canonical state and live Git; its target Session is a new invocation with `resumed_from_session_id` linking to the selected prior target. `--new-session` disables reuse; `--resume-session` selects an explicit target invocation.

The authority preamble warns that an existing provider-native conversation contains historical assumptions. Repository and task state may have changed; the fresh handoff supersedes those assumptions and requires renewed live inspection. Plain `cortexshift resume` does not create or inject a handoff.

Codex uses `codex exec --sandbox read-only --json <bootstrap-context>` for new managed handoffs, or `codex exec --sandbox read-only resume --json ID <bootstrap-context>` on return. JSONL must report a native thread, completed turn and no failure. Only the ID leaves the adapter; response data is discarded. Antigravity adds `--conversation ID` to its existing plan bootstrap on return. Both providers must confirm the requested ID before interactive resume. Bootstrap failure marks the new handoff failed without altering the old native reference or falling back to a fresh chat.

Codex and Antigravity bootstrap model turns are explicit transport costs. Dry-run exposes the selected native mode, prior target Session, native-ID availability, delivery strategy and model-turn requirement without running that transport. See [ADR-0007](decisions/ADR-0007-native-session-continuity.md).

---

## 19. Phase 8: Real-Time MCP Shared State & Agent Self-Reporting

With Phase 8, the receiving agent has access to the CortexShift Model Context Protocol (MCP) server running via local stdio.

### Instructions for Incoming Agents

1. **Explore Canonical Context**: Use read tools `get_project_context`, `get_current_task`, `get_latest_checkpoint`, and `get_repository_status` (or read `cortexshift://` resources) to inspect current constraints, decisions, and live Git status.
2. **Report In-Flight Progress**: Use write tools during execution:
   - `set_current_work(str)`: Record what is currently being edited or debugged.
   - `add_remaining(list[str])`: Add newly identified requirements or edge cases.
   - `mark_completed(list[str])`: Move completed work items from `remaining_items` to `completed_items`.
   - `record_issue(list[str])`: Log known bugs, blocker issues, or test failures.
   - `record_decision(str)`: Log crucial architectural decisions (persists an automatic checkpoint).
   - `create_checkpoint(decisions, test_summary, note)`: Capture cooperative milestone checkpoints with reported test execution summaries.
3. **Continuous Handoff Enrichment**: When the next handoff or switch occurs, all self-reported state captured via MCP is automatically synthesized into the next canonical handoff payload, ensuring subsequent agents inherit high-fidelity context without manual operator intervention.


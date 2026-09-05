# ADR-0008: Checkpoints, Crash Recovery & Handoff Enrichment

- **Status**: Accepted
- **Date**: 2026-09-05
- **Scope**: Phase 7; builds on Phase 5 canonical handoff and Phase 6 native session continuity.

## Context

Coding sessions frequently terminate without an orderly exit — agents exhaust rate limits or quotas, processes crash or receive SIGKILL, terminals are closed, or CortexShift itself is interrupted. In these scenarios, an outgoing provider cannot summarize its progress, leaving the task in an ambiguous state.

CortexShift requires a deterministic mechanism to reconstruct the latest reliable engineering state from durable local records and live Git inspection, without invoking any outgoing model calls, parsing provider transcripts, or guessing unobserved execution outcomes.

## Decisions

- **Checkpoint Protocol v1**: Checkpoints are stored as immutable `CheckpointRecord` entities containing a structured `CheckpointPayload`. Payloads record a snapshot of canonical Task state, live Git working tree and commit state, source session metadata, operator notes, engineering decisions, and reported test execution status.
- **Strict Storage Limits & Privacy**: No conversational transcripts, provider reasoning, credentials, or full file diffs are stored in checkpoints. Strict character limits are enforced at the domain boundary: `MAX_OPERATOR_NOTE_CHARS = 2000`, `MAX_DECISION_CHARS = 1000`, `MAX_TEST_SUMMARY_CHARS = 1000`.
- **Truth Hierarchy Preservation**: Checkpoint records are immutable historical observations of what was true at capture time. Live repository files, live Git status, and verified test results strictly outrank any historical checkpoint. Recorded completed items or dirty state never override live filesystem reality.
- **Honest Test Provenance**: Test results captured in checkpoints from agent output or operator flags are classified with `CheckpointTestProvenance.REPORTED_UNVERIFIED`. Canonical handoffs enriched with these results explicitly disclose that tests were reported by prior agents and not independently verified by CortexShift.
- **Cooperative Milestone Checkpoints Without Lease**: `cortexshift checkpoint create` allows human operators and active agents to record milestone progress while a provider session is actively running. It deliberately does NOT acquire the exclusive workspace lease (`.cortexshift/agent.lock`), eliminating lock contention during productive work.
- **Crash Recovery Under Exclusive Lease**: `cortexshift recover` strictly requires acquiring the exclusive workspace lease to ensure no other agent is actively modifying the repository. If the lease is held, recovery safely halts with `WorkspaceLockedError`.
- **Honest Session Reconciliation**: Stale unfinalized sessions (`INITIALIZING`, `RUNNING`) found when no process holds the lease are updated to `status=interrupted` and `exit_reason=unexpected_termination`. The recovery time is recorded in `reconciled_at`, while `ended_at` remains `None` — CortexShift never fabricates an unobserved process termination time.
- **Automatic Session-End Checkpoints**: Captured safely upon normal completion, non-zero exits, or keyboard interrupts before releasing the workspace lease. Inspection or persistence errors during session-end capture log a warning to session metadata without raising exceptions, preventing checkpoint failures from failing an otherwise successful coding session.
- **Checkpoint-Enriched Handoffs**: `HandoffBuilder` injects decisions and reported test status from the latest checkpoint into `HandoffPayload` and links `source_checkpoint_id` on `HandoffRecord`. Provider startup contracts advise receiving agents to inspect live repository state and create cooperative checkpoints at key milestones.
- **Schema v6 Persistence**: Forward-safe migration adds the `checkpoints` table with foreign keys to `projects`, `tasks`, `sessions`, and `git_snapshots`, indexed by task/creation, session, snapshot, and kind. It adds `reconciled_at TEXT` to `sessions` and `source_checkpoint_id TEXT` to `handoffs`. All migrations run transactionally with full rollback on failure.

## Trade-offs

- Checkpoints record self-reported agent/operator decisions and test summaries rather than cryptographic execution traces. Marking test status as unverified honestly balances context continuity with truthfulness.
- Capturing Git state during session-end adds minor overhead (~10-20ms) to provider exit, executed within the already held workspace lease.
- Dry-run recovery inspects live Git and computes reports without writing to SQLite or modifying sessions.

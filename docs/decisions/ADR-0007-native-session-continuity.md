# ADR-0007: Native Session Identity, Resume & Return-to-Provider Continuity

- **Status**: Accepted
- **Date**: 2026-09-05
- **Scope**: Phase 6; supersedes the Phase 4/5 launch and handoff transport details in ADR-0005/0006 where noted below.

## Context

A Task is canonical across providers. A CortexShift Session records one orchestration invocation. A provider-native conversation may span many such invocations. Canonical handoffs alone cannot restore a provider's own history when returning to it.

## Decisions

- Never guess provider-native identity, inspect provider transcript/cache/auth storage, or use automatic provider-last heuristics (`--last`, `--continue`). Capture only IDs from supported native CLI surfaces.
- Allocate a UUID4 before new Claude interactive launches, using `--session-id`. Persist the ID before the interactive process starts. Historical null IDs stay null.
- Capture Codex `thread.started.thread_id` during one `codex exec --sandbox read-only --json <bootstrap-context>` turn. Require a zero process exit, valid JSONL, a valid thread ID, a completed turn, and no error/failed-turn event. Discard response bodies, reasoning, tool details, usage and the entire JSONL stream. Resume interactively with `codex resume ID`.
- Return to Codex with `codex exec --sandbox read-only resume --json ID <bootstrap-context>`, verifying that the returned ID matches. The installed codex-cli 0.144.3 help confirms this option placement: sandbox belongs to the parent `exec`, JSON is supported on `exec resume`. The interactive resume receives no sandbox/model/permission override; provider-native settings govern it.
- Preserve Antigravity's read-only plan bootstrap and `conversation_id` capture. For a returning conversation, append `--conversation ID` to the bootstrap argv, require `SUCCESS` and the same ID, then open `agy --conversation ID`. No `agy` binary was available during implementation, so its exact live E2E remains deferred; the existing Phase 5 contract is retained and deterministically tested.
- `ProviderNativeSessionAdapter` is an optional runtime port. Its immutable capability object describes exact resume, preallocation, bootstrap capture, follow-up context, model-turn requirements, and managed-new support. Unsupported adapters advertise no continuity rather than implementing placeholder operations.
- Every resume creates a new CortexShift Session. Schema v5 adds nullable `resumed_from_session_id REFERENCES sessions(id) ON DELETE SET NULL`; no separate native-session table or redundant resumability column is necessary. Existing invocations are never rewritten by resume.
- Exact resumability requires a safe, non-empty native ID, an adapter supporting exact resume, and a finalized invocation with process exit evidence, excluding `spawn_failed`. Unfinished/stale rows are conservatively not selected; they never block a free OS lease. IDs are opaque bounded ASCII identifiers, never option strings or file paths.
- Selection uses the newest `started_at` across the complete history of the active task and requested provider. It does not stop at an arbitrary 100-session window. Explicit `--session` / `--resume-session` validate task, provider, identity and launch evidence.
- `ResumeService` reuses run context resolution and the shared lifecycle launcher. Plain resume does not build a handoff, inspect Git, or capture a snapshot. All actual interactive invocations retain the lease and both-stdio TTY contract. Dry runs launch no process, consume no model turn and create no canonical records.
- Switch target policy defaults to auto: reuse the newest exact eligible target conversation with a fresh canonical handoff; otherwise use managed-new delivery. `--new-session` forces fresh identity. `--resume-session` selects a prior target explicitly and is mutually exclusive with `--new-session`.
- Fresh handoffs explicitly supersede stale assumptions in the old native conversation. Live repository files, Git, and verified test results retain their higher authority. Returning native history never replaces canonical Task state.
- Bootstrap or native resume failure never silently falls back to a fresh conversation. The old Session remains intact; failed invocations/handoffs record safe lifecycle classifications. Provider-owned IDs may no longer exist; CortexShift delegates that check to the native CLI rather than reading private storage.

## Codex plain-run investigation

The [official App Server contract](https://learn.chatgpt.com/docs/app-server) separates `thread/start` from `turn/start` and exposes `thread.id` and `thread.sessionId`. It does not establish that creating an empty thread, terminating the App Server, and immediately invoking normal CLI resume is reliably interoperable without a first turn. Consequently Phase 6 does not use App Server for plain run. Both `codex` and `codex <user-prompt>` retain native direct TUI semantics and record a null native ID. There is no hidden model call to manufacture tracking coverage.

The [official noninteractive contract](https://learn.chatgpt.com/docs/non-interactive-mode) documents JSONL event output and exact `codex exec resume <SESSION_ID>`. Claude launch/resume flags were checked against the installed CLI help. Live paid provider turns are not part of automated verification.

## Trade-offs

Managed Codex and Antigravity handoffs each consume one read-only model turn before opening the TUI, including returns. Explicit switch authorizes that transport; dry-run reports the requirement without performing it. Interactive continuation may still need user approval inside the native provider. Read-only transport can influence provider-owned session settings; CortexShift passes no configuration overrides to interactive resume.

Plain Antigravity and Codex runs may remain untracked. Legacy null IDs and unfinished invocations are not exactly resumable. These capability limits are preferable to guessing another conversation or changing interactive semantics. No SDK or runtime dependency is added. Checkpoints, recovery reconciliation, MCP, quota parsing, and automatic switching remain outside Phase 6.

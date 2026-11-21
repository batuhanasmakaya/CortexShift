# ADR-0018 — Switch and resume the active Task

**Status** Accepted

## Context

DX1 stores a complete objective and can serve it in managed MCP pages. DX2
extends that delivery path to switch and resume. Native conversation identity,
canonical Task continuity, and provider process lifecycle have distinct evidence.

## Decision

`ResumeService` selects the active, nonterminal Task in the verified workspace
and resolves the launch under the workspace lease. Bare resume follows the newest
meaningful Session in persisted ordering. It asks the provider for exact resume
when that Session has a recorded eligible ID and finalized process evidence.
Without that evidence, Claude Code and Codex can continue the Task in a fresh
conversation. A provider argument remains exact-only by default. `--session`
pins a same-Task selection and `--new-session` requests fresh continuation.
An unfinalized source prompts a recovery check. A reconciled source without an
observed exit remains eligible for fresh continuation where supported.

`SwitchService` settles the source and target under the workspace lease. It
uses the latest Task checkpoint reference and independently carries the latest
historical test evidence and all distinct recorded decisions. The destination
Session, the handoff binding, and any Git snapshot are committed together
before provider work. A failed spawn is recorded and skipped as the default
source when earlier meaningful work exists.

Switch reuses the existing bounded transports. Claude Code requests its bound
handoff through MCP. Codex uses the read-only JSONL bootstrap to capture the
exact thread ID and then opens the native TUI with a short follow-up. Both
runtimes request the complete objective, current Task fields, and historical
context through the live MCP binding. An oversized objective is deferred to
paged MCP for these targets and identified as omitted in the excerpt. Its
canonical text remains intact. Identity, requirements, and constraints are
required in the 48,000 UTF-8 byte package.

Exact Claude Code and Codex resume can attach the same short request without
creating a handoff. Antigravity retains its plan-mode switch, which requires
the essential context to fit in the bootstrap. Its exact resume opens the
recorded native conversation, with automatic Task refresh unavailable.

## Consequences

MCP receipts record a served prefix or a served handoff. Headless receipts
record a provider-reported bootstrap completion. The launcher finalizes the
same bound Session without retaining its live MCP authority. Each invocation
has its own record, with distinct lineage for an exact resume or a fresh Task
continuation. Schema v6 and canonical checkpoint and handoff payloads stay
compatible.

Fresh interactive Codex launches retain an unknown native ID. The installed
0.153.4 CLI confirms the interactive resume prompt and JSONL execution
surface. The documented lifecycle hook includes a session ID and depends on
provider trust. Current CLI defaults do not provide a dependable hook receipt.
Context completion and provider permissions remain observable through their
respective receipts and native UI.
[Codex hooks](https://learn.chatgpt.com/docs/hooks) ·
[Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode)

`ResumeService`, `SwitchService`, `ProviderSessionLauncher`, and
`ProjectStatusService` provide the reusable application boundary. The CLI and
TUI use their launch plans, callbacks, and results. The TUI releases the
terminal before native provider work.

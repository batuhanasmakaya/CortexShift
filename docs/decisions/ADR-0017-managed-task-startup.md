# ADR-0017 — Managed Task startup

**Status** Accepted

## Context

Starting work required separate Task creation and provider launch. File and
piped objectives can exceed native argument limits and the handoff budget.
Users need an interactive launch that finds the right Task automatically.

## Decision

`start` resolves the initialized workspace from the caller's directory and uses
the same provider discovery, workspace lease, and Session launcher as managed
execution. Task creation uses the existing Task write transaction. The default
checks for an empty active pointer in that transaction. `--replace-active`
explicitly authorizes moving the pointer and preserves the previous Task.
`--task ID` opens a fresh invocation for the selected active Task. A normal
launch failure preserves the committed Task and finalizes any created Session
using the existing failure semantics.

Claude Code and Codex use a fixed native initial instruction and their existing
per-launch MCP binders. Antigravity's workspace MCP configuration lacks this
capability. When the caller supplies stdin, the CLI consumes it to EOF and opens
the controlling terminal or Windows console input device for direct child input.
The provider still owns its terminal interaction.

The Task stores the full objective with whitespace. The startup MCP tool
`get_task_objective` serves up to 4,000 Unicode characters per call. It starts
at offset zero, permits repeated reads, and uses `next_offset` to continue until
the final page. Only a live startup invocation can request pages. Every request
rechecks the bound workspace, Project, Task, Session, provider, and invocation
authority. The existing `get_current_task` tool accepts `include_objective=false`
to return the remaining Task fields after paging.

## Consequences

The Session records the length of the contiguous objective prefix served.
Updating that receipt and finalizing the Session both use SQLite's existing
writer ordering. This provides transport evidence for none, some, or all of
the objective and does not confirm provider or model consumption. No startup
configuration, page text, or provider response is added to Session state.

Startup reads current canonical Task data. The historical switch handoff is a
separate operation and its 48,000 UTF-8 byte budget still applies. Schema v6,
handoff v1, and checkpoint v1 remain compatible. Claude Code retains its
preallocated exact session ID. A fresh Codex interactive launch has no recorded
native ID. Codex still learns a native ID through the existing switch bootstrap.

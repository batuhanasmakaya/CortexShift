# ADR-0013 · Atomic Task writes

**Status** Accepted

## Context

Task updates previously loaded a complete row and later replaced that row in a separate
transaction. Two writers could start from the same Task and commit successfully while
the second erased progress from the first. Task creation and completion also committed
the row and the active pointer independently.

## Decision

`TaskService` owns the mutation rules. It opens a short Task write transaction before
looking up the Task or the active pointer. The SQLite adapter uses `BEGIN IMMEDIATE`
on the verified connection, so each writer reads the last committed state after
acquiring the write reservation. SQLite enforces that reservation across processes.
The store also serializes access to its own connection for callers such as the MCP
server that share a connection across threads. One caller owns commit or rollback.
Nested write transactions are rejected. [SQLite transaction documentation](https://www.sqlite.org/lang_transaction.html)

The transaction covers state lookup, ownership and lifecycle checks, domain changes,
and persistence. Git inspection, provider work, and user interaction occur outside
that scope. Separate connections in WAL mode can read committed state while a Task
write is in progress. Writer contention uses SQLite's configured five-second busy
timeout and surfaces a storage error if the write cannot proceed. CortexShift does
not replay operations automatically. [SQLite isolation documentation](https://www.sqlite.org/isolation.html)

- Creation commits the new Task and any requested activation together. An inactive
  creation starts pending and leaves the pointer as it was.
- Activation checks project ownership and terminal status within the transaction.
  The pointer follows the last successful activation and eligibility checks preserve
  the existing lifecycle status of the selected Task.
- Completion checks the latest status and clears the pointer only when it still names
  the completed Task. A repeated explicit completion returns an already-completed
  error. A cancelled Task cannot be completed.
- Progress writes require a pending, in-progress, or blocked Task. List operations
  retain their existing ordering and deduplication rules. Replacing current work
  follows the order in which the writes commit.
- CLI default-active mutations resolve the pointer inside the transaction. MCP tools
  use the session-bound Task ID. TUI progress and checkpoint dialogs use the ID
  captured when the operator opened the dialog.

Cooperative decisions and checkpoints are immutable records associated with their
Task. They can still capture historical context for a terminal Task, and their
inspection and persistence remain separate from Task mutation transactions.

## Consequences

Existing schema v6 databases use the same tables and migration history. A revision
column would add migration and conflict-retry handling for a workload SQLite can
already serialize at the write boundary. The guarantee covers callers that use
`TaskService` on each write. Older clients or direct database writers using a stale
whole-row upsert can still overwrite state. Pre-existing inconsistent active pointers
are preserved until an operator selects or creates a valid active Task.

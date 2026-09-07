# ADR-0009: MCP Shared State & Agent Self-Reporting

- **Status**: Accepted
- **Date**: 2026-09-06
- **Scope**: Phase 8; builds on Phase 5 canonical handoffs, Phase 6 native continuity, and Phase 7 checkpoints and recovery.

## Context

Prior to Phase 8, coding agents received canonical context strictly at session launch (via rendered markdown prompt attachments or CLI arguments) and could not report structured updates back into CortexShift mid-flight without exiting or relying on external operator CLI commands.

Coding agents require a first-class structured channel to:
1. Query canonical project context, active task requirements, recent checkpoints, and live repository status.
2. Self-report ongoing progress, update current work, add newly discovered remaining items, mark completed items, record known issues, and log architectural decisions.
3. Capture cooperative milestone checkpoints with reported test status mid-session before handoffs occur.

All of this must occur without violating CortexShift's architectural invariants: local-first execution, zero telemetry, zero credential persistence, pure stdout wire protocol, workspace lease safety, and truthful provenance boundaries.

## Decisions

- **Local Stdio Transport Exclusively**: CortexShift MCP operates strictly via local subprocess stdio (`python -m cortexshift mcp serve`). Network listeners, HTTP/SSE endpoints, and remote transport mechanisms are prohibited, preserving local-first privacy and zero-telemetry guarantees.
- **Pure Stdout Wire Protocol**: Standard output (`stdout`) is reserved strictly and exclusively for valid MCP JSON-RPC protocol frames. All diagnostic messages, informational logs, and CLI status notices are routed to `stderr` or silenced. Non-protocol stdout emissions break client JSON-RPC parsers and are strictly prevented.
- **Context Binding via McpExecutionContext**: The server resolves and validates execution context at launch from environment variables:
  - `CORTEXSHIFT_PROJECT_ROOT`: Absolute path to the initialized repository.
  - `CORTEXSHIFT_TASK_ID`: ID of the task assigned to this session.
  - `CORTEXSHIFT_SESSION_ID`: Active CortexShift session ID.
  - `CORTEXSHIFT_PROVIDER_ID`: Identity of the running agent provider.
  - `CORTEXSHIFT_MCP_READ_ONLY`: Flag (`1` or `0`) enforcing read-only behavior.
  The context strictly binds tool mutations to the assigned `Task` and `Session`, isolating operations against external changes to the active task in SQLite.
- **Binding Delivery Is Explicit, Never Inherited**: A provider CLI spawns the MCP server, so the server is CortexShift's *grandchild* and each provider decides how much of the intermediate environment it forwards. Codex forwards a fixed allowlist plus the server's own declared `env` table, which silently strips `CORTEXSHIFT_*` and left real managed Codex sessions read-only with `session: null`. CortexShift therefore writes the binding into the per-launch MCP configuration it generates for each provider, and continues to export it into the provider process for providers and tooling that do inherit. The binding is minted in exactly one place, `ProviderSessionLauncher.run`, from the already-persisted `Session`; it always overrides any environment an adapter contributed, and nothing supplied by a provider or a model can produce or alter it. Antigravity's workspace configuration is written once rather than per launch, so it carries no binding and its execution mode is unchanged.
- **Dual-Mode Capability Exposure (Managed vs Read-Only)**:
  - **Managed Mode** (`managed=True`, `read_only=False`): Exposes all 10 tools (4 read tools and 6 write tools). Granted only when running within an authenticated, active provider session bound to an existing task.
  - **Read-Only Mode** (`read_only=True` or unmanaged): Exposes only 4 read tools. Write tools are not registered on the server; attempting to invoke write operations raises `McpReadOnlyError`. Headless provider bootstrap sessions pass `CORTEXSHIFT_MCP_READ_ONLY=1`.
- **Exposed Tools**:
  - **Read Tools**:
    - `get_project_context`: Project metadata, active task summary, truth hierarchy guidelines.
    - `get_current_task`: Bound task title, objective, requirements, current work, completed/remaining items, known issues.
    - `get_latest_checkpoint`: Most recent checkpoint for the bound task.
    - `get_repository_status`: Live, read-only Git working tree status (branch, SHA, staged/modified/untracked files, shortstat).
  - **Write Tools**:
    - `set_current_work`: Atomically updates `Task.current_work` (`MAX_CURRENT_WORK_CHARS = 500`).
    - `mark_completed`: Atomically moves specified items from `remaining_items` to `completed_items`. Does NOT mark the overall task completed.
    - `add_remaining`: Appends newly discovered work items to `Task.remaining_items`.
    - `record_issue`: Records known blockers or issues into `Task.known_issues`.
    - `record_decision`: Persists an architectural decision backed by an automatic checkpoint (`trigger="decision"`).
    - `create_checkpoint`: Creates a cooperative milestone checkpoint with explicit `reported` test provenance.
- **Exposed JSON Resources**:
  - `cortexshift://project`: Canonical project context (`application/json`).
  - `cortexshift://task`: Bound task state (`application/json`).
  - `cortexshift://checkpoint/latest`: Latest task checkpoint (`application/json`).
  - `cortexshift://repository`: Live read-only Git status (`application/json`).
- **Workspace Lease Bypass**: MCP server tool executions deliberately bypass the workspace advisory lock (`.cortexshift/agent.lock`). The OS lock is already held by the provider CLI process that spawned the MCP server; attempting to acquire the lock would cause a self-deadlock. SQLite WAL mode provides multi-process safe atomic transitions.
- **Thread Safety in FastMCP**: FastMCP dispatches synchronous tool and resource callbacks across worker threads using `anyio.to_thread.run_sync`. SQLite connections are configured with `check_same_thread=False` to support concurrent read and write operations across worker threads.
- **Provider Transports**:
  - **Claude Code**: Configured automatically per launch via `--mcp-config <inline JSON>`.
  - **OpenAI Codex**: Configured automatically per launch via `-c mcp_servers.cortexshift...` inline CLI flags.
  - **Google Antigravity**: Configured via `.agents/mcp_config.json` in workspace root. Managed via `cortexshift mcp setup antigravity` (`--dry-run`, `--force`). Launch issues an advisory notice on stderr if unconfigured.
- **Schema Preservation**: Retains SQLite schema version strictly at **v6**. No schema migrations are required; MCP state operations map cleanly to existing project, task, session, and checkpoint models.

## Trade-offs

- Running the MCP server in-process alongside SQLite WAL concurrency removes lock contention at the cost of requiring careful thread and connection handling in `SQLiteStateStore`.
- Antigravity uses workspace-local `.agents/mcp_config.json` which may appear in Git status if not ignored by the user. CortexShift never silently modifies `.gitignore`, adhering strictly to read-only repository inspection invariants.
- Test summaries captured via MCP `create_checkpoint` are truthfully flagged as `reported` provenance, ensuring downstream agents never confuse self-reported test claims with verified results.

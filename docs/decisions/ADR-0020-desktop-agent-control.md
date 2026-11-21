# ADR-0020 — Desktop agent control in a native terminal

**Status** Accepted

Desktop users need to create Tasks and control managed providers while continuing to observe the project. The existing bridge uses private stdio for bounded requests. Interactive providers require their own terminal input and output, and their launcher holds process ownership and MCP authority throughout the invocation.

A closed set of typed Tauri commands binds renderer actions to the registered Project. The Desktop application service reuses verified workspace identity, atomic Task writes, the provider runtime adapters, `StartService`, `SwitchService`, `ResumeService`, and `RecoveryService`. A confirmed plan pins the active Task and reviewed Session selection. The services revalidate selection at the launch boundary. A Desktop execution lease adds a check for unresolved invocation ownership while holding the existing core workspace lease.

The request bridge schedules a one-shot app-local ticket. The native terminal worker claims that ticket under a separate advisory lock and consumes it before executing. The worker directly inherits terminal streams, waits for the immediate provider, and finalizes through the shared Session launcher. It remains independent of the WebView and request bridge. The frozen executable implements the exact managed MCP entry arguments so existing provider binders work without a separate Python installation.

Task creation streams UTF-8 through the existing private pipe and commits the objective with the Task. A client-generated Task ID makes the canonical record available when transport fails and protects same-input retries. The active pointer compares against the operator's observed value in the same transaction. Terminal retries use fresh confirmed actions after inspecting the latest durable receipt. A pending ticket can be invalidated before claim. Interrupted invocations use existing recovery rules.

The Desktop app retains its Graphite, Slate, and Soft Indigo design. Scope includes native managed Claude Code and Codex. The existing Antigravity CLI integration, first-party app handoff, and SDK surfaces retain their respective capabilities. SQLite schema v6, managed MCP, and published handoff and checkpoint protocol semantics are preserved. Desktop build tooling stays outside the Python installation path.

[Desktop IPC and lifecycle](../desktop/ipc.md)

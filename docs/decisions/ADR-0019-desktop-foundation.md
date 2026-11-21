# ADR-0019 — Desktop workspace browser

**Status** Accepted

## Context

DX3A needs a native graphical view of the same Tasks, Sessions, and historical records used by the CLI. Opening the view must be safe alongside a managed agent or a competing CLI process.

## Decision

Use Tauri 2 as a native shell and React, TypeScript, Vite and Tailwind for the renderer. Place workspace reads behind a versioned Python stdio bridge. Rust authorizes project selection through a native folder picker and a local identity registry. The Python application layer opens a schema-v6 SQLite read transaction and revalidates the root and project on every query. No schema changes are needed.

Use native Rust pipes to supervise a fixed child. This keeps process I/O, request bounds, timeouts and shutdown behind the shell. The renderer receives application-command permissions only. An HTTP bridge would add a listener and authentication surface. Direct SQLite access would duplicate scope, identity and lifecycle rules across languages.

Use the existing uv environment for development. Build host-specific frozen sidecars for local bundle and CI checks. Defer signing, notarization and installed-platform qualification to distribution work.

## Consequences

The Desktop integrates with the same working tree, schema and truth hierarchy as the CLI. Worktree ownership is observed per workspace and Session state is last-recorded evidence. Read operations do not initialize or migrate state, recover Sessions, or launch provider executables. Project and Task histories are fetched in bounded pages. Visual provider control remains DX3B.

[Desktop architecture and IPC](../desktop/ipc.md) · [Packaging](../desktop/packaging.md) · [Provider integration matrix](../desktop/integrations.md)

# ADR-0012 · Verified workspace identity

Status: Accepted

## Context

CortexShift discovers project-local state by walking up from the invocation directory.
Provider services also used the persisted Project path as a working directory. Moving or
copying a workspace could put those two paths in different directories. Opening SQLite
before validating the Project could also change journal settings or migrate the copy.

## Decision

The application verifies that the discovered root and the persisted absolute Project path
resolve to the same existing directory. The verified root supplies the database path, Git
inspection path, lease location, launch working directory, and managed MCP binding. The
check works for non-Git projects. Relative invocation paths and normal ancestor discovery
still work. Root aliases such as symlinks and macOS `/tmp` resolve on the host filesystem.
Redirected private state files and lock files are rejected.

SQLite inspects identity read-only before opening existing state for writes. A probe of a
quiescent database avoids creating journal sidecars. When a journal is present, SQLite
uses its ordinary read-only locking and reads committed WAL frames. The operational
connection verifies identity again before setting WAL mode or applying pending migrations.

The dashboard and managed MCP server retain the verified root, Project ID, and filesystem
identities. Provider dispatch reopens exactly that workspace. Run, resume, switch, and
recovery revalidate before taking the lease or starting the provider. Git snapshots and
checkpoints revalidate before saving. A relocated or copied project is refused with the
discovered and recorded locations. The recorded Project path is left intact.

## Limits

The checks cover normal moves, copies, aliases, and detectable replacements at operation
boundaries. A process that can concurrently replace directories or edit the database can
still race these checks. SQLite may touch shared-memory coordination files when reading a
live or interrupted WAL. The database schema and provider, checkpoint, and handoff
protocols are unchanged.

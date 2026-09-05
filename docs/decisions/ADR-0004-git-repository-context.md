# ADR-0004: Git Repository Context and Snapshot Architecture

- **Status**: Accepted
- **Date**: 2026-09-05
- **Deciders**: CortexShift Core Architecture Team

---

## Context

Phase 0 established foundational domain entities (`GitSnapshot`) and abstract ports (`RepositoryInspector`). Phase 1 implemented passive native provider discovery. Phase 2 added project and task persistence via SQLite schema v1.

However, CortexShift still lacked awareness of repository filesystem reality. When coordinating coding agents across handoffs, the orchestrator and incoming agents must know:
- What branch and commit the workspace is currently based on.
- Which files have been modified, staged, or left untracked.
- Whether merge or rebase conflicts exist.
- Summary statistics of working tree and staged changes.
- Historical snapshots of repository state at critical checkpoints.

We need a Git inspection and persistence architecture that preserves CortexShift's foundational invariants: native-first, read-only safety, deterministic parsing, local persistence, and no heavy dependencies.

---

## Decisions

We have made the following foundational decisions for repository inspection and snapshot persistence in Phase 3:

### 1. Native `git` CLI over Third-Party Libraries
Repository inspection invokes the host's native `git` executable via the `CommandRunner` port:
- Prohibits dependencies on heavy or binary C-binding libraries (such as `GitPython`, `pygit2`, or `dulwich`).
- Relies exclusively on standard `git` CLI subcommands available across modern developer machines.
- Inherits user Git configurations (such as `.gitignore`, global ignore rules, and line-ending settings) natively without emulation.

### 2. Strictly Read-Only Subprocess Execution
CortexShift's repository inspection is strictly non-mutating:
- Commands executed: `git --version`, `git rev-parse`, `git symbolic-ref`, `git status`, and `git diff --shortstat`.
- Strictly prohibited: `git add`, `git commit`, `git checkout`, `git switch`, `git reset`, `git clean`, `git fetch`, `git pull`, `git push`, or any mutating git subcommand.
- Subprocesses run with a sanitized environment (`_GIT_ENV`):
  - `GIT_TERMINAL_PROMPT=0` (prevent interactive credential prompts from hanging).
  - `GIT_PAGER=cat` (prevent terminal pagers from blocking headless execution).
  - `GIT_OPTIONAL_LOCKS=0` (prevent background index refresh locks).
- All subprocesses enforce finite timeouts (default 10 seconds).

### 3. NUL-Safe (`-z`) Porcelain v1 Status Parsing
Working tree status is parsed from `git status --porcelain=v1 -z --untracked-files=all`:
- The `-z` NUL-byte delimiter prevents path truncation, escape sequences, or quoting artifacts on filenames with spaces, unicode characters, tabs, or newlines.
- Rename records (`R ` or `C `) consuming two consecutive NUL-delimited records (`dest\0orig\0`) are correctly mapped to destination paths.
- Unmerged conflict states (codes `UU`, `AA`, `DD`, `AU`, `UD`, `UA`, `DU`) are parsed into `conflicted_files`.
- Mixed state entries (such as `MM`, where a file is both staged and has unstaged modifications) are tracked in both staged and modified lists.

### 4. Monorepo and Project Path Scoping
When a CortexShift project root is a subfolder inside a larger Git repository:
- Git root and project root are identified independently (`git_root` vs. `project_root`).
- Status and diff outputs are filtered and normalized relative to the CortexShift project root.
- Changes entirely outside the project root are ignored.
- File paths stored in `GitSnapshot` are strictly relative to the project root.

### 5. Exclusion of Internal Private State
CortexShift's runtime state resides in `<project_root>/.cortexshift/`.
- The parser explicitly ignores any paths starting with `.cortexshift/` even if `.gitignore` is missing or temporarily broken.
- Internal SQLite databases (`state.sqlite3`, `state.sqlite3-wal`, `state.sqlite3-shm`) are never surfaced as untracked repository files.

### 6. SQLite Schema Migration v2 (`git_snapshots`)
Snapshot persistence is integrated into the project's SQLite state store via schema version 2:
- Migration `_migrate_v2` adds the `git_snapshots` table with foreign key constraint `project_id REFERENCES projects(id) ON DELETE CASCADE`.
- Indexes `idx_git_snapshots_project_id` and `idx_git_snapshots_captured_at` optimize temporal and project-scoped lookups.
- Lists (`staged_files`, `modified_files`, `untracked_files`, `conflicted_files`) and `metadata` are stored as validated JSON text.
- `RepositorySnapshotStore` port defines `save_snapshot`, `get_snapshot`, and `list_snapshots`.

### 7. Truth Hierarchy: Historical Observation Semantics
Stored `GitSnapshot` records represent immutable historical observations at capture time:
- A stored snapshot is evidence of what was true when captured, never proof of current working tree reality.
- Live filesystem and native Git inspections outrank stored snapshot records.
- In handoffs and checkpoints, stored snapshots provide comparative reference (e.g. verifying whether files were modified since the last agent session).

### 8. Compact Diff Statistics over Full Diffs
Snapshots store human-readable summary lines (`git diff --shortstat` and `git diff --cached --shortstat`) rather than full patch diffs or binary blobs:
- Keeps the SQLite database lean and prevents exponential state growth on large refactors.
- Full file inspection remains available dynamically through the filesystem and native Git.

### 9. Non-Blocking Fallback for Non-Git Environments
CortexShift does not require Git for core task persistence:
- If `git` is not found on `PATH` or the project directory is not a Git repository, `cortexshift repo status` exits with status code 0 and reports diagnostic information (`git_not_installed`, `not_git_repository`).
- `cortexshift repo snapshot` fails cleanly with explanatory error messages rather than crashing.

---

## Consequences

### Positive
- Cross-agent handoffs in Phase 5 will contain precise, empirical Git context.
- Zero external Python Git dependencies guarantees portability across macOS, Linux, and Windows.
- Unborn repositories (0 commits), detached HEADs, and complex merge conflicts are handled gracefully.
- Project-relative path scoping ensures proper operation in monorepos.

### Negative / Trade-offs
- Invoking native `git` CLI subprocesses incurs slight overhead compared to direct in-memory C libraries (acceptable given inspection runs on explicit user commands or session boundaries).
- Bounded timeouts mean exceptionally massive repositories (>100k untracked files) could time out if `git status` takes longer than 10 seconds (handled gracefully by `GitProbeTimeoutError`).

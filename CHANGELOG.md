# Changelog

This project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html),
with compatibility care for commands, persisted state, and MCP contracts during 0.x.

## 0.1.0 — Unreleased release candidate

Initial public alpha; no publication date has been assigned.

### Added

- Project-local SQLite tasks with objectives, requirements, structured progress,
  known issues, and durable history.
- Passive discovery and native terminal orchestration for Claude Code, Codex,
  and Antigravity through provider adapters.
- Deterministic canonical handoffs, manual switching, exact known-ID resume,
  and fresh context on return to a provider.
- Cooperative milestone and session-end checkpoints, plus deterministic crash
  recovery with honest historical observations and reported test provenance.
- Local stdio MCP shared state and a keyboard-driven Textual control center.
- Release metadata with verified canonical project URLs, wheel/sdist content
  validation, installed CLI/MCP/TUI tests, isolated pipx tests, cross-platform CI,
  OIDC release machinery, and public guides.

### Safety and compatibility

- Same working tree, exclusive provider workspace lease, read-only Git inspection,
  provider-native permissions, and no outgoing model call for handoff/recovery.
- No provider credentials, prompts, rendered handoffs, responses, transcripts,
  full patches, telemetry, self-updater, or cloud account stored/introduced.
- SQLite schema v6, Handoff Protocol v1, and Checkpoint Protocol v1 are preserved.
- Provider CLI behavior remains external; real-provider validation is a separate gate.

### Fixed

- Preserve architectural decisions across handoffs. Handoffs read decisions from the
  newest checkpoint alone, so any later checkpoint recorded without decisions of its
  own — including the session-end checkpoint captured automatically when a provider
  exits — buried every decision recorded during that session, and the package then
  stated that no structured decisions were recorded. `HandoffBuilder` now aggregates
  decisions across the task's whole checkpoint history in chronological first-seen
  order, de-duplicated by exact string equality and scoped strictly to one task.
  Checkpoints remain immutable point-in-time records and are not rewritten;
  `record_decision` additionally carries a structured `trigger="decision"` marker in
  existing checkpoint metadata. Schema v6, Checkpoint Protocol v1, and Handoff
  Protocol v1 are unchanged.
- Bind the managed CortexShift session inside the per-launch MCP configuration.
  A provider CLI spawns the MCP server, and Codex starts MCP servers with a
  sanitized environment, so managed Codex sessions previously saw `session: null`
  and only the four read tools. Unmanaged MCP stays read-only.
- Escape Windows/quoted Python paths in Codex MCP configuration.
- Safely probe existing empty Windows workspace lock files without double-close.
- Close subprocess pipes in test helpers and run fake Python providers portably.

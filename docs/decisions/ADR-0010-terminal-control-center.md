# ADR-0010: Interactive Terminal Control Center (TUI)

- **Status**: Accepted
- **Date**: 2026-09-06
- **Scope**: Phase 9; builds on the application services established in Phases 2–8.

## Context

Through Phase 8, every human-facing interaction with CortexShift was a discrete CLI invocation. Answering "where is this task, what did the last agent leave behind, is the workspace busy, and which agent should take it next?" meant running `status`, `task show`, `repo status`, `session list`, `checkpoint list`, `handoff list`, and `doctor` in sequence and holding the result in your head.

Phase 8 made this sharper rather than softer: an agent can now self-report progress mid-flight through MCP, but a human had no way to *watch* that happen. The canonical state was moving while the operator was blind to it.

Phase 9 introduces a persistent, keyboard-driven terminal dashboard over the state CortexShift already owns. It is a control center, not a new product surface: no new backend, no second persistence system, no replacement for the CLI, and emphatically not a terminal emulator for the coding agents themselves.

## Decisions

### Framework

- **Textual is the TUI framework**, constrained to `textual>=8,<9`. The range is deliberately not pinned to one patch release: CortexShift's other dependencies use compatible ranges, and pinning exactly would create needless upgrade friction without a compelling reason.
- **Only stable, documented Textual 8.x APIs are used**: `App`, `Screen`, `ModalScreen`, `DataTable`, `OptionList`, `Static`, `Input`, `Button`, `Header`, `Footer`, `ContentSwitcher`, `Binding`, `Worker`/`@work`, the built-in command palette `Provider`, and `App.run_test()` with `Pilot` for tests. No private internals are relied upon.
- **`pytest-asyncio` is added as a test-only dependency**, because Textual's `App.run_test()` harness is async. It is not a runtime dependency and adds nothing to an installed CortexShift.

### The TUI is an adapter

- **The dashboard sits beside the CLI and the MCP server, not beneath them.** All three call the same application services:

  ```text
                   ┌──── CLI (Typer / Rich)
                   │
  Application ◄────┼──── MCP (stdio server)
  Services         │
                   └──── TUI (Textual)
                           │
                           ▼
                    presentation state
  ```

- **The TUI contains no SQL, opens no SQLite connection, runs no Git subprocess, and constructs no provider argv.** Task rules stay in `Task` and `TaskService`; recovery stays in `RecoveryService`; handoff generation stays in `SwitchService` and `HandoffBuilder`; provider variance stays behind the provider adapters.
- **`TuiFacade` aggregates use cases and assembles read models.** It holds no business rules. It is bound at construction to a single resolved project root, and every service call it makes passes that root — no dashboard action accepts a project path.
- **Presentation read models are immutable dataclasses** (`TuiStateSnapshot`, `TuiTaskModel`, `TuiRepositoryModel`, `TuiSessionRow`, `TuiCheckpointRow`, `TuiHandoffRow`, `TuiProviderStatus`, `TuiMcpStatus`). They are frozen `dataclasses` rather than Pydantic models because they are never serialized, never validated at a trust boundary, and re-assembled every couple of seconds; they exist to keep screen code free of persistence and inspection details.

### One canonical mark-completed rule

"Mark completed" appends to `completed` (deduplicated) and removes any exactly matching `remaining` entry, without completing the Task itself. This rule previously lived inside the MCP facade. Rather than copy it into the dashboard, it was promoted to `Task.complete_items()` in the domain, and the MCP facade now calls it. The dashboard and MCP agents therefore cannot drift apart. The shared input bounds (`MAX_CURRENT_WORK_CHARS`, `MAX_ITEM_CHARS`, `MAX_ITEMS_PER_CALL`) moved to `domain/task.py` for the same reason.

`TaskWorkspaceService` was added to the application layer so path-addressed callers get Task operations without opening a store themselves. It manages store lifecycle and delegates every rule to `TaskService` and `Task`.

### No new persistence

- **SQLite schema remains at v6.** No migration is introduced.
- **Dashboard state is entirely ephemeral**: last screen, cursor position, sidebar state, and theme are never persisted. Provider discovery results are cached in memory for the life of one dashboard process only, purely to avoid re-probing on every refresh; nothing is written to disk.

### Explicit entrypoint, unchanged CLI

- **`cortexshift tui` is the only way to open the dashboard.** Plain `cortexshift` still prints help, and every existing command keeps its semantics. Making the dashboard the default is a decision for a later release, not this one.
- **The command requires an initialized project first, then a terminal.** Someone standing in the wrong directory is told exactly that, rather than being told about their terminal — the project check runs first deliberately.
- **The real dashboard requires a TTY on both stdin and stdout.** Headless `run_test()`/`Pilot` tests bypass the launcher entirely and are unaffected.

### Refresh model

- **Persisted state refreshes on a lightweight ~2 second timer.** This is SQLite-only: it never runs Git, never probes a provider CLI, and never spawns a subprocess. Its purpose is that an agent self-reporting through MCP shows up in an open dashboard without the operator doing anything.
- **Live Git runs only on startup, on entry to the Repository screen, and on explicit refresh (`r`).** Polling `git status` on a timer would punish large repositories for no benefit; a manual refresh is one keystroke.
- **All slow work runs in Textual thread Workers** — Git inspection, provider discovery, recovery previews, handoff previews, checkpoint creation, MCP setup, and every task mutation. The UI thread never blocks on I/O.
- **Refresh races are resolved by exclusive worker groups plus a generation token.** `exclusive=True` cancels an in-flight refresh in the same group; a monotonically increasing generation, captured before the worker starts and re-checked on the UI thread, discards any late result. A stale inspection can never overwrite newer data.
- **Workers use `exit_on_error=False`.** Expected application errors are routed to notifications; unexpected exceptions are reported *and* logged in full, never silently swallowed.

### No provider TUI embedding

This is the load-bearing rule of the phase.

- **CortexShift never embeds, wraps, scrapes, multiplexes, or emulates a provider's terminal UI.** No pseudo-terminal, no `pexpect`, no `ptyprocess`, no `pyte`, no tmux, no piping a provider TUI into Rich. Phase 9 adds no dependency of that kind, and a regression test asserts none is present.
- **The terminal is released before a provider starts.** Choosing run, resume, or switch exits the Textual application with a structured `TuiExitRequest`. Only after `App.run()` has returned — Textual having torn down and restored the terminal — does `TuiCoordinator` invoke `RunService`, `ResumeService`, or `SwitchService`. The native provider then owns the terminal directly, exactly as it does from the CLI.

  ```text
  Textual App
     │  user chooses Switch → Codex
     ▼
  app.exit(TuiExitRequest(...))
     │
  Textual restores the terminal
     │
     ▼
  TuiCoordinator
     │
     ▼
  SwitchService
     │
     ▼
  Native Codex TUI (owns the terminal)
  ```

- **Returning to the dashboard after the provider exits is not implemented.** `cortexshift tui` ends when the provider ends; the operator runs it again. Automatic re-entry would mean nesting or re-initialising a Textual app around a process that has just had raw control of the terminal, and reliability matters more here than polish.

### Workspace lease

- **The dashboard holds no workspace lease for its lifetime.** An open dashboard must never block a coding agent — that would invert the tool's purpose.
- **Operations that genuinely need exclusivity acquire the lease inside their own services**: run, resume, switch, and recover. The dashboard does not check first and then act; it calls the service and reports what the service decides.
- **The workspace activity indicator is a non-invasive probe.** It attempts a non-blocking acquire of the OS advisory lock and releases it immediately if it succeeds. The presence of `.cortexshift/agent.lock` on disk is never treated as evidence of an active lease, and the operator is never told to delete it.
- **Reading state and cooperative editing keep working while another agent owns the workspace**, matching the Phase 7 rule that milestone checkpoints never contend for the lease.

### Honest authority labelling

Every screen states how much authority its data carries: repository inspection is **Live**; checkpoints and handoffs are **Historical observation**; operator- or agent-supplied test summaries are **Reported / unverified**; an unfinalized session row is **Last-known**, never asserted as crashed. Progress is computed only from structured task items — when `completed + remaining` is zero the dashboard says "No structured progress yet" rather than manufacturing a percentage from Git state or a process exit code.

### Read-only with respect to the repository and source

- **The Repository screen is strictly read-only.** No staging, committing, checkout, reset, or stash. Full diffs are never rendered — only counts, paths, and shortstat summaries.
- **No source editor, file browser with edit, diff editor, commit interface, or shell panel.** Source code belongs to the coding agents and the operator's own tools.
- **No transcripts.** The Sessions screen shows CortexShift orchestration metadata only. Provider conversations, prompts, reasoning, and terminal history are never read or displayed, consistent with the zero-transcript invariant.

### Local terminal only

No Textual Web, no browser serving, no localhost HTTP listener, no remote dashboard, no telemetry. The dashboard is a local terminal program, like the rest of CortexShift.

### Presentation

- Textual CSS in `cortexshift.tcss`: dark terminal-friendly ground, one accent, subtle borders, emphasis reserved for state that needs attention. No animation, no ASCII banners.
- **Meaning is never carried by colour alone.** Every status marker pairs a glyph (`✓`, `!`, `×`, `·`, `?`) with its styling, so the dashboard stays readable where colour rendering is limited.
- **Usable at 80×24**, using the space at 100×30 and 120×40. Below 90 columns the sidebar folds away and navigation continues through the number keys; below 60×12 a clear message replaces the layout rather than a broken render.
- **IDs are abbreviated in tables and shown in full in detail panels**, rendered as plain selectable text so they stay copyable. Service calls always receive the full canonical identifier.

## Alternatives considered

- **`curses` directly** — rejected. It would mean hand-rolling layout, focus, scrolling, event dispatch, resize handling, and colour degradation, plus a separate Windows story. That is a framework's job, and a bespoke one would be worse and unfamiliar to contributors.
- **`prompt_toolkit`** — rejected. Excellent for line editors and REPLs; a dashboard of tables, panels, and modals is not its centre of gravity, and its full-screen layer would need more scaffolding than Textual's for the same result.
- **A custom Rich `Live` UI** — seriously considered, since Rich is already a dependency and would have added nothing new. Rejected because `Live` gives rendering but not an application: no focus model, no key bindings, no modal screens, no widget-level events, no worker integration, and no headless test harness. Phase 9 needs all of those, and building them on top of `Live` would amount to writing a TUI framework inside CortexShift.
- **Embedding provider TUIs via a pseudo-terminal** (`pexpect`/`ptyprocess` + `pyte`) — rejected on principle and on practice. It contradicts the native-agent-first and direct-passthrough invariants, and in practice PTY relaying degrades exactly what makes those agents usable: full-screen redraws, mouse handling, bracketed paste, resize propagation, and colour fidelity. It would also put CortexShift in the position of observing agent conversations, which the zero-transcript invariant forbids. Releasing the terminal is both simpler and more honest.
- **A browser UI (Textual Web, or a localhost server)** — rejected. It would introduce a network listener into a local-first, zero-telemetry tool, and CortexShift's users are already in a terminal, next to the agents.
- **Making `cortexshift` open the dashboard by default** — deferred. Existing CLI semantics are stable and scripted against; changing the default is a release-engineering decision, not a Phase 9 one.

## Consequences

- Operators get one keyboard-driven view of project, task, repository, sessions, checkpoints, handoffs, providers, and MCP status, plus the ability to advance task state, capture checkpoints, run recovery, and launch agents from it.
- A human can now watch an agent's MCP self-reporting land in canonical state in near real time, without any transcript scraping.
- Phase 9 adds one runtime dependency (`textual`) and one test dependency (`pytest-asyncio`).
- The dashboard exits before a provider launches, so an operator returns to a shell after the agent finishes and reruns `cortexshift tui` when they want the dashboard back.
- `RepositoryService` was fixed to close the SQLite connections it opens. The leak predated Phase 9 but was invisible at CLI cadence; a dashboard inspecting on refresh made it consequential.

## References

- [ADR-0005: Native Provider Launch & Session Lifecycle](ADR-0005-native-provider-runtime.md) — direct terminal passthrough and the workspace lease.
- [ADR-0008: Checkpoints, Crash Recovery & Handoff Enrichment](ADR-0008-checkpoint-and-recovery.md) — historical observation semantics and honest reconciliation.
- [ADR-0009: MCP Shared State & Agent Self-Reporting](ADR-0009-mcp-shared-state.md) — the write path whose results this dashboard surfaces.

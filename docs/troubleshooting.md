# Troubleshooting

| Symptom | Next step |
|---|---|
| Provider not found in PATH | Install the native CLI separately, reopen the shell, and run `cortexshift doctor`. |
| Provider unauthenticated | Authenticate through that provider's native CLI; CortexShift cannot log in for you. |
| Project not initialized | Change to the intended project and run `cortexshift init`. |
| No active Task | Run `cortexshift start claude --file prompt.md`, or select an existing Task with `cortexshift task activate ID`. |
| An active Task already exists | Use `cortexshift start claude --task ID` to continue it in a fresh session. Add `--replace-active` to a new-objective command to activate a new Task and keep the previous one. |
| Invalid or missing objective | Supply one UTF-8 file, a UTF-8 stdin stream, or a short nonblank inline objective. Relative file paths are relative to the invocation directory. |
| Piped startup requires an attached terminal | Run from an interactive terminal with terminal stdout. Supply a UTF-8 pipe that closes on completion, or use `--file prompt.md`. |
| Startup failed after saving the Task | Use the printed `cortexshift start <provider> --task ID` command to retry the same Task. If it is no longer active, select it with `cortexshift task activate ID` first. |
| Objective retrieval was incomplete or unobserved | Check the native provider's MCP and trust messages. Retry with `cortexshift resume` after resolving the provider issue. If the original launch never started, use `cortexshift start PROVIDER --task ID`. |
| Objective exceeds the handoff budget | Use `cortexshift switch codex --dry-run` or `cortexshift switch claude --dry-run` to check the paged delivery plan. Antigravity and standalone `handoff preview` require the full objective to fit. |
| Workspace unavailable after provider exit | Let the active provider or known background work finish. Use the job's usual controls if you want to stop it, then retry. Keep `agent.lock` and `provider.lock` in place. |
| Managed MCP writes report expired authority | Use the MCP integration from the current CortexShift `start`, `run`, `resume`, or `switch` invocation. An older binding can serve the existing read tools. |
| Native session unavailable | Run `cortexshift resume` for recent work or `cortexshift resume codex --new-session` for an intentional fresh conversation. Use `cortexshift session list` if you want to choose a specific recorded ID. |
| Destination failed to start | Confirm the provider is installed with `cortexshift doctor` and retry the same switch. CortexShift keeps the Task and records each failed attempt. |
| Antigravity MCP missing | Preview `cortexshift mcp setup antigravity --dry-run`, then run setup. Review conflicts rather than blindly forcing replacement. |
| Git missing | Install Git for repository context. Core init/tasks work without it; repository status honestly reports unavailable context. |
| TUI requires terminal | Use a real terminal with stdin/stdout attached, after initialization. Piped output is not a TUI session. |
| Session has no recorded end | Inspect `cortexshift status` and `cortexshift recover --dry-run`. Run `cortexshift recover` to reconcile eligible sessions once the workspace is free. Investigate any records that recovery reports as uncertain. |
| Database needs migration | Stop active sessions, back up state safely, then rerun `cortexshift init`. Do not delete the database. |

An unfinalized record alone does not prove a crash. Recovery does not undo source
changes, and its checkpoint is a historical observation, not proof of tests.

A provider exit can leave background jobs running. On macOS and Linux CortexShift
blocks transitions for work that still holds the inherited guard. Activity that
closed the descriptor and Windows background activity remain unknown. Use your
terminal or development server controls to check jobs you started before switching.

Report bugs at <https://github.com/batuhanasmakaya/CortexShift/issues>; use the
[security policy](../SECURITY.md) for suspected vulnerabilities instead of a
public issue. Include version, OS, Python version, provider/version, reproduction,
and a sanitized error. Review `doctor` output for local paths. Never attach
credentials, transcripts, a private repository, or unsanitized `.cortexshift` state.

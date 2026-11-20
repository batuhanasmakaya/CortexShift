# Troubleshooting

| Symptom | Next step |
|---|---|
| Provider not found in PATH | Install the native CLI separately, reopen the shell, and run `cortexshift doctor`. |
| Provider unauthenticated | Authenticate through that provider's native CLI; CortexShift cannot log in for you. |
| Project not initialized | Change to the intended project and run `cortexshift init`. |
| No active Task | Run `cortexshift task start --title "Work" --objective "Describe the objective"`, or activate an existing task with `cortexshift task activate ID`. |
| Workspace unavailable after provider exit | Let the active provider or known background work finish. Use the job's usual controls if you want to stop it, then retry. Keep `agent.lock` and `provider.lock` in place. |
| Managed MCP writes report expired authority | Use the MCP integration from the current CortexShift `run`, `resume`, or `switch` invocation. An older binding can serve read tools. |
| Native session unavailable | Inspect `cortexshift session list`. Null IDs cannot resume; deleted native conversations require an intentional new run/switch with `--new-session` on switch. |
| Antigravity MCP missing | Preview `cortexshift mcp setup antigravity --dry-run`, then run setup. Review conflicts rather than blindly forcing replacement. |
| Git missing | Install Git for repository context. Core init/tasks work without it; repository status honestly reports unavailable context. |
| TUI requires terminal | Use a real terminal with stdin/stdout attached, after initialization. Piped output is not a TUI session. |
| Unfinalized session after crash | Inspect `cortexshift recover --dry-run`; when no agent is active, run `cortexshift recover`. |
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

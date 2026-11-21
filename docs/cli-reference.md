# CLI reference

Run `cortexshift --help` for the full command list and `cortexshift <command> --help`
for flags and examples. Initialize the intended workspace once with
`cortexshift init`.

## Start work

```text
cortexshift start PROVIDER (--file PATH | --stdin | --objective TEXT) [--replace-active]
cortexshift start PROVIDER --task ID
```

`PROVIDER` is `claude` or `codex`. Supply exactly one option from `--file`,
`--stdin`, `--objective`, or `--task`. The command starts from the initialized
workspace found at or above the current directory.

```bash
cortexshift start claude --file "prompts/implement authentication.md"
cortexshift start codex --file prompt.md
cat prompt.md | cortexshift start claude --stdin
cortexshift start claude --objective "Implement authentication"
```

- `--file` reads UTF-8 Markdown from a relative or absolute path. Relative paths
  are resolved from the directory where you invoke the command. Files can live
  outside the workspace. Line endings, indentation, and trailing whitespace are
  stored with the complete canonical objective.
- `--stdin` reads a redirected UTF-8 stream through EOF, then opens the attached
  terminal for native interactive input. Run it from a terminal with terminal
  stdout. In Windows PowerShell, `--file` reads the original bytes directly and
  avoids the shell's pipeline encoding settings.
- `--objective` takes a shell argument for short text. Your shell and OS cap
  command-line size before CortexShift can read it. Use a file or stdin for long
  objectives.

CortexShift derives a short title from the first nonblank line and creates and
activates the Task together. An existing active Task blocks creation unless you
pass `--replace-active`. This changes the active pointer and keeps the previous
Task and its history. To open an existing Task with the same automatic context,
use `--task ID` with the active ID from `cortexshift task list`. If a launch fails
after creation, the error includes the command to retry that same Task.

The native provider receives a short instruction to retrieve the objective in
pages through invocation-bound MCP. The terminal reports whether CortexShift
served all, some, or none of the objective through the startup tool. This receipt
records transport activity. Provider trust and permission choices still apply.
Rendered switch handoffs retain the 48,000 UTF-8 byte limit.

## Manage and inspect work

| Command | Purpose |
|---|---|
| `cortexshift task start --title "Work" --objective "Describe the objective"` | Create a Task separately. Add `--requirement` or `--constraint` for structured fields. |
| `cortexshift task list` / `cortexshift task activate ID` | Find and select an existing Task. |
| `cortexshift task complete` | Complete the current Task and clear its active pointer. |
| `cortexshift run PROVIDER` | Open the native provider on the active Task. The separate workflow also supports `antigravity`. |
| `cortexshift handoff preview PROVIDER` / `cortexshift switch PROVIDER` | Preview a bounded historical handoff or switch the active Task to another provider. |
| `cortexshift resume PROVIDER` | Resume an eligible native session with a known exact ID. |
| `cortexshift session list` / `cortexshift mcp status` | Inspect Session history and MCP integration. |
| `cortexshift recover --dry-run` / `cortexshift recover` | Preview or reconcile eligible interrupted work. |

See [Provider Support](provider-support.md) for adapter capabilities and
[Troubleshooting](troubleshooting.md) for common startup failures.

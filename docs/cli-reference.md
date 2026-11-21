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
| `cortexshift resume [PROVIDER]` | Continue recent work or request an exact resume for a specific provider. |
| `cortexshift session list` / `cortexshift mcp status` | Inspect Session history and MCP integration. |
| `cortexshift recover --dry-run` / `cortexshift recover` | Preview or reconcile eligible interrupted work. |

## Switch providers

```text
cortexshift switch PROVIDER [--from-session ID] [--note TEXT] [--new-session | --resume-session ID]
cortexshift switch PROVIDER --dry-run [--json]
```

Switch uses the verified workspace and active, nonterminal Task. The newest
Session whose launch did not fail is the default source, with the newest
recorded Session as the fallback when every launch failed. Use `--from-session`
to select an earlier source on that Task. CortexShift takes the workspace lease
before settling the source and preparing the destination. It includes the
latest Task checkpoint and any distinct historical decisions and test evidence
recorded for that Task.

A return to a provider uses its newest eligible exact conversation on the Task
when the adapter supports a fresh handoff for it. Use `--new-session` to start a
new native conversation or `--resume-session ID` to pin a prior destination.
A same-provider switch directs you to `cortexshift resume`. Switch leaves the
canonical Task, objective, and progress intact.

Claude Code retrieves the prepared handoff through managed MCP. Codex and
Antigravity send the bounded handoff in a read-only bootstrap turn over stdin.
Claude Code and Codex also receive a short interactive instruction to retrieve
all objective pages, current Task fields, and historical decisions and evidence.
For these managed targets, an objective that exceeds the rendered budget is
explicitly marked for MCP retrieval. The full historical payload remains
available through `cortexshift handoff show ID --json`.

## Resume recent work

```text
cortexshift resume [PROVIDER] [--session ID | --new-session] [--dry-run [--json]]
```

- `cortexshift resume` selects the latest meaningful Session on the active Task.
  Sessions are ordered by start time, with the most recently inserted record
  first for equal timestamps. When that Session has an eligible native ID,
  CortexShift requests exact native resume. Otherwise it can open a fresh
  Claude Code or Codex conversation with the same Task.
- `cortexshift resume claude` requests the newest eligible exact Claude Code
  conversation on the active Task. A provider argument preserves exact-only
  behavior unless `--new-session` is set.
- `cortexshift resume --session ID` requests that exact Session and gets the
  provider from its record. If you also supply a provider, it must match. Use
  `cortexshift session list` to find IDs. The source must belong to the active Task.
- `cortexshift resume codex --new-session` opens fresh Task context after prior
  managed Codex work. Use `cortexshift start codex --task ID` for an active Task
  without prior Codex work.
- A Session without a recorded end calls for `cortexshift recover --dry-run`
  once the workspace is free. After recovery, a reconciled Session without
  recorded process-exit evidence supports fresh continuation where available.

Resume always creates a new CortexShift Session. Exact resume links its source
Session and sends the recorded ID to the provider. A provider can reject an
expired or deleted native conversation. In that case, use `--new-session` to
request a fresh conversation where supported.

`cortexshift status` shows the last recorded Session on the active Task,
workspace lock activity, and unfinalized Session counts. Handoff and objective
receipts describe transport activity. A completed bootstrap records the
provider's completion report. MCP receipts record the data CortexShift served.
Use the dry-run commands with `--json` for machine-readable plans.

See [Provider Support](provider-support.md) and [Troubleshooting](troubleshooting.md).

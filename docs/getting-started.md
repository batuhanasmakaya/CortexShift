# Getting started

Use Python 3.12–3.14. Install Git and the native coding agents you plan to use
separately, and authenticate through their own CLIs.

## Install

After publication, `pipx install cortexshift` is preferred: it isolates application
dependencies while exposing the command globally. Upgrade with
`pipx upgrade cortexshift`; remove the application with `pipx uninstall cortexshift`.
Removing the application does not remove project history.

Before publication, from a clone of the canonical repository
(<https://github.com/batuhanasmakaya/CortexShift>):

```bash
git clone https://github.com/batuhanasmakaya/CortexShift.git
cd CortexShift
uv sync --locked
uv build
pipx install dist/cortexshift-0.1.0-py3-none-any.whl
```

Alternatively create a virtual environment:

```bash
python -m venv .venv
# POSIX shells:
. .venv/bin/activate
# Windows PowerShell instead: .venv\Scripts\Activate.ps1
python -m pip install cortexshift
```

That last command requires PyPI publication; before then give pip the local
wheel path. Use `python -m pip install --upgrade cortexshift` to upgrade and
`python -m pip uninstall cortexshift` to remove it. Never use sudo pip.

## Create a task and work

```bash
cd my-project
cortexshift doctor
cortexshift init
echo "Preserve existing APIs while adding authentication." > prompt.md
cortexshift start claude --file prompt.md
```

`start` also accepts `codex`. The native session receives an instruction to
retrieve the objective and Task fields through CortexShift MCP. Keep your
provider's normal trust and permission settings. The [CLI reference](cli-reference.md)
describes input options and recovery when launch or retrieval fails.

`doctor` checks availability, version, and authentication where known without
sending prompts. `cortexshift mcp status` shows integration details. Initialization
works without Git. Add `.cortexshift/` to your project's `.gitignore` if needed.

## Create and run in separate steps

Use the separate commands to choose a title, provide structured requirements
and constraints up front, or launch Antigravity after configuring its workspace MCP.

```bash
cortexshift task start --title "Add authentication" \
  --objective "Preserve existing APIs while adding authentication."
cortexshift run claude
```

For Claude Code or Codex, use `cortexshift start <provider> --task ID` to open an
existing active Task with automatic context retrieval. Find its ID with
`cortexshift task list`.

## Record a milestone and switch

In another terminal during work, or after the provider exits:

```bash
cortexshift task update --work "Checking API compatibility"
cortexshift checkpoint create -d "Keep existing API signatures" \
  -t "Targeted tests passed (reported, not independently verified)"
```

After the running provider exits:

```bash
cortexshift switch codex
```

Actual Codex/Antigravity handoffs consume one bootstrap model turn and may
consume further interactive usage. `cortexshift switch codex --dry-run` previews
the destination and context budget without model calls or saved records.

After the native agent exits, run `cortexshift resume` to continue recent work
or `cortexshift switch claude` to return to Claude Code. To pick an earlier
conversation, use `cortexshift session list` and `cortexshift resume --session ID`.
If the recorded conversation is unavailable, `cortexshift resume codex --new-session`
opens a fresh Codex conversation with the active Task.

## Recover and inspect

After a hard crash, inspect `cortexshift recover --dry-run`, then run
`cortexshift recover` when the workspace lease and retained provider guard are
clear. Recovery examines unfinished Sessions across the project. It records a
checkpoint per affected Task and leaves Sessions with uncertain ownership for
operator inspection. Recovery preserves source files and keeps unobserved
process end times empty. Keep `agent.lock` in place.

```bash
cortexshift status
cortexshift checkpoint latest --json
cortexshift session list
cortexshift tui
```

The dashboard requires an initialized project and a real terminal. `?` opens
keyboard help. It closes before handing control to a provider.

For Antigravity, configure workspace MCP explicitly with
`cortexshift mcp setup antigravity --dry-run`, then
`cortexshift mcp setup antigravity`. Review the generated workspace config;
unrelated servers are preserved, and conflicting entries are refused.

## Preserve your history

State lives in `.cortexshift/state.sqlite3`. For a simple file backup, stop all
CortexShift/provider/MCP processes first and back up the state directory together;
SQLite may have WAL sidecars. Do not sync or copy a live WAL database as though
its main file were a complete backup. Upgrades retain state; `cortexshift init`
applies supported forward migrations transactionally when needed.

See [Troubleshooting](troubleshooting.md) and [Provider Support](provider-support.md).

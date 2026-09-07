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
cortexshift task start --title "Add authentication" \
  --objective "Preserve existing APIs while adding authentication."
cortexshift run claude
```

`doctor` safely reports availability, version, and authentication where known;
it does not send prompts. `cortexshift mcp status` shows integration details.
Initialization does not require Git. Add `.cortexshift/` to your project's
`.gitignore` yourself if needed.

## Record a milestone and switch

In another terminal during work, or after the provider exits:

```bash
cortexshift task update --work "Checking API compatibility"
cortexshift checkpoint create -d "Keep existing API signatures" \
  -t "Targeted tests passed (reported, not independently verified)"
```

After the running provider exits:

```bash
cortexshift handoff preview codex
cortexshift switch codex --dry-run
cortexshift switch codex
```

Actual Codex/Antigravity handoffs consume one bootstrap model turn and may
consume further interactive usage. Preview/dry-run are free of model calls.
Return with `cortexshift switch claude`; exact native resume without a handoff
is available through `cortexshift resume claude` when an eligible ID exists.

## Recover and inspect

After a hard crash, inspect `cortexshift recover --dry-run`, then run
`cortexshift recover` when no provider holds the workspace lease. Recovery
reconciles unfinalized records and checkpoints live state; it does not invent
an end time or restore source files. Never delete `agent.lock` to bypass a lock.

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

# CortexShift

**Switch agents. Keep the context.**

CortexShift keeps development tasks intact as you move between Claude Code,
Codex, and Antigravity in the same local repository. A Task belongs to CortexShift;
providers are workers over that Task. Switching does not require the outgoing
agent to answer, summarize, or even remain installed.

**0.1.0 is an initial public alpha release candidate. Publication is pending.**
Python 3.12–3.14 is targeted. Automated cross-platform coverage is configured;
real provider and platform validation has a separate maintainer checklist.

## How it works

CortexShift stores objectives, requirements, progress, checkpoints, and session
metadata in project-local SQLite. It combines that structured state with live
Git inspection to prepare a canonical handoff. A conversation transcript is not
canonical project state: receiving agents must verify the files and run tests.

Authority flows from live repository files → live Git → verified evidence →
canonical Task state → historical observations. Recorded completion is a report,
not proof; checkpoints never establish that tests passed independently.

## Capabilities

- Persistent tasks, provider session history, and manual agent switching.
- Exact native resume when an ID is known; fresh canonical context on return.
- Cooperative checkpoints and deterministic crash recovery.
- MCP shared state over local stdio: agents can read context and report progress.
- A keyboard-driven terminal control center for tasks, repository state, and history.
- Read-only Git inspection and an OS workspace lock for exclusive provider runs.

## Quick start

After PyPI publication:

```bash
pipx install cortexshift
cd my-project
cortexshift doctor
cortexshift init
cortexshift task start --title "Implement authentication" \
  --objective "Add authentication without breaking existing APIs."
cortexshift run claude
# After the provider exits:
cortexshift switch codex
cortexshift tui
```

CortexShift does not bundle Git or any provider CLI. Install and authenticate
native providers yourself. Git is recommended for repository-aware handoffs,
but project initialization also works without Git.

## Installation and updates

[pipx](https://pipx.pypa.io/stable/) gives a global command with isolated Python
dependencies. Use Python 3.12 or newer. Once published:

```bash
pipx upgrade cortexshift
pipx uninstall cortexshift
```

Before publication, from this checkout use `uv sync --locked`, then
`uv run cortexshift --help`. To install a candidate globally without an editable
checkout: `uv build`, then
`pipx install dist/cortexshift-0.1.0-py3-none-any.whl`.
A virtualenv/pip alternative is in [Getting Started](docs/getting-started.md).
Homebrew is pending publication of a custom tap.

## Your first handoff

```bash
cortexshift checkpoint create -d "Preserve existing API compatibility" \
  -t "Targeted tests passed (operator report)"
cortexshift handoff preview codex
cortexshift switch codex --dry-run
cortexshift switch codex
```

Preview and dry-run launch no model and persist nothing. Actual Codex and
Antigravity handoffs each use one read-only bootstrap model turn before native
resume, which may consume provider usage. Claude receives context directly.

## Terminal control center

Run `cortexshift tui` in an initialized project and a real terminal. Use `1`–`7`
to navigate, `r` to refresh, `c` to checkpoint, `x` for provider actions, and `?`
for help. The dashboard releases terminal ownership before launching a native
provider. After that provider exits, run `cortexshift tui` again.
An open dashboard holds no workspace lease and may observe a running agent.

## MCP and providers

Claude and Codex receive MCP configuration automatically. Antigravity requires
explicit workspace setup: `cortexshift mcp setup antigravity`.
Inspect integration with `cortexshift mcp status`. Managed sessions expose
10 context-bound tools; unmanaged/read-only sessions expose four read tools.
See [Provider Support](docs/provider-support.md) for identity, resume, and
validation limits.

## Privacy and trust

CortexShift has no cloud account, telemetry, or paid model API key requirement.
It does not copy provider credentials or persist prompts, provider responses,
transcripts, or full Git patches. Native provider permissions remain authoritative.
Providers may send repository/context data to their own services under their
configuration and terms; local orchestration does not change that behavior.

Task text, paths, and project history are local development data. Generally ignore
`.cortexshift/` in Git; CortexShift does not rewrite your `.gitignore`.
See [Security](SECURITY.md) for boundaries and safe reporting.

## Limitations

External provider CLIs can change. Legacy or plain Codex/Antigravity runs may
have no native ID; those sessions cannot be exact-resumed. Deleted native
conversations are not silently replaced. There is no automatic quota switching,
embedded provider TUI, GUI, cloud agent, secret manager, or terminal multiplexer.
Provider accounts, subscriptions, and usage costs are governed by each provider.
Internal Python modules are not a stable library API.

## Documentation and development

- [Getting Started](docs/getting-started.md)
- [Provider Support](docs/provider-support.md)
- [Architecture](docs/architecture.md) and [Handoff Protocol](docs/handoff-protocol.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Contributing](CONTRIBUTING.md), [Releasing](docs/releasing.md), and [Roadmap](docs/roadmap.md)

```bash
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
```

## License

[MIT](LICENSE). CortexShift is an independent open-source project and is not
affiliated with or endorsed by Anthropic, OpenAI, or Google.

# CortexShift

**Switch agents. Keep the context.**

CortexShift keeps development tasks intact as you move between Claude Code,
Codex, and Antigravity in the same local repository. A Task belongs to CortexShift;
providers are workers over that Task. Switching does not require the outgoing
agent to answer, summarize, or even remain installed.

**0.1.0 is an initial public alpha release candidate. PyPI publication is pending.**
The canonical repository is <https://github.com/batuhanasmakaya/CortexShift>.
Python 3.12–3.14 is targeted, with required Linux/macOS/Windows CI on protected
`main`. Real end-to-end validation is complete for Claude Code and Codex;
Antigravity has automated coverage only, and interactive platform smokes remain
on a separate maintainer checklist.

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
- Read-only Git inspection and an OS workspace lease for managed provider runs.

## Quick start

After PyPI publication:

```bash
pipx install cortexshift
cd my-project
cortexshift doctor
cortexshift init
echo "Add authentication without breaking existing APIs." > prompt.md
cortexshift start claude --file prompt.md
```

CortexShift saves the Markdown as the active Task and gives the native agent an
initial instruction to retrieve it through managed MCP. Use `codex` in place of
`claude` to start with Codex. See the [CLI Reference][cli-reference] for piped
input, inline objectives, and the separate `task start` and `run` workflow.

CortexShift does not bundle Git or any provider CLI. Install and authenticate
native providers yourself. Git is recommended for repository-aware handoffs,
but project initialization also works without Git.

If a workspace is moved or copied with its `.cortexshift` directory, CortexShift
checks the recorded project path before using that state. A mismatch stops the
command and shows both locations so you can confirm which workspace to use.

## Installation and updates

[pipx](https://pipx.pypa.io/stable/) gives a global command with isolated Python
dependencies. Use Python 3.12 or newer. Once published:

```bash
pipx upgrade cortexshift
pipx uninstall cortexshift
```

Before publication, clone the repository and use `uv sync --locked`, then
`uv run cortexshift --help`:

```bash
git clone https://github.com/batuhanasmakaya/CortexShift.git
cd CortexShift
```

To install a candidate globally without an editable checkout: `uv build`, then
`pipx install dist/cortexshift-0.1.0-py3-none-any.whl`.
A virtualenv/pip alternative is in [Getting Started][getting-started].
Homebrew is pending publication of a custom tap.

## Switch and resume

```bash
cortexshift checkpoint create -d "Preserve existing API compatibility" \
  -t "Targeted tests passed (operator report)"
cortexshift switch codex
cortexshift resume
cortexshift switch claude
```

Exit the native agent before each switch or resume. Bare `resume` follows the
most recent managed Session on the active Task. Claude Code and Codex can open
a fresh conversation with automatic Task context when the selected Session has
no eligible native ID. Use `cortexshift resume claude --session ID` to request
an exact earlier conversation, or `cortexshift resume codex --new-session` to
continue in a fresh conversation deliberately.

`cortexshift switch codex --dry-run` previews the switch without launching a
provider or saving records. Actual Codex and Antigravity switches use one
read-only bootstrap turn and may consume provider usage. Switches to Claude
Code and Codex automatically request the full objective through MCP, including
when the 48,000 UTF-8 byte handoff uses an excerpt.

## Terminal control center

Run `cortexshift tui` in an initialized project and a real terminal. Use `1`–`7`
to navigate, `r` to refresh, `c` to checkpoint, `x` for provider actions, and `?`
for help. The dashboard releases terminal ownership before launching a native
provider. After that provider exits, run `cortexshift tui` again.
An open dashboard holds no workspace lease and may observe a running agent.

## MCP and providers

Claude and Codex receive MCP configuration automatically. Antigravity requires
explicit workspace setup: `cortexshift mcp setup antigravity`.
Inspect integration with `cortexshift mcp status`. Live managed launches with
automatic Task context expose the paged objective and Task history tools.
Unmanaged and expired bindings expose four basic read tools at startup.
See [Provider Support][provider-support] for identity, resume, and
validation limits.

## Privacy and trust

CortexShift has no cloud account, telemetry, or paid model API key requirement.
Objective input is kept in local canonical Task state. CortexShift does not copy
provider credentials or persist rendered provider prompts, provider responses,
transcripts, or full Git patches. Native provider permissions remain authoritative.
Providers may send repository/context data to their own services under their
configuration and terms; local orchestration does not change that behavior.

Task text, paths, and project history are local development data. Generally ignore
`.cortexshift/` in Git; CortexShift does not rewrite your `.gitignore`.
See [Security][security-policy] for boundaries and safe reporting.

## Limitations

External provider CLIs can change. Fresh interactive Codex and Antigravity runs
may have no recorded native ID. An exact resume requires a recorded eligible ID.
There is no automatic quota switching,
embedded provider TUI, GUI, cloud agent, secret manager, or terminal multiplexer.
Provider accounts, subscriptions, and usage costs are governed by each provider.
Internal Python modules are not a stable library API.

## Documentation and development

- [Getting Started][getting-started], [CLI Reference][cli-reference], and [Troubleshooting][troubleshooting]
- [Provider Support][provider-support]
- [Architecture][architecture] and [Handoff Protocol][handoff-protocol]
- [Contributing][contributing], [Releasing][releasing], and [Roadmap][roadmap]
- [Changelog][changelog] and [Issues][issues]

```bash
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
```

## License

[MIT][license]. CortexShift is an independent open-source project and is not
affiliated with or endorsed by Anthropic, OpenAI, or Google.

[getting-started]: https://github.com/batuhanasmakaya/CortexShift/blob/main/docs/getting-started.md
[cli-reference]: https://github.com/batuhanasmakaya/CortexShift/blob/main/docs/cli-reference.md
[provider-support]: https://github.com/batuhanasmakaya/CortexShift/blob/main/docs/provider-support.md
[architecture]: https://github.com/batuhanasmakaya/CortexShift/blob/main/docs/architecture.md
[handoff-protocol]: https://github.com/batuhanasmakaya/CortexShift/blob/main/docs/handoff-protocol.md
[troubleshooting]: https://github.com/batuhanasmakaya/CortexShift/blob/main/docs/troubleshooting.md
[contributing]: https://github.com/batuhanasmakaya/CortexShift/blob/main/CONTRIBUTING.md
[releasing]: https://github.com/batuhanasmakaya/CortexShift/blob/main/docs/releasing.md
[roadmap]: https://github.com/batuhanasmakaya/CortexShift/blob/main/docs/roadmap.md
[changelog]: https://github.com/batuhanasmakaya/CortexShift/blob/main/CHANGELOG.md
[security-policy]: https://github.com/batuhanasmakaya/CortexShift/blob/main/SECURITY.md
[license]: https://github.com/batuhanasmakaya/CortexShift/blob/main/LICENSE
[issues]: https://github.com/batuhanasmakaya/CortexShift/issues

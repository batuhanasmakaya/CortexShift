# Provider support

CortexShift orchestrates native CLIs; it neither bundles nor authenticates them.
Run `cortexshift doctor` and `cortexshift mcp status` to inspect your environment.
The table describes implemented adapter contracts; see Validation status below for
what has actually been exercised against real, authenticated provider CLIs.

| Capability | Claude Code (`claude`) | Codex (`codex`) | Antigravity (`agy`) |
|---|---|---|---|
| Passive discovery/version | Yes | Yes | Yes |
| Passive authentication | Native auth status | Native login status | Unknown |
| Interactive run | Direct terminal | Direct terminal | Direct terminal |
| Automatic Task context with `start` | Native initial instruction and managed MCP | Native initial instruction and managed MCP | Unsupported |
| Canonical handoff | Invocation-bound MCP retrieval | Read-only stdin bootstrap with JSONL output, then resume | Plan-mode stream-JSON bootstrap over stdin, then resume |
| Full objective on switch | Paged managed MCP | Paged managed MCP in the interactive session | Full objective must fit in the handoff |
| Managed native identity | Preallocated UUID on start/run/switch | ID captured during managed handoff | ID captured during managed handoff |
| Exact resume | Known ID and automatic Task refresh | Known ID and automatic Task refresh | Known ID with native context |
| Fresh continuation with `resume` | Automatic Task context | Automatic Task context | Use the manual `run` workflow |
| Return to provider | Same ID + fresh handoff | Same ID + fresh bootstrap | Same ID + fresh plan bootstrap |
| MCP | Automatic inline configuration | Automatic config overrides | Explicit workspace setup |
| Bootstrap model cost | None | One turn per handoff | One turn per handoff |

Legacy null IDs stay unknown. Plain Codex and Antigravity runs may have no recorded
ID. No transcript/cache inspection or “last session” guessing is used. Native
conversations can be deleted outside CortexShift; a stale ID fails without
silently substituting a new conversation. Every resume records a new invocation.

Claude Code supplies a fresh UUID with `--session-id` and requests exact resume
with `--resume ID`. Claude Code and Codex accept a short interactive instruction
when resuming. It requests the full paged objective, current Task fields, and
historical context through the managed MCP binding. Native trust and permissions
govern retrieval. [Claude Code CLI reference](https://code.claude.com/docs/en/cli-reference)

Codex switch captures `thread.started.thread_id` from `codex exec --json` and
requires a successfully completed read-only turn before opening
`codex resume ID`. A returning switch checks that the emitted ID matches the
requested thread. Fresh interactive launches keep the native ID unknown. Codex
CLI 0.153.4 help exposes `codex resume [SESSION_ID] [PROMPT]` and reserves JSONL
events for `exec`. The documented `SessionStart` hook includes a session ID
and requires hook review and trust. CortexShift keeps that trust boundary and
uses Task continuation for fresh interactive sessions without an ID.
[Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode) ·
[Codex hooks](https://learn.chatgpt.com/docs/hooks)

Antigravity captures the exact conversation ID from a successful plan-mode
switch bootstrap. Plain `resume` with an eligible Antigravity ID reopens that
conversation with its existing native context. New Task context can be sent
when switching from another provider. Antigravity also supports the separate
`task start` and `run antigravity` commands.
Use `cortexshift mcp setup antigravity` to prepare `.agents/mcp_config.json`.
Workspace MCP setup provides the basic read tools. Automatic per-invocation
context refresh and full-objective paging are unavailable for this adapter.
[Antigravity headless mode](https://antigravity.google/docs/cli/headless/)

## Validation status

Three distinct levels of evidence are tracked separately, and they are not
interchangeable.

**Automated coverage.** Deterministic fake-provider tests cover all three
adapters, including the flagship multi-provider handoff, resume, checkpoint, and
MCP workflows. These run in CI on Linux, macOS, and Windows across CPython
3.12–3.14 and consume no model quota. Fake-provider coverage proves CortexShift's
own logic, not a native CLI's behavior.

The authenticated checks below predate the bounded handoff transport, `start`,
and DX2. Automated coverage uses real local MCP stdio for startup, switching,
and resume, including retrieval of an objective larger than the handoff budget.

**Prior authenticated validation: Claude Code and Codex.** Both were exercised against
authenticated, installed CLIs by a maintainer. In each case the managed MCP
session binding was confirmed, the managed write tools were available and usable,
checkpoint provenance was bound to the managed session, the native session ID was
captured, native resume worked, and CortexShift session lineage was confirmed. For
Claude Code all 10 MCP tools were visible and all 6 write tools usable; for Codex
the native ID was captured through the switch/bootstrap path. A real
Claude → Codex `cortexshift switch codex` delivered the current task, objective,
current work, previous provider/session, and a structured decision correctly, and
the receiving Codex obtained a new managed CortexShift session. A decision recorded
in an earlier checkpoint also survived a later empty manual checkpoint and the
automatic session-end checkpoint, while later checkpoints stayed literal
point-in-time observations rather than copying history forward.

The versions actually exercised were **Claude Code 2.1.204** and **Codex 0.153.4**
(the environment's installed Codex 0.144.3 was updated because the selected model
required a newer CLI). Those are the versions validated, not a compatibility
guarantee: other or future provider CLI versions may change flags, output, or
session semantics and may require adapter updates and fresh validation.

**Real end-to-end not performed: Antigravity.** Antigravity is not installed in
the validation environment, so no real Antigravity run, handoff, resume, or
workspace MCP setup has been exercised. Automated fake-provider coverage remains
the only validation basis for that adapter, and its real-world behavior is
unverified.

Remaining real-provider and platform checks stay a deliberate maintainer gate in
[Releasing](releasing.md). No real model quota is consumed by CI.

Run `uv run python scripts/provider_preflight.py` for bounded version/help-only
contract inspection. Missing providers are reported as unavailable; incompatible
installed help surfaces fail the check. Help presence alone cannot prove runtime
behavior or authentication.

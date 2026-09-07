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
| Canonical handoff | Direct context | Read-only JSONL bootstrap, then resume | Plan-mode JSON bootstrap, then resume |
| Managed native identity | Preallocated UUID on run/switch | ID captured during managed handoff | ID captured during managed handoff |
| Exact resume | Known ID required | Known ID required | Known ID required |
| Return to provider | Same ID + fresh handoff | Same ID + fresh bootstrap | Same ID + fresh plan bootstrap |
| MCP | Automatic inline configuration | Automatic config overrides | Explicit workspace setup |
| Bootstrap model cost | None | One turn per handoff | One turn per handoff |

Legacy null IDs stay unknown. Plain Codex and Antigravity runs may have no recorded
ID. No transcript/cache inspection or “last session” guessing is used. Native
conversations can be deleted outside CortexShift; a stale ID fails without
silently substituting a new conversation. Every resume records a new invocation.

Antigravity setup uses `cortexshift mcp setup antigravity` to prepare
`.agents/mcp_config.json`. Native CLI changes may require adapter updates.
CortexShift never bypasses native permissions or embeds provider terminal UIs.

## Validation status

Three distinct levels of evidence are tracked separately, and they are not
interchangeable.

**Automated coverage.** Deterministic fake-provider tests cover all three
adapters, including the flagship multi-provider handoff, resume, checkpoint, and
MCP workflows. These run in CI on Linux, macOS, and Windows across CPython
3.12–3.14 and consume no model quota. Fake-provider coverage proves CortexShift's
own logic, not a native CLI's behavior.

**Real end-to-end validated: Claude Code and Codex.** Both were exercised against
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

# Provider support

CortexShift orchestrates native CLIs; it neither bundles nor authenticates them.
Run `cortexshift doctor` and `cortexshift mcp status` to inspect your environment.
The table describes implemented adapter contracts, not a claim of real E2E validation.

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

Deterministic fake-provider tests cover all three adapters. On 2026-09-06,
passive local inspection found Claude 2.1.204 and Codex 0.144.3 with the required
help surfaces; Antigravity was not installed. This is not real-provider E2E
validation. All real run/handoff/resume/MCP checks remain a deliberate maintainer
gate in [Releasing](releasing.md). No real model quota is consumed by CI.

Run `uv run python scripts/provider_preflight.py` for bounded version/help-only
contract inspection. Missing providers are reported as unavailable; incompatible
installed help surfaces fail the check. Help presence alone cannot prove runtime
behavior or authentication.

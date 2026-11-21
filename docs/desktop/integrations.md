# External agent integration research

Documentation reviewed November 21, 2026. The matrix describes vendor capabilities for evaluating DX3B. DX3A reads local CortexShift records. Provider authentication and runtime actions remain with the existing CLI.

| Surface | External application or native handoff | Embedded integration | Context-only integration |
| --- | --- | --- | --- |
| Claude Code CLI | Documented terminal resume. Claude Code 2.1.285+ supports `claude --desktop --resume SESSION_ID` for eligible closed local Sessions on macOS and x64 Windows with Claude subscription sign-in. | Claude Agent SDK exposes its own agent lifecycle, controls, and Session APIs. | Official CLI and SDK MCP configuration. |
| Claude Desktop | Can reopen an eligible CLI Session via the CLI handoff or the local Desktop `/resume` picker. General control over existing conversations is unconfirmed. | Claude Agent SDK drives the integrating application's own Sessions. | Desktop supports MCP under its own configuration and permissions. |
| Codex CLI | `codex resume` supports recorded native Session IDs. The stable `codex app PATH` command launches the app. | App Server has a documented JSONL protocol over stdio with initialization, thread and turn APIs, events, and approvals. The CLI reference marks the command experimental. OpenAI does not support it for production workloads. | Codex MCP configuration can expose approved local tools and resources. |
| Codex Desktop | `codex app PATH` opens the workspace on macOS. On Windows the command launches the app and prints the workspace path. General third-party control of already-running app conversations is unconfirmed. | Evaluate a separately owned App Server process with stdio and version negotiation. | Use documented Codex MCP settings with explicit context authorization. |
| Google Antigravity | Official Antigravity CLI advertises interactive resume. A supported handoff into an already-running Antigravity App conversation is unconfirmed. | Google documents `agy acp` with editor Session lifecycle support and a separate Antigravity SDK. Treat these as candidates for new adapter validation. | Google documents local MCP for the App, IDE and CLI. CortexShift's established Antigravity setup remains context-only where configured. |

Claude Agent SDK integrations use Anthropic-supported API-key or cloud-provider authentication and the applicable API or cloud billing. Claude.ai login and subscription rate limits for third-party products require Anthropic approval. The documented native Desktop handoff has different authentication eligibility. [Claude Code Desktop](https://code.claude.com/docs/en/desktop), [Claude Code CLI](https://code.claude.com/docs/en/cli-reference), [Claude Agent SDK authentication](https://code.claude.com/docs/en/agent-sdk/overview), [Anthropic API pricing](https://platform.claude.com/docs/en/about-claude/pricing)

Codex integration work should pin a tested CLI/App Server compatibility range. Prefer private stdio for embedded clients. Network transport and experimental methods require separate evaluation. A launched UI does not transfer managed CortexShift process ownership. [Codex CLI reference](https://learn.chatgpt.com/docs/cli/reference), [Codex App Server](https://learn.chatgpt.com/docs/app-server), [Codex MCP](https://learn.chatgpt.com/docs/extend/mcp)

Antigravity mechanisms have their own install, auth, protocol, and host requirements. Verify supported CLI identity and behavior against the actual installed provider before extending CortexShift's existing adapter. [Antigravity CLI](https://antigravity.google/docs/cli-features), [ACP](https://antigravity.google/docs/ide/extensions/acp), [Antigravity SDK](https://antigravity.google/docs/sdk/overview), [Antigravity MCP](https://antigravity.google/docs/mcp)

## DX3B scope

1. Add visual Task creation and selection backed by the canonical application services.
2. Add managed startup, switch, and resume through provider adapters and the existing exclusive workspace lease.
3. Expose invocation lineage, MCP retrieval and handoff delivery receipts with the same evidence rules as the CLI.
4. Validate native Claude Desktop handoff and Codex app launch against supported versions and platform/auth requirements.
5. Prototype App Server over stdio and evaluate Claude Agent SDK and Antigravity ACP separately for embedded operation. Keep context authorization invocation-scoped across all hosts.

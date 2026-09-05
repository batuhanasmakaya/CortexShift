# ADR-0002: Safe, Passive Native Provider Discovery and Diagnostics

- **Status**: Accepted
- **Date**: 2026-09-05
- **Deciders**: CortexShift Core Architecture Team

---

## Context

To orchestrate transitions across multiple coding agents, CortexShift needs to discover which native coding agent command-line interfaces (CLIs) are installed, executable, and authenticated on the developer's local machine.

Initial target providers:
- **Claude Code** (`claude`)
- **OpenAI Codex** (`codex`)
- **Google Antigravity** (`agy`)

However, invoking coding agent CLIs presents distinct hazards:
1. Interacting with AI agent binaries can unintentionally trigger interactive sessions, run prompts, or consume user quota/credits.
2. Directly inspecting user credential stores (e.g. `~/.codex/auth.json`, `~/.claude/`, `~/.gemini/`, macOS Keychain) violates the architectural invariant of **Zero Credential Storage/Handling** and risks security leakage.
3. Unsafe shell subprocess execution (`shell=True`) introduces command injection risks.
4. Hanging external commands could block user workflows indefinitely.

We need a disciplined, safe, and passive discovery mechanism for `cortexshift doctor`.

---

## Decisions

We have made the following foundational decisions for provider discovery and environment diagnostics:

### 1. Passive Discovery Only: Doctor != Health-Check-by-Model-Call
`cortexshift doctor` is strictly passive environment inspection. It will never:
- Launch interactive agent sessions or terminal user interfaces.
- Send test prompts to AI models or consume token quotas.
- Log into or out of external services.
- Install, update, or reconfigure provider CLIs.
- Mutate the workspace, project files, or Git status.

### 2. PATH-Based Cross-Platform Executable Resolution
Executables are located cross-platform using `shutil.which()` against the active environment `PATH`. CortexShift does not traverse arbitrary directories, hardcode OS-specific paths, or infer authentication from executable presence.

### 3. Safe Subprocess Execution Abstraction (`CommandRunner`)
All external processes are executed via a strict `CommandRunner` port (`SubprocessCommandRunner`):
- `shell=False` is strictly enforced.
- Commands are passed exclusively as lists of arguments (e.g., `["claude", "auth", "status"]`).
- Every command enforces a finite timeout (default: 5.0 seconds) to prevent infinite hangs.
- Process execution failures (`FileNotFoundError`, `PermissionError`, `TimeoutExpired`, `OSError`) are caught and translated into structured `CommandResult` models, never throwing unhandled exceptions to the CLI.

### 4. Native Diagnostic Commands Over Credential Storage Inspection
CortexShift will **never** read, parse, or touch vendor credential files or operating system keychains. Authentication state is inspected exclusively through non-interactive, vendor-supported diagnostic commands:
- **Claude Code**: `claude auth status`
- **OpenAI Codex**: `codex login status`
- **Google Antigravity**: Because `agy` currently exposes no non-interactive auth status command without model execution, its status is reported honestly as `unknown`.

### 5. Explicit Modeling of Uncertainty (`AuthenticationStatus.UNKNOWN`)
Authentication status is modeled as an enum (`authenticated`, `not_authenticated`, `unknown`, `not_probed`). If a tool does not provide a safe non-model probe or command output is ambiguous, CortexShift explicitly reports `unknown` rather than guessing or running a prompt.

### 6. Zero Sensitive Output Leakage
Raw process output from authentication status commands is discarded immediately after classification. Doctor outputs (both human-readable Rich tables and machine-readable JSON) never include email addresses, account IDs, tokens, usernames, hostnames, IP addresses, or home directories.

### 7. Ephemeral Diagnostics in Phase 1
In Phase 1, doctor reports are generated on-demand and ephemeral. No `.cortexshift/` state directory, SQLite database, or disk caches are created.

### 8. Missing Providers are Expected Diagnostic States
A missing or unauthenticated provider CLI is a normal diagnostic observation, not an application error. `cortexshift doctor` exits with returncode 0 as long as diagnostics executed properly.

---

## Consequences

### Positive
- **Safe**: Zero risk of unintentional quota exhaustion, session hijacking, or credential exposure.
- **Robust**: Resilient to hung processes, non-standard CLI outputs, or missing tools.
- **Privacy-preserving**: No user identities, host metadata, or auth tokens in reports or JSON output.
- **Maintainable**: Provider-specific probing logic resides behind modular adapters; domain and CLI remain clean.

### Negative / Trade-offs
- Antigravity authentication cannot be confirmed until Google provides a passive, non-model CLI status check. (Accepted: safety and zero quota consumption take precedence over speculative status).

---

## Alternatives Considered

1. **Test-Prompt Probing (e.g., `agy -p "ping"`)**:
   *Rejected*. Consumes paid API quota, requires network connectivity, and violates passive inspection invariants.
2. **Reading Local Credential Files (`~/.claude/config.json`, `~/.codex/auth.json`)**:
   *Rejected*. Violates Architectural Invariant #7 (Zero Credential Storage/Handling). File formats can change without notice and inspecting credentials exposes user secrets.
3. **Shell String Invocation (`subprocess.run("claude auth status", shell=True)`)**:
   *Rejected*. Security vulnerability and cross-platform quoting issues.

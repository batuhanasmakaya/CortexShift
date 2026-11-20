# ADR-0014 · Provider process ownership and MCP invocation authority

**Status** Accepted

## Context

The CortexShift launcher owned `agent.lock` and waited for the immediate native
provider. A child could keep working after the provider or launcher exited. MCP
identity was checked at server startup, and a server with a valid historical
Session could keep writing after completion or recovery.

## Workspace ownership

Run, resume, switch, and recovery acquire the workspace lease at the verified
root. Preflight takes `agent.lock` before checking the provider guard. A held
guard blocks the operation before another managed provider or recovery starts.
The dashboard probes both locks. Cooperative task and checkpoint operations
keep their existing lease behavior.

On macOS and Linux, the subprocess adapter passes one `flock` descriptor for
`.cortexshift/provider.lock` to the immediate provider, including during headless
handoff bootstrap. The launcher closes its copy when the process wait finishes.
The lock stays held for as long as any process retains a copy, including after
the launcher dies. A free guard leaves other activity unknown. A completed
Session records the direct provider's exit and the terminal diagnostic reports
guard contention or uncertainty separately. [Python subprocess documentation](https://docs.python.org/3.12/library/subprocess.html#subprocess.Popen) · [Darwin flock](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man2/flock.2.html) · [Linux flock](https://www.man7.org/linux/man-pages/man2/flock.2.html)

Interactive providers keep inherited terminal streams, process groups, and job
control. CortexShift retains its bounded interrupt handling for the direct child.
It never sends signals to descendants or scans for processes by name or PID. To
retry a blocked transition, let the provider or background job finish, or stop
known work using its usual controls. Keep the lock files in place.

| Platform | Workspace process ownership |
| --- | --- |
| macOS and Linux | The guard tracks processes that retain its descriptor, even after reparenting. Processes that close it, including many subprocess launchers, are outside that observation. |
| Windows | The launcher holds the existing workspace lease during managed execution. Activity beyond the direct child is unknown. |

A dedicated POSIX process group would require foreground terminal handover and
job-control changes. A Windows Job Object would require assigning the provider
before it could spawn children and could change job membership or breakaway
behavior. Both approaches would affect native workflows. The advisory guard
preserves those workflows and blocks when ownership can be observed. External
processes can edit the workspace independently of either lock.

## MCP write authority

Each CortexShift Session corresponds to one invocation, including a native resume.
After launch preparation the launcher acquires a private, non-inheritable lock
for that invocation and saves the SHA-256 verifier of a fresh random token in
the running Session's existing metadata. Managed provider config and environment
carry the token to local MCP. CortexShift keeps the token out of launch observers,
logs, canonical state, and handoff payloads. Headless bootstrap remains read-only
and drops any inherited token. Antigravity's static configuration receives no
per-invocation token.

Every MCP mutation verifies the pinned workspace, Project, Task, provider,
running and unreconciled Session, token verifier, and live launcher lock inside a
short SQLite `BEGIN IMMEDIATE` transaction. Startup uses the same reservation for
its probe. This also serializes Windows byte-range probes, which temporarily
take an exclusive lock when the marker is free. Missing or unverifiable authority
fails closed. Checkpoint and decision calls inspect Git before taking the write
reservation. Their snapshot and checkpoint commit together after authorization.
[SQLite transactions](https://www.sqlite.org/lang_transaction.html) · [Windows byte-range locking](https://learn.microsoft.com/en-us/cpp/c-runtime-library/reference/locking?view=msvc-170)

Normal finalization commits under the same SQLite writer ordering. An authorized
report may commit first, or finalization may make the report ineligible. The
launcher then releases MCP authority and captures its own session-end checkpoint
before releasing the workspace lease. After launcher death the OS releases the
invocation lock even if a provider, MCP server, or unfinished Session survives.
A mutation already authorized before the OS releases the lock can finish its
transaction. Recovery retains the existing unobserved end-time semantics.
Expired connected servers reject writes and still serve reads. Restarting with
the expired binding exposes only read tools.

Schema v6 and the MCP tool, checkpoint, and handoff formats are preserved. Normal
finalization drops the verifier and removes its marker when possible. A crash may
leave an unlocked marker and a historical verifier. Neither grants permission.
The local account and private workspace filesystem remain the trust boundary.

# CortexShift Canonical Handoff Protocol

The **Canonical Handoff Protocol** defines the standard, provider-independent contract used to pass context between coding agents.

This is an **internal state specification**, not an external network protocol. It defines the structured schema and operational expectations when an outgoing agent yields control and an incoming agent assumes responsibility for a persistent CortexShift task.

---

## 1. The Canonical Handoff Structure

Every handoff package contains the following 14 sections:

```text
1.  PROJECT
2.  ORIGINAL OBJECTIVE
3.  REQUIREMENTS
4.  CONSTRAINTS
5.  COMPLETED
6.  CURRENT WORK
7.  REMAINING
8.  IMPORTANT DECISIONS
9.  FILES TOUCHED
10. TEST STATUS
11. KNOWN ISSUES
12. GIT STATE
13. DO NOT REDO
14. RECOMMENDED NEXT ACTION
```

---

## 2. Field Specifications

| Section | Mandatory? | Type | Description |
| :--- | :--- | :--- | :--- |
| **PROJECT** | **Yes** | String / Metadata | Project name, root path, and core invariants. |
| **ORIGINAL OBJECTIVE** | **Yes** | String | The user's original goal when initiating the task. Never modified during handoffs. |
| **REQUIREMENTS** | **Yes** | List of Strings | Explicit functional/non-functional requirements. |
| **CONSTRAINTS** | **Yes** | List of Strings | Technical boundaries, prohibited libraries, or invariant constraints. |
| **COMPLETED** | **Yes** | List of Strings | Concrete achievements, implemented files, and passed milestones. |
| **CURRENT WORK** | No (Nullable) | String | Specific component or unit currently being implemented when the session halted. |
| **REMAINING** | **Yes** | List of Strings | Backlog of unfinished requirements and tasks. |
| **IMPORTANT DECISIONS** | No (Can be empty) | List of Strings | Architectural or design decisions made during the session and their rationale. |
| **FILES TOUCHED** | No (Can be empty) | List of Strings | Relative paths of files created, modified, or deleted during the task. |
| **TEST STATUS** | **Yes** | String / Object | Summary of last executed test commands, results, and current pass/fail counts. |
| **KNOWN ISSUES** | No (Can be empty) | List of Strings | Discovered bugs, blockers, or failing edge cases requiring attention. |
| **GIT STATE** | **Yes** | GitSnapshot | Active branch, HEAD commit hash, dirty flag, staged/modified/untracked file lists. |
| **DO NOT REDO** | No (Can be empty) | List of Strings | Explicit warnings about paths already attempted that failed, or decisions not to revert. |
| **RECOMMENDED NEXT ACTION** | **Yes** | String | The single clearest immediate step the incoming agent should perform. |

---

## 3. Advisory Status & Empirical Verification

### The Golden Rule of Agent Handoff
> **A handoff is advisory; the repository is reality.**

Incoming agents must never blindly trust handoff declarations. For example:
- If `TEST STATUS` claims *"All tests pass"*, the receiving agent must independently run the test suite before writing new code.
- If `COMPLETED` claims *"Feature X is implemented"*, the receiving agent must inspect the corresponding code file to confirm its structure and quality.
- If `GIT STATE` claims clean working tree, the agent verifies with `git status`.

Every handoff prompt injected into a receiving agent concludes with this mandatory directive:
```text
NOTICE TO RECEIVING AGENT:
This handoff package represents advisory memory from previous work.
The actual repository files, Git working tree, and verified test execution
results outrank all claims made in this document. Inspect relevant files
and verify test status before proceeding.
```

---

## 4. Handling Unexpected Termination

Agents frequently disconnect unexpectedly due to:
1. Provider API rate limits or hourly quota limits.
2. Context window saturation.
3. Process crashes or network timeouts.
4. User abortion (`Ctrl+C`).

When an unexpected termination occurs, an outgoing agent cannot produce an exit handoff. CortexShift handles this seamlessly:

1. **Latest Checkpoint Retrieval**: CortexShift retrieves the most recent `Checkpoint` saved during the session.
2. **Repository Re-Inspection**: CortexShift executes a fresh `RepositoryInspector` sweep to obtain the actual current Git state (modified files, untracked files).
3. **Synthetic Handoff Generation**: CortexShift merges the checkpoint's `DONE`, `CURRENT`, `NEXT`, `DECISIONS`, and `ISSUES` with the fresh Git state to generate a valid `Handoff` payload.
4. **Recovery Notification**: The incoming agent receives the synthetic handoff flagged with `recovery_mode = true` and `exit_reason = UNEXPECTED_TERMINATION`, warning the agent to pay special attention to uncommitted changes.

---

## 5. Stale Checkpoint Recovery

If an agent worked for an extended period without creating checkpoints before crashing:
- CortexShift detects that the working tree has uncommitted modifications not referenced in the last checkpoint.
- The synthesized handoff marks `CURRENT WORK` as unknown and sets `RECOMMENDED NEXT ACTION` to:
  *"Review uncommitted changes in git status, run test suite to ascertain workspace health, and reconstruct current task status."*

---

## 6. Context Budgeting & Distillation

Over long-running tasks spanning dozens of agent switches, cumulative lists (like `COMPLETED` or `IMPORTANT DECISIONS`) can grow excessively large.

In future phases:
- **Hierarchical Distillation**: Completed low-level micro-tasks are rolled up into milestone summaries.
- **Deduplication**: Superseded decisions are pruned from `IMPORTANT DECISIONS`.
- **Active Working Set**: Only files touched within the last $N$ sessions are emphasized, while older modified files are summarized in an aggregate archive.

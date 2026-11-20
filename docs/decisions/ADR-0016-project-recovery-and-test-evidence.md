# ADR-0016 — Project-wide recovery and historical test evidence

**Status** Accepted

## Context

Recovery found unfinished Sessions through the active Task. It persisted a Git
snapshot, a recovery checkpoint, and each Session update in separate commits.
A crash could leave a checkpoint claiming reconciliation before the corresponding
Session transition had committed. Retrying could create another checkpoint.

Handoffs used the newest checkpoint as the sole source of test results. Automatic
session-end and recovery checkpoints usually carry no new report, so they hid
earlier results that were still present in history.

## Recovery

Preview and execution acquire the verified project's exclusive workspace lease
and respect the retained-provider guard. Contention refuses the operation. Recovery
selects unfinished Sessions by their Task's project binding, newest first with the
existing insertion-order tie-breaker. This includes inactive Tasks and projects
without an active Task. Previously reconciled Sessions are counted separately.

A clear guard permits reconciliation when the Session has provenance for a managed
guarded launch. New invocations record that fact in existing Session metadata.
On POSIX, a well-formed C4 invocation verifier from before the marker is also evidence
of the managed interactive launch path. A held invocation lock identifies live
launcher ownership. Missing legacy provenance, inconsistent lifecycle fields,
and inconclusive lock probes yield uncertain ownership. Windows lacks a retained
provider guard after launcher death, so unfinished launches on Windows remain
uncertain. Expired MCP authority alone supplies no evidence of provider exit.
Activities outside the managed guard remain unknown.

Recovery acquires one live Git observation before writing. Each affected Task gets
a short SQLite write transaction. Inside it, recovery reloads the Task and its
candidate invocations and writes only the prepared Sessions whose records remain
unchanged. It commits those Session transitions with one checkpoint bound to the
newest reconciled Session and listing all reconciled Session IDs for that Task.
The first committed Task transaction also saves the Git snapshot when available.
Later Tasks in that run reference that observation. Each Session becomes interrupted
with unexpected termination and a reconciliation timestamp. Its unobserved process
end time stays empty. Task fields, activation, prior checkpoints, and handoff
delivery receipts retain their existing values.

If a transaction fails, that Task's Session transitions and evidence roll back
together. Earlier Task transactions may already be committed. A repeat discovers
remaining work and counts prior reconciliations without creating duplicate evidence.
The workspace lease excludes overlapping managed launches, and the write transaction
preserves normal finalization that wins before reconciliation. Session persistence
preserves a committed reconciliation against a subsequent stale write.

## Test evidence

Preview, dry-run switch, and actual switch select the newest meaningful test report
across the Task's Sessions by checkpoint creation and insertion order. The latest
checkpoint remains a separate provenance reference. A meaningful report has recognized
provenance, an explicit known marker, and nonblank text beyond the default unknown
message. A later failure supersedes an earlier success, and a later success
supersedes an earlier failure. Session-end and recovery checkpoints retain their
own state without copying any earlier test report.

The rendered TEST STATUS includes the evidence checkpoint, originating Session when
known, capture time, provenance, and recorded text as quoted data. The Git comparison
describes observed HEAD at capture and at handoff. Matching HEAD provides limited
context since the tested files and repository state at execution time are unknown.
The report identifies dirtiness observed at either point and leaves current
verification unknown. When Git or either HEAD is unavailable, the comparison
is explicitly unknown.

The renderer treats the report as one optional entry under the 48,000 UTF-8 byte
limit. It discloses an omitted report and preserves essential Task instructions.
Recovery transfers only candidate Session rows and counts prior reconciliations in
SQLite. Handoffs project decision text and test evidence out of checkpoint JSON,
avoiding full checkpoint payload deserialization. Decision lookup still visits the
Task's history. The evidence lookup stops at the first valid report in indexed
checkpoint order. SQLite schema v6 and both protocol versions are unchanged.

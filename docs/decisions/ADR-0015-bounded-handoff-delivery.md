# ADR-0015 — Bounded handoff delivery

**Status** Accepted

## Context

The renderer counted code points and reserved space for only some lists. Decisions,
free-text observations, and appended retrieval instructions could exceed 48,000
bytes. An objective clipped by its field cap could still report a complete render.
Provider launch arguments carried the handoff text and therefore grew with the Task.

## Decision

Count the final UTF-8 handoff text, including formatting, notices, and any fixed
provider preamble, against the existing numeric ceiling of 48,000. Keep task identity,
objective, requirements, and constraints complete. If they cannot fit with the
required framing, return a typed error before persisting a handoff or starting a
provider. Under pressure, select whole optional entries deterministically, with
first access to extra room for current work and explicit section-specific omissions.
Agents can retrieve current Task fields through existing MCP tools or inspect the
full historical handoff through the local CLI.

Use Codex exec stdin and Antigravity stream-JSON stdin for the existing headless
bootstraps. Close the pipes after sending one prompt and retain exact native-ID
checks. For Claude Code, launch the native TUI with a small request for the
invocation-bound MCP tool. The tool re-derives the bounded text and atomically
records that CortexShift served it, using the existing live invocation write
authorization. A no-argument tool and reciprocal Session/Handoff links scope
retrieval to one invocation. Read-only or expired bindings cannot record a receipt.
Provider-reported completion or a served response records transport evidence.
Actual model use remains unverified.

## Consequences

A bootstrap can fail when the installed provider lacks the documented stdin
transport. Claude's MCP server can be blocked by provider policy or user approval.
Its handoff remains prepared if CortexShift never serves it. The historical
handoff is accessible under its existing ID in those cases. Native interactive
stdio and exact resume behavior are preserved.

Rendering scans optional records in order and bounds the size of intermediate
rendered lines and output. Canonical history lookup and storage still scale with
the size of retained project data. JSONL and MCP envelope encoding can add bytes to
the wire representation after the prompt text is measured. No prompt files are
created. Schema v6, canonical handoff v1, checkpoint v1, and historical rows remain
compatible. New strategy and budget values use the existing record metadata.

[Claude Code CLI reference](https://code.claude.com/docs/en/cli-reference) ·
[Codex exec CLI source](https://github.com/openai/codex/blob/main/codex-rs/exec/src/cli.rs) ·
[Antigravity headless mode](https://antigravity.google/docs/cli/headless/)

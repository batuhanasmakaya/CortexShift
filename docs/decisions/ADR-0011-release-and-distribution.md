# ADR-0011: Release and distribution

- **Status**: Accepted
- **Date**: 2026-09-06

## Context

The implemented product needs reproducible installation and reviewable publication
without adding product capabilities or a cloud dependency. Public identity and
account settings remain maintainer-controlled external actions.

## Decision

The Python package is canonical, using Hatchling wheel/sdist builds and one version
in `pyproject.toml`; installed metadata supplies the runtime version. pipx is the
primary install/upgrade/uninstall path. CPython 3.12–3.14 and Linux/macOS/Windows
are covered by a nine-cell CI matrix. v0.1.0 is an initial public alpha.

GitHub Actions uses immutable full-SHA actions and least privilege. Release
validation precedes building publication artifacts once; publishing consumes the
same checked artifacts through PyPI Trusted Publishing with OIDC and a dedicated
`pypi` environment. Checksums accompany release assets; no long-lived PyPI secret
is used. Public repository and publisher configuration require separate authorization.

A custom Homebrew tap follows real release publication, with immutable source
checksums and declared Python resources. No placeholder formula is publishable.
There is no self-updater, curl installer, or standalone binary promise.

Published release tags/artifacts, SQLite migrations, and persisted handoff/checkpoint
meanings are immutable. New incompatible state changes require forward migrations
or protocol versioning. Release engineering preserves schema v6 and protocols v1.

## Alternatives and consequences

Standalone executables add platform packaging complexity without a release blocker
in Python installation. A custom shell installer or updater adds unnecessary
network execution and maintenance. Long-lived tokens enlarge credential exposure.
Immediate homebrew/core submission would assume acceptance and missing public metadata.
These alternatives are rejected for the initial alpha.

Clean-room artifact tests cost CI time but detect missing CSS, source leakage, and
MCP subprocess path failures that editable-install tests miss. Real-provider E2E
remains a separate intentional gate because it requires accounts and usage.

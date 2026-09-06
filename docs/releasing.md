# Releasing CortexShift

0.1.0 is the initial public alpha. This candidate is preparation only: no tag,
push, GitHub Release, PyPI upload, or Homebrew tap publication is authorized by
running local checks. A maintainer must explicitly authorize external publication.

## One-time external setup (pending)

- Establish the canonical public GitHub repository, with `main` as default branch.
  No remote is configured in the candidate checkout. Populate truthful project
  URLs in `pyproject.toml`, README links/badges, and the security reporting link
  only after that identity is established. Verify Markdown links on PyPI too.
- Enable Actions and require CI via a branch ruleset; consider review requirements,
  Dependabot alerts, available secret scanning, and private vulnerability reporting.
- Secure the PyPI account with MFA. Configure a pending publisher for `cortexshift`
  (or a publisher on the existing owned project) using the actual GitHub owner,
  repository, workflow filename `release.yml`, and environment `pypi`.
- Create the GitHub `pypi` environment. Consider required reviewers and protected
  deployment tags before the first release. These settings are not created by YAML.
- Later create a custom Homebrew tap; do not imply homebrew/core acceptance.

The official [Trusted Publishing guide](https://docs.pypi.org/trusted-publishers/using-a-publisher/)
describes OIDC setup. No long-lived PyPI token or provider credential is required.

## Release sequence

1. Review and commit the release work, then begin from clean `main`. Phase 10 is
   intentionally left uncommitted for review. Never amend historical releases.
2. Run `uv sync --locked`, `uv run ruff check .`, `uv run ruff format --check .`,
   `uv run mypy`, and `uv run pytest`. Inspect warnings and coverage.
3. Repeat exact normalized PyPI namespace and public repository name checks.
   On 2026-09-06 PyPI's `cortexshift` JSON endpoint returned 404. GitHub search
   found an apparently unrelated `mevcel/CortexShift` JavaScript repository.
   Review that collision before choosing the public repository identity. This
   is a namespace check, not trademark/legal clearance or a uniqueness claim.
4. Update the canonical version in `pyproject.toml` and its changelog section.
   Runtime version comes from installed metadata. Validate the intended tag:
   `uv run python scripts/release_check.py --require-clean --tag v0.1.0`.
5. Build with `uv build` from clean source. Remove only previous generated
   artifacts first if necessary; `dist/` is ignored. The build uses Hatchling.
6. Run `uv run python scripts/release_check.py --dist dist --checksums` and
   `uvx --from 'twine==7.0.0' twine check --strict dist/*.whl dist/*.tar.gz`.
   Inspect archive contents and candidate SHA256SUMS. Run the local secret/path
   audit (`uv run python scripts/security_audit.py`) and a best-effort dependency
   vulnerability/license review.
7. Run `uv run python scripts/artifact_smoke.py --dist dist`. This installs the
   wheel and an sdist-derived wheel into separate fresh environments outside the
   checkout (paths include spaces), with temporary child HOME/USERPROFILE. It
   tests the installed CLI, official MCP stdio client, Textual resources, fake
   providers, continuity, recovery, privacy, and isolated pipx install/uninstall.
   It also runs representative tests from the sdist. Package-index dependency
   downloads are expected; no models or release uploads are performed.
8. Require the full Linux/macOS/Windows × CPython 3.12/3.13/3.14 CI matrix green.
   Finish the intentional real-provider and platform checks below. The local
   existence of workflow files is not evidence that GitHub CI has run.
9. With maintainer authorization, push the reviewed release commit and immutable
   `v0.1.0` tag, then publish a normal GitHub Release with the draft release notes.
   A GitHub prerelease/draft does not publish stable `0.1.0` through this workflow.
10. `release.yml` reuses the complete CI gate, guards tag/version/clean state,
    builds its publication distributions once, checks metadata and installed
    behavior, and uploads an immutable Actions artifact. The `pypi` job downloads
    those exact files, verifies SHA256SUMS, and publishes through the official
    SHA-pinned PyPA action with job-only `id-token: write`. Attestations remain
    enabled by the action's default. No rebuild occurs in the publish job.
11. The assets job downloads the same artifact, verifies checksums, and attaches
    wheel, sdist, and SHA256SUMS to the GitHub Release with `gh release upload`.
    It has job-only `contents: write` and does not overwrite existing assets.
    Verify a fresh `pipx install cortexshift`, version, help, and a local quickstart.
12. Finalize the [Homebrew tap](../packaging/homebrew/README.md) only with real
    public release URLs, immutable checksums, and generated dependency resources.
13. Perform post-release CLI, MCP, TUI, and provider smoke tests. Record exact
    artifact hashes and validation environments. Candidate hashes are not final
    if the release build changes the artifacts.

TestPyPI is an optional separately authorized OIDC rehearsal, not a required step.
Ordinary CI never publishes and requires zero repository secrets. Release jobs
have bounded timeouts, full-SHA actions, and no environment/auth-file dumps.

## Manual provider and platform gate

Run `uv run python scripts/provider_preflight.py` first. It only probes version
and help, with timeouts. Missing installed providers remain unvalidated; any
installed contract drift is a release blocker requiring an adapter fix.
The following intentionally consume native provider usage and must be performed
by a maintainer, not automatically by CI:

- [ ] Claude real run, handoff/resume, and visible MCP tools.
- [ ] Codex real run, handoff/resume, and visible MCP tools.
- [ ] Antigravity real run, handoff/resume, and explicit workspace MCP setup.
- [ ] macOS interactive TUI/provider smoke.
- [ ] Linux interactive TUI/provider smoke.
- [ ] Windows interactive TUI/provider smoke.

Distinguish fake automated coverage, passive local detection, actual CI runs,
and human validation. Use “CI-tested” only after CI ran; do not claim mature
production support from CI alone.

## Compatibility and corrections

Follow Semantic Versioning. During 0.x, minor releases may introduce documented
breaking changes; patch releases should preserve user-facing behavior. Commands,
MCP tool contracts, SQLite migrations, and persisted protocols deserve explicit
compatibility review. Internal Python imports are not a stable library API.

0.1.0 uses SQLite schema v6, Handoff Protocol v1, and Checkpoint Protocol v1.
Published migrations v1–v6 are immutable; new changes require v7+ migrations.
Do not silently delete/reset state or change historical payload meanings.
Published tags and distributions are immutable. PyPI versions cannot simply be
overwritten: publish a new version for corrections, with a migration/versioning
strategy where needed. No self-updater or remote shell installer is included.

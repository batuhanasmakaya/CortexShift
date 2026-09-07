# Releasing CortexShift

0.1.0 is the initial public alpha. This candidate is preparation only: no tag,
push, GitHub Release, PyPI upload, or Homebrew tap publication is authorized by
running local checks. A maintainer must explicitly authorize external publication.

## One-time external setup

- Done: the canonical public repository is
  <https://github.com/batuhanasmakaya/CortexShift>, with `main` as the default
  branch and `origin` configured in the checkout. Verified project URLs are
  populated in `pyproject.toml` (`Homepage`, `Repository`, `Issues`, `Changelog`,
  `Security`) and in the README/SECURITY/CONTRIBUTING links. Re-verify the
  rendered Markdown links on PyPI after the first upload.
- Done: `main` is protected by an active ruleset. Pull requests are required, and
  10 required status checks are enforced: `quality` plus the Ubuntu, macOS, and
  Windows jobs on CPython 3.12, 3.13, and 3.14. A ruleset is repository
  configuration, not something the workflow files can assert.
- Done: GitHub private vulnerability reporting is enabled, and `SECURITY.md`
  directs reporters to the Security Advisory intake instead of public issues.
  Secret scanning alerts are enabled. Dependabot alerts are **not** claimed
  enabled: no current repository evidence proves that setting, so treat it as an
  open manual repository check rather than completed setup.
- Done: a PyPI Trusted Publishing pending publisher is configured for project
  `cortexshift` with owner/repository `batuhanasmakaya/CortexShift`, workflow
  filename `release.yml`, and environment `pypi`. No long-lived PyPI API token is
  used anywhere in this project.
- Done: the GitHub `pypi` environment exists and allows `v*` release tags.
  Revisit required reviewers before the first release; these settings are not
  created by YAML.
- Manual account check: PyPI account MFA. Repository evidence cannot prove the
  account's MFA state, so verify it in PyPI account settings rather than assuming
  it; do not record it as complete here on the basis of this checkout.
- Pending: the first PyPI publication has not happened. The pending publisher
  becomes a normal publisher on the first successful upload, after which the
  rendered Markdown links must be re-verified on the PyPI project page.
- Later create a custom Homebrew tap; do not imply homebrew/core acceptance.

The official [Trusted Publishing guide](https://docs.pypi.org/trusted-publishers/using-a-publisher/)
describes OIDC setup. No long-lived PyPI token or provider credential is required.

## Release sequence

1. Review and commit the release work, then begin from clean `main`. Release
   preparation lands on `main` through a reviewed pull request, because the
   ruleset requires one. Never amend historical releases.
2. Run `uv sync --locked`, `uv run ruff check .`, `uv run ruff format --check .`,
   `uv run mypy`, and `uv run pytest`. Inspect warnings and coverage.
3. Repeat the exact normalized PyPI namespace check. On 2026-09-06 PyPI's
   `cortexshift` JSON endpoint returned 404. GitHub search found an apparently
   unrelated `mevcel/CortexShift` JavaScript repository; the public repository
   identity has since been established as `batuhanasmakaya/CortexShift`. This
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

- [x] Claude real run, handoff/resume, and visible MCP tools. Validated against
      authenticated Claude Code 2.1.204: managed MCP session binding confirmed,
      all 10 MCP tools visible and all 6 write tools usable in the managed
      session, session-bound checkpoint provenance confirmed, native session ID
      captured, native resume confirmed, and CortexShift session lineage confirmed.
- [x] Codex real run, handoff/resume, and visible MCP tools. Validated against
      authenticated Codex 0.153.4 (the environment's installed 0.144.3 was
      updated because the selected model required a newer CLI): managed MCP
      session binding confirmed, all managed write tools available, session-bound
      checkpoint provenance confirmed, native session ID captured through
      switch/bootstrap, native resume confirmed, and CortexShift session lineage
      confirmed.
- [x] Cross-provider real handoff: `cortexshift switch codex` from a real Claude
      session delivered current task, objective, current work, previous
      provider/session, and a structured decision correctly, and the receiving
      Codex obtained a new managed CortexShift session.
- [x] Decision durability across handoffs: a decision recorded in an earlier
      checkpoint survived a later empty manual checkpoint and the automatic
      session-end checkpoint, handoff aggregation still surfaced it, and later
      checkpoints remained literal point-in-time observations with empty
      decisions rather than copying history forward.
- [ ] Antigravity real run, handoff/resume, and explicit workspace MCP setup.
      Deferred: Antigravity is not installed in the validation environment, so no
      real Antigravity E2E has been performed. Deterministic fake-provider
      coverage remains the only validation basis for that adapter.
- [ ] macOS interactive TUI/provider smoke.
- [ ] Linux interactive TUI/provider smoke.
- [ ] Windows interactive TUI/provider smoke.

Provider versions above are the versions actually exercised, not a compatibility
guarantee for other or future CLI releases; native CLI changes may require
adapter updates and re-validation.

Distinguish fake automated coverage, passive local detection, actual CI runs,
and human validation. Use “CI-tested” only after CI ran; do not claim mature
production support from CI alone.

### Latest verified automated validation

Recorded before merge of the managed-MCP/decision-durability fix branch: 863
pytest tests passed, with dedicated managed-MCP regression coverage passing;
`ruff check`, `ruff format`, and strict `mypy` passed; `scripts/release_check.py`
passed; the rebuilt artifact smoke passed; and `scripts/security_audit.py`
reported 0 findings. That branch then passed all required cross-platform GitHub
CI checks and merged to `main` as PR #1. Re-run this gate on the exact release
commit; an earlier run is evidence about that earlier tree, not about a later one.

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

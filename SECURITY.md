# Security

CortexShift is local orchestration software, with no CortexShift cloud/server,
telemetry, or network listener. Its MCP transport is child-process stdio only.
Project-local SQLite stores structured task state and historical observations.
Native providers may send context or repository data to external services under
their own configuration and terms.

CortexShift does not copy credentials, read provider auth/cache files, or persist
user prompts, rendered handoffs, provider responses, transcripts, or full Git
patches. User-entered task/checkpoint text is deliberately persisted: do not put
secrets in those fields. Native permission controls remain authoritative;
CortexShift does not enable unsafe permission bypasses. Git inspection is read-only.
The workspace lease coordinates cooperative agents; it is not an OS security
boundary against other programs running as the same user. MCP binding protects
against accidental cross-task writes, not a malicious local process.

## Reporting vulnerabilities

The canonical public repository is
<https://github.com/batuhanasmakaya/CortexShift>, and this policy is published at
<https://github.com/batuhanasmakaya/CortexShift/security>.

Report suspected vulnerabilities privately through GitHub private vulnerability
reporting, which is enabled on that repository. Open a draft Security Advisory
from the Security tab ("Report a vulnerability"), or use
<https://github.com/batuhanasmakaya/CortexShift/security/advisories/new>. The
report stays private to the maintainer until an advisory is published. This is
the only supported intake channel; no reporting email address is published.
Do not disclose exploit details, proof-of-concept exploits, or sensitive data in
public issues, discussions, or pull requests. Expect best-effort, single-maintainer
response times on an alpha project, and coordinate disclosure through the advisory.

Ordinary bug reports belong in
<https://github.com/batuhanasmakaya/CortexShift/issues> and may include
CortexShift version, OS, Python/provider version,
`cortexshift doctor` output after reviewing local paths, and a sanitized error.
Do not attach API keys, credentials, private transcripts, a whole private repository,
or the `.cortexshift` database. Review all diagnostics before sharing.

## Compatibility and maintenance

The initial alpha uses SQLite schema v6, Handoff Protocol v1, and Checkpoint
Protocol v1. Published migrations and protocol meanings are immutable contracts;
future changes require forward-safe migrations/versioning. No older public
release is currently claimed as supported. Dependency and workflow updates are
reviewed; ordinary CI requires no secrets, and release publishing uses OIDC with
no long-lived PyPI token. GitHub secret scanning alerts are enabled on the
repository.

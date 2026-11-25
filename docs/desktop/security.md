# Desktop dependency security review

The fresh DX3C baseline used cargo-audit 0.23.0, RustSec database commit `10db6998b5d74ec438bcf0763125bd425a068a8f` (1,567 advisories), and the DX3B Cargo lockfile. It found the six earlier warnings and one new confirmed vulnerability. After resolving `cookie` 0.18.3, a second audit against the same refreshed database found zero confirmed vulnerabilities and the same six warnings. The released targets under consideration are Apple Silicon macOS and Windows x64.

| Advisory | Affected version and category | Build/runtime and reachability | Fix, compatibility and residual release impact |
| --- | --- | --- | --- |
| [RUSTSEC-2026-0543](https://rustsec.org/advisories/RUSTSEC-2026-0543) | `chrono` 0.4.45, unmaintained | Optional integration in `serde_with` appears in the lockfile. Cargo's resolved graph omits it even for `--target all` with current features. | No fixed upgrade. Keep the optional feature off and track upstream removal. Reassess maintenance risk before enabling it. |
| [RUSTSEC-2024-0370](https://rustsec.org/advisories/RUSTSEC-2024-0370) | `proc-macro-error` 1.0.4, unmaintained | Build-time GTK/GLib procedural macro chain on Linux. Absent from both release targets. | No fixed upgrade. Follow upstream `glib-macros` and GTK migration. Reassess build-toolchain maintenance before adding Linux. |
| [RUSTSEC-2026-0371](https://rustsec.org/advisories/RUSTSEC-2026-0371) | `zbus_names` 4.3.5, unmaintained | Linux D-Bus name handling and macros through `tauri-plugin-single-instance`. Absent from both release targets. | No fixed upgrade. Track the upstream D-Bus migration and verify single-instance semantics before adopting it. Residual Linux maintenance risk. |
| [RUSTSEC-2026-0370](https://rustsec.org/advisories/RUSTSEC-2026-0370) | `zvariant` 5.15.1, unmaintained | Linux D-Bus serialization at runtime and proc-macro build time. Absent from both release targets. Same dependency chain as `zbus_names`. | No fixed upgrade. Follow a compatible upstream single-instance stack. Residual Linux parsing and maintenance risk. |
| [RUSTSEC-2026-0372](https://rustsec.org/advisories/RUSTSEC-2026-0372) | `zvariant_derive` 5.15.1, unmaintained | Build-time macro for the same Linux D-Bus chain. Absent from both release targets. | No fixed upgrade. Update with its matching `zvariant` series when the upstream stack changes. Residual Linux build risk. |
| [RUSTSEC-2024-0429](https://rustsec.org/advisories/RUSTSEC-2024-0429) | `glib` 0.18.5, unsoundness in `VariantStrIter` | Linux GTK runtime dependency. Exact iterator reachability is unverified for Linux and excluded by both shipped target graphs. | Patched in 0.20.0 or later. A forced pin conflicts with GTK3 bindings; move to a compatible upstream platform stack. Block Linux distribution until resolved. |
| [RUSTSEC-2026-0594](https://rustsec.org/advisories/RUSTSEC-2026-0594) | `cookie` 0.18.2, confirmed private-cookie nonce reuse after `fork` | Runtime dependency through Tauri on both targets. The vulnerable API's `private` feature was not selected in the target graphs. | Resolved to 0.18.3 and auditable in the lockfile. The compatible patch closes the advisory even if feature selection changes. |

The scoped disposition permits the six exact reports only while absent from both supported native graphs. The release gate retains all warnings in CI logs, fails on any vulnerability and requires explicit review of new or changed warning records. A future Linux build needs a separate audit and qualification.

```sh
cargo audit --file desktop/src-tauri/Cargo.lock --json > rustsec.json
uv run --locked python desktop/scripts/release.py --audit rustsec.json
cd desktop/src-tauri
cargo tree --locked --target aarch64-apple-darwin -e normal,build -i glib
cargo tree --locked --target x86_64-pc-windows-msvc -e normal,build -i zvariant
cargo tree --locked --target all -e features -i chrono
cargo tree --locked --target aarch64-apple-darwin -e features -i cookie
```

The production process also audits the npm and locked Python dependencies and scans the repository for high-confidence credential patterns. Software component inventories and build provenance allow reviewers to trace artifact bytes to the lockfile and source revision. Consult the [release guide](releasing.md) for the verification commands.

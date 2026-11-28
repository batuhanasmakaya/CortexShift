# Desktop release engineering

## Versioning and eligibility

`desktop/src-tauri/tauri.conf.json` owns the Desktop version. Update npm and Rust package metadata and their lockfiles to match it. Use stable numeric `X.Y.Z` within Windows version field limits. `desktop-vX.Y.Z` identifies an exact Git commit. `vX.Y.Z` remains reserved for the CLI and PyPI workflow. Desktop currently embeds Python core 0.1.0 from the same commit's `uv.lock`, uses private bridge protocol 1 and reads project schema v6. Changes to project schema and compatibility follow the core's own migration contract.

| State | How it is created | Evidence |
| --- | --- | --- |
| Development | Vite/Tauri dev in a checkout | Local development services and Python environment |
| Verification | Read-only Desktop CI on PRs and main | Unsigned native artifacts and installer smoke |
| Internal | Manual distribution workflow with `signed=false` | Clean revision, version check, audits and unsigned installers |
| Signed candidate | `signed=true` on protected main at the exact existing Desktop tag | Protected signing approvals, platform and updater signatures, installer smoke, hashes and provenance |
| Published stable | Maintainer promotion of exact qualified candidate | Both clean-machine results, previous-version upgrade, authenticated provider qualification and separate publication approval |

```sh
uv run --locked python desktop/scripts/release.py --require-clean --tag desktop-v0.2.0
```

A signed run requires `--require-tag` as well. Tag protected main only after the intended source and version-specific release notes have been reviewed. Configure both `desktop-sign-macos` and `desktop-sign-windows` environments with required reviewers, prevention of self-review, and deployment restricted to protected main. Configure tag protection for `desktop-v*`. Keep these environment credentials out of repository-wide variables and PR jobs. [GitHub deployment environments](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments)

## macOS signing and notarization

Provision an Apple Developer Program team, a **Developer ID Application** identity and a team App Store Connect API key accepted by `notarytool`. Set the protected macOS environment entries:

- Secrets `APPLE_CERTIFICATE` (base64 PKCS#12), `APPLE_CERTIFICATE_PASSWORD` and `APPLE_API_KEY_P8` (base64 `.p8`).
- Variables `APPLE_SIGNING_IDENTITY` (exact certificate name), `APPLE_TEAM_ID`, `APPLE_API_ISSUER` and `APPLE_API_KEY` (key ID).

The build imports the identity into a temporary keychain. PyInstaller rewrites Mach-O paths and signs the collected binaries with that identity before embedding them. Tauri signs and notarizes the assembled `.app`, then builds the update archive and `.dmg`. The signing script submits the disk image separately, requires an `Accepted` response and staples it. The verification step inspects the Mach-O inventory, verifies code signatures and Hardened Runtime, checks Gatekeeper and confirms Apple stapling for the app, update archive's app and disk image. Extracted one-file archive binaries are checked individually. Signing only the outer executable leaves the Python payload's signatures unevaluated. [Tauri macOS signing](https://v2.tauri.app/distribute/sign/macos/), [PyInstaller signing order](https://pyinstaller.org/en/v6.17.0/feature-notes.html#macos-binary-code-signing), [Apple notarization](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution)

## Windows signing

The protected Windows job uses Azure Artifact Signing **Public Trust** with Microsoft Entra federation. Configure the federated subject for `repo:batuhanasmakaya/CortexShift:environment:desktop-sign-windows` and assign **Artifact Signing Certificate Profile Signer** as narrowly as the certificate profile permits. Set `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `AZURE_SIGNING_ENDPOINT`, `AZURE_SIGNING_ACCOUNT`, `AZURE_SIGNING_CERTIFICATE_PROFILE`, and the exact `WINDOWS_SIGNER_SUBJECT` as protected environment variables. The pinned Azure login action obtains short-lived credentials, and the pinned Microsoft Dlib and SDK SignTool sign and timestamp the bridge and Tauri's NSIS executables. The qualification step checks the installer, installed app, bridge and uninstaller against the expected Authenticode subject and timestamp.

A hardware-backed public code-signing certificate or another hosted signer is an alternative. Provision its authorized signing client and configure Tauri's `bundle.windows.signCommand`, with `%1` as the file argument, to sign every required PE file. The installed bridge is signed before NSIS embeds it. Verify the actual installer and files on a target Windows machine. [Tauri Windows signing](https://v2.tauri.app/distribute/sign/windows/), [Microsoft Artifact Signing integrations](https://learn.microsoft.com/en-us/azure/artifact-signing/how-to-signing-integrations)

## Updater and stable feed

Generate a password-protected key offline with `npm run tauri -- signer generate --write-keys /path/outside/source/cortexshift-update.key`. Escrow the private key and password securely. Set the same `TAURI_SIGNING_PRIVATE_KEY` and `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` secrets in both signing environments. Set `DESKTOP_UPDATER_PUBLIC_KEY` to the generated `.pub` contents. Establish the key's identity by comparing the public key independently on both platforms. Both candidates must be signed by the same updater key. The Tauri public key shipped in a production build represents long-term trust. If it is lost or compromised, hold the feed and issue a platform-signed installer with a replacement key. Keep disposable test keys out of production.

The Rust updater checks only the HTTPS stable feed at `https://batuhanasmakaya.github.io/CortexShift/desktop/stable.json`. It accepts strictly newer stable versions and the exact platform asset for the matching GitHub Desktop tag. The signature binds the artifact bytes and announced version. The check displays release notes as text. An explicit install verifies the download, checks private action receipts and registered workspace ownership, then shuts down the supervised bridge before replacing files. Pending or active jobs and unverified ownership block installation. macOS requires a quit and reopen. Windows runs the native installer and closes Desktop. The updater stays unavailable when the protected signing configuration is absent. [Tauri 2 updater](https://v2.tauri.app/plugin/updater/)

## Assembly and publication

The manual workflow never writes GitHub Release assets. A signed run produces a candidate archive after both native jobs pass and the updater public-key fingerprints match. It includes the DMG, macOS `.app.tar.gz`, NSIS installer, two `.sig` files, `PUBLIC-KEY.sha256`, `SHA256SUMS`, `RELEASE-NOTES.md` and `stable.json`. The app is delivered inside the DMG. The NSIS installer doubles as its updater payload. Download and verify the exact Actions artifacts locally before qualification:

```sh
(cd candidate && shasum -a 256 -c SHA256SUMS)
gh attestation verify candidate/SHA256SUMS --repo batuhanasmakaya/CortexShift --signer-workflow batuhanasmakaya/CortexShift/.github/workflows/desktop-release.yml
```

Match the attestation to the approved run, protected ref and expected source SHA. Verify each installer and manifest the same way when needed. Record the release tag, SHA, run and asset hashes together with the platform qualification evidence. The qualification and publication approver must review the [release checklist](qualification.md).

After explicit approval, a maintainer creates a **draft** GitHub Release for the pre-existing Desktop tag, with the exact staged files and release notes. Use the repository's immutable release setting where available. Publish the draft with the reviewed metadata. Then verify every linked download serves the expected hash and update `desktop/stable.json` through the separately reviewed GitHub Pages deployment. Reserve the stable pointer for qualified releases only. Keep unpublished candidates and any future prerelease channel outside this feed. Verify the live manifest's version and both asset URLs after deployment. PyPI's latest-release state does not affect this URL. [Tauri GitHub pipeline](https://v2.tauri.app/distribute/pipelines/github/), [GitHub artifact attestations](https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations)

## Rollback and inventory

Suspend a bad rollout by withdrawing or restoring the previous stable pointer. Installed clients will only accept a newer version. Qualify a forward fix on the same project schema. If hands-on rollback is necessary, finish managed work, back up the project using a SQLite-consistent backup of `.cortexshift/` with all writers stopped, and save the app profile. Reinstall the previous verified app only after checking that it can read the existing schema. On Windows, manually uninstall before reinstalling an earlier installer. Retain the project data and verify the library and preferences afterward. If the signing key has been compromised, recover through a separately verified platform-signed installer.

Record the two native targets, Python interpreter and lockfile hashes for each candidate. Use `cargo tree --locked --target <target> -e normal,build` for the Rust graph, `npm sbom --package-lock-only --omit dev --sbom-format cyclonedx` for the renderer graph, `uv export --locked --no-dev --group desktop-build --no-emit-project` for the freeze inputs and `pyi-archive_viewer --list` for the resulting native bridge contents. See the [RustSec review](security.md) for unresolved upstream maintenance advisories.

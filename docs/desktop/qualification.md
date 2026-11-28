# Desktop release qualification

Record the candidate tag, Git commit, Actions run ID, installer SHA-256, signing publisher, OS version, architecture and pass or fail for each action. Use a test profile or VM and disposable projects. Review issues against this record before approving any publication.

## macOS Apple Silicon

1. Use a clean macOS 14 or later account or VM with no Python, Node, Rust, uv or provider auth installed for the exercise. Keep a clean image of the oldest supported OS for verification. Download the candidate, verify its digest, mount the disk image, drag CortexShift to Applications, eject and launch it.
2. Verify `codesign --verify --deep --strict /Applications/CortexShift.app`, `spctl --assess --type execute --verbose /Applications/CortexShift.app` and `xcrun stapler validate` for the app and DMG. Compare the Developer ID and Team ID to the release record. Validate the `.app.tar.gz` and version-bound minisign signature with the staged public key.
3. Confirm Settings & diagnostics shows the installed Desktop version, bridge protocol 1 and expected embedded core. Inspect an initialized workspace without provider auth. Use the native folder picker to select an existing project and view its Task, Session and checkpoint. Try a fresh project initialized by the independent CLI, a missing path and a folder without canonical state. Try a folder whose permissions are denied, then restore the permission and reopen it.
4. Confirm missing provider guidance, dark and light appearance, keyboard navigation and launcher icon. Finish the provider rows below independently from this installation check.

## Windows x64

1. Start with a clean Windows 11 x64 VM under an ordinary local user. Record WebView2's initial state. Check `Get-AuthenticodeSignature` on the installer. Run the per-user NSIS installer and launch CortexShift from Start. Confirm the installed app, bundled bridge and uninstaller have the expected timestamped publisher signature. If WebView2 is absent, confirm the online bootstrap path and the installer error when downloading is unavailable.
2. Exercise the same folder picker, malformed or missing path, Task and Session and no-provider scenarios as on macOS. Check Settings & diagnostics and the per-user profile paths documented in the [installation guide](install.md).
3. Install a previous supported release, register a populated project and set a non-default appearance. Close Desktop and run the new NSIS installer. Reopen and verify the version, library, theme, Tasks, Sessions and proofs. Use the CLI to read the project while Desktop is open and verify that Desktop reflects subsequent CLI changes on refresh.
4. Uninstall through Apps → Installed apps. Confirm app binaries and shortcuts were removed, then inspect the project through the independent CLI. Reinstall the candidate and verify the intended profile retention. Restore the clean VM snapshot between scenarios.

## Upgrade, rollback and updater

- On macOS, repeat the Windows previous-version profile exercise by replacing the earlier `.app` from the new DMG. Verify Task and Session IDs, checkpoint counts and appearance before and after. Try a rollback of the app files against a copied project and the documented compatible schema. Keep the same app identifier across all stages.
- Before routing any users to a stable release, use a controlled same-architecture test build with a separate disposable trust key and HTTPS staging feed. On both OSes, check a current feed and a newer signed candidate, read its notes, observe progress, install and confirm the version after restart. For the end-to-end staging exercise, change the feed and asset URL constants in a disposable checkout. Keep production verification rules and the same target layout.
- Test bad metadata, a missing platform, missing signature, tampered artifact, replay with a false higher version, unavailable asset, interrupted download, network timeout, and a read-only app location. Verify active or pending native workers can continue, and uncertain Session ownership defers installation. Repeat with a worker whose project was removed from the Desktop library. Confirm no valid document or installer bytes were replaced in failure cases.
- Repeat the previous-version upgrade with the exact signed candidate and save the evidence. Signed artifact assembly and automated smoke checks are inputs to this qualification. Publication approval is a separate action.

## Real provider matrix

Run with the operator's permission and their own provider accounts. Use a disposable project with a small Task and keep provider credentials and conversations outside release evidence. Record CLI versions, actual Session IDs, outcomes and observed handoff receipts.

| Scenario | Record |
| --- | --- |
| Launch Claude Code from Desktop | Native terminal, Task binding, context retrieval, observable Session exit |
| Launch Codex from Desktop | Native terminal, Task binding, context retrieval, observable Session exit |
| Switch Claude → Codex and Codex → Claude | Exact source Session, fresh handoff receipt and target reading current canonical Task state |
| Exact native resume where available | Stored exact native lineage, fresh handoff context and a new CortexShift invocation record |
| Fresh Task continuation | New provider invocation with Task binding and preserved previous history |
| Interrupt a provider terminal | Verified or uncertain ownership reported accurately and explicit recovery preview followed by recovery |
| Quit and reopen Desktop with an agent still running | Native terminal retains ownership, Desktop reconnects to durable status and refuses update installation |
| Provider executable missing | Setup guidance and local inspection available |
| Provider authentication rejected | Native CLI owns auth errors and display preserves truthful Session status |
| Quota exhausted or provider version incompatible | Observable native-provider outcome and no inference of an unverified reason |

Evidence from fake-provider tests covers Desktop's adapter path and lifecycle. Authenticated integrations need their own qualification results.

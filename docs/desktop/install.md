# Install and use CortexShift Desktop

CortexShift Desktop browses local Tasks, Sessions, checkpoints and handoffs and can start and switch supported coding agents in native terminal windows. Viewing an initialized workspace needs neither provider authentication nor a separate Python installation.

## macOS

Use a qualified Apple Silicon disk image on macOS 14 or later. Verify its SHA-256 against `SHA256SUMS` from the corresponding `desktop-vX.Y.Z` release. Open the disk image and drag CortexShift into Applications or a writable Applications folder in your home directory. Eject the disk image and launch the copied app. Contact the publisher if Gatekeeper cannot verify the release.

## Windows

Use the qualified Windows x64 `setup.exe` on Windows 11. Verify its SHA-256 with `Get-FileHash -Algorithm SHA256 .\CortexShift-Desktop-X.Y.Z-windows-x64-setup.exe` and check the publisher in the file's Digital Signatures tab. Run the per-user installer. Microsoft WebView2 may need to download on first install. Launch CortexShift from the Start menu. To remove it, use Windows Settings → Apps → Installed apps → CortexShift.

## First run

1. For a new directory, install the independently distributed CLI and run `cortexshift init` in that directory. For an existing workspace, locate its original project folder.
2. Open Desktop and select **Open Project**. Choose the initialized project root and confirm the Task and Session history.
3. For agent actions, install the provider's native Claude Code or Codex CLI. Use **Check setup** in the launch dialog to verify that Desktop finds the CLI in that workspace. Complete provider authentication in the provider's own terminal.
4. Use **Settings & diagnostics** to check the installed Desktop version, local bridge protocol and core version. The **Desktop updates** panel checks the configured stable channel for signed updates.

Project records live in `<project>/.cortexshift/`. On macOS, application support belongs to `~/Library/Application Support/com.cortexshift.desktop`. Windows stores the library under `%APPDATA%\com.cortexshift.desktop` and local action receipts under `%LOCALAPPDATA%\com.cortexshift.desktop`. The appearance preference belongs to the app WebView profile. Save project backups before moving folders or changing versions. Removing a project from the Desktop library only removes its reference.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Project missing or identity changed | Open the original initialized folder. Verify a moved workspace with the CLI before registering it again. |
| Python bridge failed | Check Settings & diagnostics, quit Desktop, and reopen the installed app. Reinstall the same verified build if the bundled helper is missing or quarantined. |
| Provider executable unavailable | Use **Check setup** in the launch dialog and verify the native provider CLI installation. Project inspection remains available. |
| Update deferred | Finish work in the provider terminal, cancel unclaimed actions, or review recovery for unfinalized Sessions. Repair or remove an unavailable library entry after confirming its workers have finished. |
| Update verification failed | Check the stable release's availability and consult the release notes or use the verified installer from the publisher. |
| Installer cannot download WebView2 | Connect to the network or provision the Microsoft Evergreen WebView2 runtime through a trusted administrator. |

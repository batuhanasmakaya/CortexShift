# ADR-0021 — Desktop distribution

**Status** Accepted

CortexShift Desktop and the PyPI CLI are independently released products. The Tauri configuration owns the Desktop version. npm and Cargo metadata mirror it, and the Desktop tag is `desktop-vX.Y.Z`. PyPI retains `vX.Y.Z`. A Desktop bundle embeds the locked Python core and uses the existing schema v6 and bridge protocol 1. The Desktop version can advance without changing the Python package version or migrating project data.

Development builds use the checkout. Pull requests build unsigned verification artifacts. An internal installer can be built from a clean revision with the updater unavailable. Signing jobs run only for the canonical repository, from a validated revision on protected main, after environment approval. A signed build is a candidate until both platforms pass installation and upgrade qualification. Publication requires a separate maintainer action.

Apple Silicon macOS and Windows x64 are the initial targets. Each platform bundles its matching PyInstaller runtime. macOS uses Developer ID, hardened runtime and Apple notarization. Windows uses per-user NSIS, Authenticode and the WebView2 download bootstrapper.

The native Tauri updater uses an embedded release public key and a dedicated HTTPS stable manifest. GitHub hosts the signed assets under the exact Desktop tag. An explicit install operation checks the current Desktop action receipts and registered workspaces, then shuts down the supervised request bridge before replacing the bundle. Pending or uncertain ownership defers installation. Native managed workers retain their existing lifecycle. See the [Desktop release guide](../desktop/releasing.md).

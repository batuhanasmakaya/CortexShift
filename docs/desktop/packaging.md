# Build CortexShift Desktop installers

The supported release targets are Apple Silicon on macOS 14 or later and Windows 11 x64. Build on each native platform with matching Rust and CPython architectures. The Rust target, Python interpreter and native wheel architectures must agree. Intel and universal macOS support require matching CPython builds and native extensions for both slices. PyInstaller can produce universal2 only when every collected binary provides both slices. A separately qualified Intel app would be simpler than merging two one-file sidecars. [PyInstaller macOS architecture notes](https://pyinstaller.org/en/v6.17.0/feature-notes.html#macos-multi-arch-support)

The app embeds a PyInstaller executable with Python and the native libraries needed by the bridge and managed terminal workers. Rust supervises the request bridge through private stdio. The same packaged executable serves managed MCP entry points. The Tauri installer owns app files. Project databases remain under the project folder and Desktop profile data remains in application support.

## Build for internal testing

Install the [development toolchain](development.md). From the repository root:

```sh
uv sync --locked --python 3.12.13 --no-dev --group desktop-build
uv run --locked --no-dev --group desktop-build python desktop/scripts/freeze_bridge.py
cd desktop
npm ci --ignore-scripts
npm run tauri -- build --ci --no-sign --target aarch64-apple-darwin --config src-tauri/bundle.conf.json --bundles app,dmg -- --locked
```

Windows uses the same commands with `--target x86_64-pc-windows-msvc` and `--bundles nsis`. NSIS installs for the current user. Its WebView2 download bootstrapper may use the network when the runtime is missing. An existing WebView2 installation permits the installer to skip that download. The packaged app supplies its own Python. [Tauri Windows installers and WebView2](https://v2.tauri.app/distribute/windows-installer/)

Bundle outputs are under `desktop/src-tauri/target/<target>/release/bundle/`. The macOS `.app` is inside the `.dmg`. The qualification helper mounts and copies the DMG or runs NSIS, opens a temporary app profile, checks the packaged executables and Python archive, and reads an isolated schema v6 project with Task and Session fixtures over the frozen bridge. Without `--skip-gui`, it also starts the installed application. Windows repeats the install and exercises its uninstaller.

```sh
uv run --locked --no-dev --group desktop-build python desktop/scripts/qualify.py path/to/installer
uv run --locked python desktop/scripts/release.py --bundle path/to/bundle --out desktop/dist-release --mac
```

Drop `--mac` on Windows. Pass `--previous-app path/to/previous.app` on macOS to exercise replacement and rollback against the same project fixture. Pass `--previous-installer path/to/previous-setup.exe` on Windows to exercise a version-to-version upgrade. Staged filenames include `unsigned` and the checksum file covers the installer bytes. Unsigned builds are internal artifacts. Developer ID, notarization, Gatekeeper, Authenticode, and a clean OS account are qualified separately. See [release engineering](releasing.md) and [qualification](qualification.md).

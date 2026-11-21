# Desktop packaging

The development app uses the checkout's uv environment. A packaged application embeds a native PyInstaller sidecar built from the locked Python project, including the interpreter and required libraries. Tauri packages it as an external binary. Rust opens the fixed executable alongside its own binary and supervises its private stdio pipes. The WebView receives no executable path or shell permission. [Tauri sidecar guide](https://v2.tauri.app/develop/sidecar/), [PyInstaller operating modes](https://pyinstaller.org/en/stable/operating-mode.html)

## Local macOS bundle

From the repository root after installing [development dependencies](development.md):

```sh
uv run --locked --with pyinstaller==6.17.0 python desktop/scripts/freeze_bridge.py
cd desktop
npm run tauri -- build --no-sign --config src-tauri/bundle.conf.json --bundles app -- --locked
open src-tauri/target/release/bundle/macos/CortexShift.app
```

The freeze script builds and smoke-tests a sidecar named for the host Rust target. The local app uses ad hoc or development signing where needed for the target. Distribution on macOS requires signing the app and its executable contents, notarization, and installation tests on supported Intel and Apple Silicon systems. Set a supported minimum OS from the strongest deployment requirement of the WebView, Python runtime, and native extensions. [Tauri macOS application bundles](https://v2.tauri.app/distribute/macos-application-bundle/), [Tauri macOS code signing](https://v2.tauri.app/distribute/sign/macos/)

## Windows strategy

Run the freeze script on a Windows MSVC host using CPython and Rust of the same architecture. After freezing, build with the same Tauri command and `--bundles nsis`. The Windows CI job compiles, tests and attempts an unsigned NSIS installer on a native runner. Packaging depends on WebView2 and the installer toolchain. Signing, WebView2 bootstrap scenarios, clean-machine installation, and native app startup on Windows require separate validation. [Tauri Windows installers](https://v2.tauri.app/distribute/windows-installer/)

The native CI jobs use a single Python version per platform and cache npm, Cargo downloads, and uv. The existing Python matrix continues to own CLI and TUI release validation. Desktop CI publishes no bundles and uses read-only repository permissions.

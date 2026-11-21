# Desktop development

On macOS, install Xcode or the Xcode command-line tools, uv, Node.js 24 LTS, and Rust through rustup. Set the active Apple developer tools with `xcode-select` if needed. Windows builds use the MSVC toolchain, Visual Studio C++ Build Tools with a Windows SDK, and WebView2. [Tauri prerequisites](https://v2.tauri.app/start/prerequisites/)

From the repository root:

```sh
rustup toolchain install 1.92.0 --profile minimal --component rustfmt,clippy
rustup default 1.92.0
uv sync --locked --python 3.12
cd desktop
npm ci --ignore-scripts
npm run tauri -- dev
```

Tauri starts the Vite server on `127.0.0.1:1420` and opens the native window. The development shell uses `.venv/bin/python` on macOS or `.venv/Scripts/python.exe` on Windows. Run the commands from the same checkout to install the editable Python module there.

For a new project, initialize it explicitly in a terminal.

```sh
cd /path/to/project
cortexshift init
```

Select **Open Project**, choose the initialized root, and use **New Task** to save an objective. Use a disposable workspace and fake provider executables for launch testing. Start the development application with the fake executable directory on its PATH, outside the test workspace. Confirm native terminal input, close the Desktop window while an invocation runs, and reopen Desktop to check its final Session receipt. The automated Desktop integration exercises Task creation, fake Claude and Codex switching, real MCP paging and write authority, and native resume.

## Checks

From the repository root:

```sh
uv run --locked pytest --no-cov tests/desktop
uv run --locked pytest -W error
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy src
```

From `desktop/`:

```sh
npm run lint
npm run typecheck
npm test
npm run build
cargo check --locked --manifest-path src-tauri/Cargo.toml
cargo test --locked --manifest-path src-tauri/Cargo.toml
cargo clippy --locked --manifest-path src-tauri/Cargo.toml --all-targets -- -D warnings
```

See [packaging](packaging.md) for a local app or installer build.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| The folder is uninitialized | Select the exact initialized root. To start tracking a new folder, run `cortexshift init` there first. |
| Desktop reports an identity mismatch | Reopen the original recorded workspace. If a different project was explicitly initialized at the same path, remove the obsolete library entry and register it again. |
| Schema upgrade required | Run `cortexshift init` with the matching CLI to apply supported migrations. Desktop reads schema v6. |
| The project is busy | Refresh when the competing SQLite transaction finishes. An observed managed workspace lock by itself allows Desktop reads. |
| Bridge unavailable in development | Run `uv sync --locked --python 3.12` in the source checkout and check the bridge with `uv run --locked python -I -m cortexshift.desktop.bridge` and a `hello` frame. |
| Sidecar fails after packaging | Verify the bridge sits beside the installed native executable and matches its architecture. Check **Settings & diagnostics** for the protocol status. |
| Port 1420 is in use | Stop the other development server and rerun Tauri. |
| Session is marked ownership uncertain | Review the live workspace lock observation and recorded Session state. Use **Review recovery** to preview eligible Sessions. If ownership remains uncertain, finish or check the provider through its usual controls before proceeding. |
| Provider unavailable or setup incomplete | Install the native CLI and make it available to Desktop. Relaunch Desktop with the intended PATH if your shell adds provider install directories. Use **Check setup** in the launch dialog or sign in through the native CLI. Windows launch expects native executables. |
| Terminal remains pending | Check whether a terminal opened. Use **Cancel pending launch** before retrying if the worker has not claimed it. |
| Task saved, launch failed | Open the saved Task and choose **Start agent**. Use Session history to check the failure and retry the existing Task. |
| Bridge timed out while submitting | Refresh action status and inspect canonical Tasks or Sessions. Use the same creation ID from the open New Task dialog to check or retry a Task write. |
| Native resume rejected | Inspect the provider terminal and Session record. If the conversation is unavailable, use **Continue Task fresh** for an eligible Session. |

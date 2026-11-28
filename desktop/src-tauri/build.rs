fn main() {
    // tauri-build 2.7 links the Common Controls manifest into the app binary only.
    // The standalone updater test also imports TaskDialogIndirect from Common Controls v6.
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("windows")
        && std::env::var("CARGO_CFG_TARGET_ENV").as_deref() == Ok("msvc")
    {
        println!("cargo::rustc-link-arg-tests=/MANIFEST:EMBED");
        println!(
            "cargo::rustc-link-arg-tests=/MANIFESTDEPENDENCY:type='win32' \
             name='Microsoft.Windows.Common-Controls' version='6.0.0.0' \
             processorArchitecture='*' publicKeyToken='6595b64144ccf1df' language='*'"
        );
    }

    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&[
            "library",
            "open_project",
            "remove_project",
            "query",
            "create_task",
            "agent_control",
            "pick_objective",
            "update_info",
            "check_update",
            "install_update",
        ]),
    ))
    .expect("configure CortexShift desktop permissions");
}

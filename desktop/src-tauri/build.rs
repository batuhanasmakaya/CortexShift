fn main() {
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&[
            "library",
            "open_project",
            "remove_project",
            "query",
        ]),
    ))
    .expect("configure CortexShift desktop permissions");
}

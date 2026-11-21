fn main() {
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&[
            "library",
            "open_project",
            "remove_project",
            "query",
            "create_task",
            "agent_control",
            "pick_objective",
        ]),
    ))
    .expect("configure CortexShift desktop permissions");
}

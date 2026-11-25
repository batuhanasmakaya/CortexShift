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
            "update_info",
            "check_update",
            "install_update",
        ]),
    ))
    .expect("configure CortexShift desktop permissions");
}

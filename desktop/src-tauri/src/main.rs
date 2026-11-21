#![cfg_attr(
    all(not(debug_assertions), target_os = "windows"),
    windows_subsystem = "windows"
)]

mod bridge;
mod control;
mod library;

use bridge::{Bridge, BridgeError};
use control::{ControlRequest, CreateTask};
use library::{Library, Project};
use serde::Deserialize;
use serde_json::{json, Value};
use tauri::{Manager, State};
use tauri_plugin_dialog::DialogExt;
use tokio::sync::{oneshot, Semaphore};

#[derive(Deserialize)]
#[serde(tag = "op", rename_all = "snake_case", deny_unknown_fields)]
enum Query {
    Hello,
    Overview {
        root: String,
    },
    Tasks {
        root: String,
        offset: u32,
    },
    Task {
        root: String,
        task_id: String,
    },
    Objective {
        root: String,
        task_id: String,
        offset: u32,
    },
    Sessions {
        root: String,
        task_id: Option<String>,
        offset: u32,
    },
    Checkpoints {
        root: String,
        task_id: Option<String>,
        offset: u32,
    },
    Handoffs {
        root: String,
        task_id: Option<String>,
        offset: u32,
    },
    Session {
        root: String,
        record_id: String,
    },
    Checkpoint {
        root: String,
        record_id: String,
    },
    Handoff {
        root: String,
        record_id: String,
    },
}

struct Picker(Semaphore);

#[tauri::command]
fn library(store: State<'_, Library>) -> Result<Vec<Project>, BridgeError> {
    store.list()
}

#[tauri::command]
fn remove_project(root: String, store: State<'_, Library>) -> Result<Vec<Project>, BridgeError> {
    store.remove(&root)
}

#[tauri::command]
async fn open_project(
    app: tauri::AppHandle,
    bridge: State<'_, Bridge>,
    store: State<'_, Library>,
    picker: State<'_, Picker>,
) -> Result<Option<Project>, BridgeError> {
    let _permit = picker
        .0
        .try_acquire()
        .map_err(|_| BridgeError::new("picker_open", "A project picker is already open."))?;
    let (sender, receiver) = oneshot::channel();
    app.dialog()
        .file()
        .set_title("Open a CortexShift project")
        .pick_folder(move |path| {
            let _ = sender.send(path);
        });
    let picked = receiver
        .await
        .map_err(|_| BridgeError::new("picker_closed", "The folder picker closed unexpectedly."))?;
    let Some(path) = picked else {
        return Ok(None);
    };
    let path = path.into_path().map_err(|_| invalid_path())?;
    let result = bridge
        .request(
            "verify",
            json!({"root": path.to_str().ok_or_else(invalid_path)?}),
        )
        .await?;
    let project = Project {
        root: result["root"].as_str().ok_or_else(invalid_path)?.into(),
        project_id: result["project_id"]
            .as_str()
            .ok_or_else(invalid_path)?
            .into(),
    };
    store.register(project).map(Some)
}

fn invalid_path() -> BridgeError {
    BridgeError::new(
        "invalid_project",
        "Choose a local project folder with a supported path.",
    )
}

#[tauri::command]
async fn create_task(
    request: CreateTask,
    bridge: State<'_, Bridge>,
    store: State<'_, Library>,
) -> Result<Value, BridgeError> {
    let project = store.get(&request.root)?;
    control::create_task(&bridge, project, request).await
}

#[tauri::command]
async fn agent_control(
    request: ControlRequest,
    app: tauri::AppHandle,
    bridge: State<'_, Bridge>,
    store: State<'_, Library>,
) -> Result<Value, BridgeError> {
    let project = store.get(request.root())?;
    let directory = app.path().app_local_data_dir().map_err(|_| {
        BridgeError::new(
            "state_unavailable",
            "Desktop action storage is unavailable.",
        )
    })?;
    control::dispatch(&bridge, project, request, &directory.join("actions")).await
}

#[tauri::command]
async fn pick_objective(
    app: tauri::AppHandle,
    picker: State<'_, Picker>,
) -> Result<Option<control::ObjectiveFile>, BridgeError> {
    let _permit = picker
        .0
        .try_acquire()
        .map_err(|_| BridgeError::new("picker_open", "A native file picker is already open."))?;
    let (sender, receiver) = oneshot::channel();
    app.dialog()
        .file()
        .set_title("Choose a Markdown objective")
        .add_filter("Markdown", &["md", "markdown"])
        .pick_file(move |path| {
            let _ = sender.send(path);
        });
    let Some(path) = receiver
        .await
        .map_err(|_| BridgeError::new("picker_closed", "The file picker closed unexpectedly."))?
    else {
        return Ok(None);
    };
    let path = path.into_path().map_err(|_| control::invalid_file())?;
    tauri::async_runtime::spawn_blocking(move || control::read_objective(&path))
        .await
        .map_err(|_| control::invalid_file())?
        .map(Some)
}

#[tauri::command]
async fn query(
    request: Query,
    bridge: State<'_, Bridge>,
    store: State<'_, Library>,
) -> Result<Value, BridgeError> {
    let (op, root, mut params) = match request {
        Query::Hello => return bridge.request("hello", json!({})).await,
        Query::Overview { root } => ("overview", root, json!({})),
        Query::Tasks { root, offset } => ("tasks", root, json!({ "offset": offset })),
        Query::Task { root, task_id } => ("task", root, json!({ "task_id": task_id })),
        Query::Objective {
            root,
            task_id,
            offset,
        } => (
            "objective",
            root,
            json!({ "task_id": task_id, "offset": offset }),
        ),
        Query::Sessions {
            root,
            task_id,
            offset,
        } => (
            "sessions",
            root,
            json!({ "task_id": task_id, "offset": offset }),
        ),
        Query::Checkpoints {
            root,
            task_id,
            offset,
        } => (
            "checkpoints",
            root,
            json!({ "task_id": task_id, "offset": offset }),
        ),
        Query::Handoffs {
            root,
            task_id,
            offset,
        } => (
            "handoffs",
            root,
            json!({ "task_id": task_id, "offset": offset }),
        ),
        Query::Session { root, record_id } => ("session", root, json!({ "record_id": record_id })),
        Query::Checkpoint { root, record_id } => {
            ("checkpoint", root, json!({ "record_id": record_id }))
        }
        Query::Handoff { root, record_id } => ("handoff", root, json!({ "record_id": record_id })),
    };
    let project = store.get(&root)?;
    params["root"] = json!(project.root);
    params["project_id"] = json!(project.project_id);
    bridge.request(op, params).await
}

fn main() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _, _| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_dialog::init())
        .manage(Bridge::default())
        .manage(Picker(Semaphore::new(1)))
        .invoke_handler(tauri::generate_handler![
            library,
            open_project,
            remove_project,
            query,
            create_task,
            agent_control,
            pick_objective
        ])
        .setup(|app| {
            app.manage(Library::new(
                app.path().app_config_dir()?.join("projects.json"),
            ));
            tauri::WebviewWindowBuilder::from_config(app, &app.config().app.windows[0])?
                .on_navigation(|url| {
                    let local = matches!(
                        (url.scheme(), url.host_str(), url.port()),
                        ("tauri", Some("localhost"), None)
                            | ("http", Some("tauri.localhost"), None)
                    );
                    local
                        || (cfg!(debug_assertions)
                            && url.scheme() == "http"
                            && url.host_str() == Some("127.0.0.1")
                            && url.port() == Some(1420))
                })
                .on_new_window(|_, _| tauri::webview::NewWindowResponse::Deny)
                .build()?;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("start CortexShift Desktop");
    app.run(|app, event| {
        if let tauri::RunEvent::ExitRequested {
            code: None, api, ..
        } = event
        {
            api.prevent_exit();
            let handle = app.clone();
            tauri::async_runtime::spawn(async move {
                handle.state::<Bridge>().shutdown().await;
                handle.exit(0);
            });
        }
    });
}

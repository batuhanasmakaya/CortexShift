use crate::bridge::{Bridge, BridgeError};
use crate::library::Project;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::fs::File;
use std::io::Read;
use std::path::Path;

// SQLite's default length ceiling, also enforced by the Python stream reader.
const OBJECTIVE_BYTES: usize = 1_000_000_000;
type Outcome<T> = Result<T, BridgeError>;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CreateTask {
    pub root: String,
    task_id: String,
    title: String,
    objective: String,
    activate: bool,
    expected_active: Option<String>,
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
enum ActionKind {
    Start,
    Resume,
    Continue,
    Switch,
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Provider {
    Claude,
    Codex,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct AgentAction {
    kind: ActionKind,
    task_id: String,
    provider: Option<Provider>,
    session_id: Option<String>,
    fresh_target: bool,
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
enum LaunchMode {
    Start,
    NativeResume,
    TaskContinuation,
    SwitchNative,
    SwitchFresh,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct AgentPlan {
    provider: Provider,
    mode: LaunchMode,
    source: Option<String>,
    target: Option<String>,
    bootstrap_turn: bool,
}

#[derive(Deserialize)]
#[serde(tag = "op", rename_all = "snake_case", deny_unknown_fields)]
pub enum ControlRequest {
    ProviderSetup {
        root: String,
        provider: Provider,
    },
    Providers {
        root: String,
    },
    Plan {
        root: String,
        action: AgentAction,
    },
    Submit {
        root: String,
        action: AgentAction,
        plan: AgentPlan,
        operation_id: String,
    },
    ActionStatus {
        root: String,
    },
    CancelAction {
        root: String,
        action_id: String,
    },
    ActivateTask {
        root: String,
        task_id: String,
        expected_active: Option<String>,
    },
    Recover {
        root: String,
        preview: bool,
    },
}

impl ControlRequest {
    pub fn root(&self) -> &str {
        match self {
            Self::Providers { root }
            | Self::ProviderSetup { root, .. }
            | Self::Plan { root, .. }
            | Self::Submit { root, .. }
            | Self::ActionStatus { root }
            | Self::CancelAction { root, .. }
            | Self::ActivateTask { root, .. }
            | Self::Recover { root, .. } => root,
        }
    }
}

pub async fn dispatch(
    bridge: &Bridge,
    project: Project,
    request: ControlRequest,
    directory: &Path,
) -> Outcome<Value> {
    let binding = |mut params: Value| {
        params["root"] = json!(project.root);
        params["project_id"] = json!(project.project_id);
        params
    };
    let private_directory = || {
        directory.to_str().ok_or_else(|| {
            BridgeError::new(
                "state_unavailable",
                "Desktop action storage has an unsupported path.",
            )
        })
    };
    let (op, params) = match request {
        ControlRequest::ProviderSetup { provider, .. } => {
            ("provider_setup", json!({ "provider": provider }))
        }
        ControlRequest::Providers { .. } => ("providers", json!({})),
        ControlRequest::Plan { action, .. } => ("plan", json!({ "action": action })),
        ControlRequest::Submit {
            action,
            plan,
            operation_id,
            ..
        } => (
            "submit",
            json!({ "action": action, "plan": plan, "operation_id": operation_id, "control_dir": private_directory()? }),
        ),
        ControlRequest::ActionStatus { .. } => (
            "action_status",
            json!({ "control_dir": private_directory()? }),
        ),
        ControlRequest::CancelAction { action_id, .. } => (
            "cancel_action",
            json!({ "action_id": action_id, "control_dir": private_directory()? }),
        ),
        ControlRequest::ActivateTask {
            task_id,
            expected_active,
            ..
        } => (
            "activate_task",
            json!({ "task_id": task_id, "expected_active": expected_active }),
        ),
        ControlRequest::Recover { preview, .. } => ("recover", json!({ "preview": preview })),
    };
    bridge.request(op, binding(params)).await
}

pub async fn create_task(bridge: &Bridge, project: Project, request: CreateTask) -> Outcome<Value> {
    let bytes = request.objective.as_bytes();
    if bytes.len() > OBJECTIVE_BYTES || request.objective.trim().is_empty() {
        return Err(BridgeError::new(
            "invalid_objective",
            "Enter an objective that fits in project storage.",
        ));
    }
    bridge
        .request_with_payload(
            "create_task",
            json!({
                "root": project.root, "project_id": project.project_id, "task_id": request.task_id,
                "title": request.title, "activate": request.activate,
                "expected_active": request.expected_active, "objective_bytes": bytes.len(),
            }),
            bytes,
        )
        .await
}

#[derive(Serialize)]
pub struct ObjectiveFile {
    name: String,
    text: String,
}

pub fn invalid_file() -> BridgeError {
    BridgeError::new("invalid_file", "Choose a readable UTF-8 Markdown file.")
}

pub fn read_objective(path: &Path) -> Outcome<ObjectiveFile> {
    let extension = path
        .extension()
        .and_then(|ext| ext.to_str())
        .unwrap_or_default();
    if !path.is_absolute()
        || !matches!(extension.to_ascii_lowercase().as_str(), "md" | "markdown")
        || !path.symlink_metadata().is_ok_and(|meta| meta.is_file())
    {
        return Err(invalid_file());
    }
    let file = File::open(path).map_err(|_| invalid_file())?;
    let meta = file.metadata().map_err(|_| invalid_file())?;
    if !meta.is_file() || meta.len() > OBJECTIVE_BYTES as u64 {
        return Err(invalid_file());
    }
    let mut text = String::new();
    file.take(OBJECTIVE_BYTES as u64 + 1)
        .read_to_string(&mut text)
        .map_err(|_| invalid_file())?;
    if text.len() > OBJECTIVE_BYTES || text.trim().is_empty() {
        return Err(invalid_file());
    }
    let name = path
        .file_name()
        .and_then(|name| name.to_str())
        .ok_or_else(invalid_file)?;
    Ok(ObjectiveFile {
        name: name.into(),
        text,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn picked_file_preserves_markdown_and_validates_kind_and_encoding() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("Objective.MD");
        let objective = "  # Unicode 🧪\r\n\n  ";
        std::fs::write(&path, objective).unwrap();
        assert_eq!(read_objective(&path).unwrap().text, objective);
        std::fs::write(&path, [255]).unwrap();
        assert!(read_objective(&path).is_err());
        assert!(read_objective(temp.path()).is_err());
    }

    #[test]
    fn rejects_renderer_paths_and_unknown_providers() {
        let json = json!({
            "op": "submit", "root": "/repo", "control_dir": "/tmp", "operation_id": "test",
            "action": { "kind": "start", "task_id": "task_ok", "provider": "claude", "fresh_target": false },
            "plan": { "provider": "claude", "mode": "start", "bootstrap_turn": false },
        });
        assert!(serde_json::from_value::<ControlRequest>(json.clone()).is_err());
        let mut request = json;
        request.as_object_mut().unwrap().remove("control_dir");
        assert!(serde_json::from_value::<ControlRequest>(request.clone()).is_ok());
        request["action"]["provider"] = json!("arbitrary");
        assert!(serde_json::from_value::<ControlRequest>(request).is_err());
    }
}

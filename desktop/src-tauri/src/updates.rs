use crate::bridge::{Bridge, BridgeError};
use crate::library::Library;
use semver::Version;
use serde::Serialize;
use serde_json::{json, Value};
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;
use tauri::{ipc::Channel, AppHandle, Manager, State, Url};
use tauri_plugin_updater::{Error, Update, UpdaterExt};
use tokio::sync::Mutex;

const FEED: &str = "https://batuhanasmakaya.github.io/CortexShift/desktop/stable.json";
const DOWNLOADS: &str = "https://github.com/batuhanasmakaya/CortexShift/releases/download";

#[derive(Default)]
pub struct Updates {
    pub workspace_changes: Mutex<()>,
    candidate: Mutex<Option<Update>>,
    installed: AtomicBool,
}

#[derive(Serialize)]
pub struct UpdateInfo {
    version: String,
    available: bool,
    restart_required: bool,
}

#[derive(Serialize)]
pub struct Offer {
    version: String,
    notes: Option<String>,
}

#[derive(Serialize)]
pub struct Progress {
    received: u64,
    total: Option<u64>,
}

fn asset_suffix(os: &str, arch: &str) -> Option<&'static str> {
    match (os, arch) {
        ("macos", "aarch64") => Some("macos-arm64.app.tar.gz"),
        ("windows", "x86_64") => Some("windows-x64-setup.exe"),
        _ => None,
    }
}

pub fn configured(app: &AppHandle) -> bool {
    let Some(config) = app.config().plugins.0.get("updater") else {
        return false;
    };
    asset_suffix(std::env::consts::OS, std::env::consts::ARCH).is_some()
        && config["pubkey"].as_str().is_some_and(|key| !key.is_empty())
        && config["requireSignedVersion"] == true
        && config["endpoints"] == json!([FEED])
        && [
            "dangerousInsecureTransportProtocol",
            "dangerousAcceptInvalidCerts",
            "dangerousAcceptInvalidHostnames",
            "allowDowngrades",
        ]
        .iter()
        .all(|key| config.get(key).is_none_or(|value| value == false))
}

fn stable_upgrade(current: &Version, offered: &Version) -> bool {
    offered.pre.is_empty() && offered.build.is_empty() && offered > current
}

fn valid_asset(url: &Url, version: &str, suffix: &str) -> bool {
    url.as_str() == format!("{DOWNLOADS}/desktop-v{version}/CortexShift-Desktop-{version}-{suffix}")
}

#[tauri::command]
pub fn update_info(app: AppHandle, updates: State<'_, Updates>) -> UpdateInfo {
    UpdateInfo {
        version: app.package_info().version.to_string(),
        available: configured(&app),
        restart_required: updates.installed.load(Ordering::Acquire),
    }
}

#[tauri::command]
pub async fn check_update(
    app: AppHandle,
    updates: State<'_, Updates>,
) -> Result<Option<Offer>, BridgeError> {
    let mut candidate = updates.candidate.try_lock().map_err(|_| busy())?;
    *candidate = None;
    if !configured(&app) || updates.installed.load(Ordering::Acquire) {
        return Err(BridgeError::new(
            "updates_unavailable",
            "Updates are unavailable for this build or until Desktop restarts.",
        ));
    }
    let updater = app
        .updater_builder()
        .timeout(Duration::from_secs(15))
        .version_comparator(|current, release| stable_upgrade(&current, &release.version))
        .restart_after_install(false)
        .configure_client(|client| client.https_only(true))
        .build()
        .map_err(update_error)?;
    let Some(mut update) = updater.check().await.map_err(update_error)? else {
        return Ok(None);
    };
    let suffix = asset_suffix(std::env::consts::OS, std::env::consts::ARCH).ok_or_else(|| {
        BridgeError::new(
            "unsupported_platform",
            "This platform has no Desktop updates.",
        )
    })?;
    if !valid_asset(&update.download_url, &update.version, suffix)
        || update.signature.trim().is_empty()
    {
        return Err(BridgeError::new(
            "invalid_update",
            "The update manifest does not identify a signed Desktop release asset.",
        ));
    }
    update.timeout = Some(Duration::from_secs(180));
    let offer = Offer {
        version: update.version.clone(),
        notes: update.body.clone(),
    };
    *candidate = Some(update);
    Ok(Some(offer))
}

#[tauri::command]
pub async fn install_update(
    app: AppHandle,
    updates: State<'_, Updates>,
    bridge: State<'_, Bridge>,
    store: State<'_, Library>,
    progress: Channel<Progress>,
) -> Result<(), BridgeError> {
    let mut candidate = updates.candidate.try_lock().map_err(|_| busy())?;
    let update = candidate
        .as_ref()
        .ok_or_else(|| BridgeError::new("no_update", "Check for an update first."))?;
    let mut received = 0;
    let bytes = update
        .download(
            |size, total| {
                received += size as u64;
                let _ = progress.send(Progress { received, total });
            },
            || {},
        )
        .await
        .map_err(update_error)?;

    // Downloading is safe while agents run. Serialize the final checks with new submissions,
    // library changes and app shutdown. Workers with existing tickets are checked by the bridge.
    let _change = updates.workspace_changes.lock().await;
    require_idle(&app, &bridge, &store).await?;
    bridge.shutdown().await;
    let update = candidate
        .take()
        .expect("candidate held throughout installation");
    let result = tauri::async_runtime::spawn_blocking(move || update.install(bytes)).await;
    if !matches!(result, Ok(Ok(()))) {
        bridge.reopen();
        return Err(BridgeError::new("install_failed", "Desktop could not install the verified update. Check application folder permissions or use the signed installer."));
    }
    updates.installed.store(true, Ordering::Release);
    Ok(())
}

async fn require_idle(
    app: &AppHandle,
    bridge: &Bridge,
    store: &Library,
) -> Result<(), BridgeError> {
    let projects = store.list().map_err(|_| ownership_unknown())?;
    let directory = app
        .path()
        .app_local_data_dir()
        .map_err(|_| ownership_unknown())?
        .join("actions");
    let result = bridge.request("update_guard", json!({
        "control_dir": directory.to_str().ok_or_else(ownership_unknown)?,
        "project_ids": projects.iter().map(|project| &project.project_id).collect::<Vec<_>>(),
    })).await.map_err(|_| ownership_unknown())?;
    match result["busy"].as_bool() {
        Some(false) => {}
        Some(true) => return Err(active_work()),
        None => return Err(ownership_unknown()),
    }
    for project in projects {
        let status = bridge
            .request("overview", json!(project))
            .await
            .map_err(|_| ownership_unknown())?;
        verify_workspace_idle(&status)?;
    }
    Ok(())
}

fn verify_workspace_idle(status: &Value) -> Result<(), BridgeError> {
    match (
        status["workspace_busy"].as_bool(),
        status["unfinalized_sessions"].as_u64(),
    ) {
        (Some(false), Some(0)) => Ok(()),
        (Some(_), Some(_)) => Err(active_work()),
        _ => Err(ownership_unknown()),
    }
}

fn busy() -> BridgeError {
    BridgeError::new("update_busy", "An update operation is in progress.")
}

fn active_work() -> BridgeError {
    BridgeError::new("active_work", "End active work in the native terminal, cancel pending launches, or review recovery before installing.")
}

fn ownership_unknown() -> BridgeError {
    BridgeError::new("ownership_unknown", "Desktop could not verify worker ownership. Check unavailable projects and review recovery before installing.")
}

fn update_error(error: Error) -> BridgeError {
    match error {
        Error::Minisign(_)
        | Error::Base64(_)
        | Error::SignatureUtf8(_)
        | Error::SignedVersionMismatch { .. }
        | Error::MissingSignedVersion => BridgeError::new(
            "invalid_signature",
            "The update signature could not be verified. Installation was deferred.",
        ),
        Error::Reqwest(error) if error.is_decode() => BridgeError::new(
            "invalid_update",
            "The stable update feed returned invalid metadata.",
        ),
        Error::Reqwest(_) | Error::Network(_) => BridgeError::new(
            "update_network",
            "The update could not be fetched. Check the connection and try again.",
        ),
        Error::TargetNotFound(_)
        | Error::TargetsNotFound(_)
        | Error::UnsupportedArch
        | Error::UnsupportedOs => BridgeError::new(
            "unsupported_platform",
            "The update is unavailable for this platform.",
        ),
        _ => BridgeError::new(
            "invalid_update",
            "The stable update feed is unavailable or returned invalid metadata.",
        ),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_newer_stable_versions_are_offered() {
        let current = Version::parse("0.2.0").unwrap();
        for candidate in ["0.2.0", "0.1.0", "0.2.0+build", "0.3.0-rc.1"] {
            assert!(!stable_upgrade(
                &current,
                &Version::parse(candidate).unwrap()
            ));
        }
        assert!(stable_upgrade(&current, &Version::parse("0.3.0").unwrap()));
    }

    #[test]
    fn download_requires_the_exact_tag_and_platform_asset() {
        let suffix = asset_suffix("macos", "aarch64").unwrap();
        let valid = format!("{DOWNLOADS}/desktop-v0.3.0/CortexShift-Desktop-0.3.0-{suffix}");
        assert!(valid_asset(&Url::parse(&valid).unwrap(), "0.3.0", suffix));
        assert!(!valid_asset(&Url::parse(&valid).unwrap(), "0.4.0", suffix));
        assert!(!valid_asset(
            &Url::parse(&format!("{valid}?extra=1")).unwrap(),
            "0.3.0",
            suffix
        ));
        assert!(asset_suffix("macos", "x86_64").is_none());
    }

    #[test]
    fn active_or_unverified_workspace_defers_installation() {
        assert!(
            verify_workspace_idle(&json!({"workspace_busy":false,"unfinalized_sessions":0}))
                .is_ok()
        );
        assert_eq!(
            verify_workspace_idle(&json!({"workspace_busy":true,"unfinalized_sessions":0}))
                .unwrap_err()
                .code,
            "active_work"
        );
        assert_eq!(
            verify_workspace_idle(&json!({"workspace_busy":false,"unfinalized_sessions":1}))
                .unwrap_err()
                .code,
            "active_work"
        );
        assert_eq!(
            verify_workspace_idle(&json!({"workspace_busy":null,"unfinalized_sessions":0}))
                .unwrap_err()
                .code,
            "ownership_unknown"
        );
    }
}

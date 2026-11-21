use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
#[cfg(debug_assertions)]
use std::path::PathBuf;
use std::process::Stdio;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Duration;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::process::{Child, ChildStdin, ChildStdout, Command};
use tokio::sync::{watch, Mutex, Semaphore};
use tokio::time::timeout;

const PROTOCOL: u32 = 1;
const FRAME_LIMIT: usize = 512 * 1024;
const REQUEST_LIMIT: usize = 32 * 1024;
const DEADLINE: Duration = Duration::from_secs(8);
const STARTUP_DEADLINE: Duration = Duration::from_secs(15);
const STOP_GRACE: Duration = Duration::from_millis(750);

type Outcome<T> = Result<T, BridgeError>;

#[derive(Debug, Deserialize, Serialize)]
pub struct BridgeError {
    pub code: String,
    pub message: String,
}

impl BridgeError {
    pub fn new(code: &str, message: &str) -> Self {
        Self {
            code: code.into(),
            message: message.into(),
        }
    }

    fn shutdown() -> Self {
        Self::new("shutdown", "CortexShift Desktop is closing.")
    }

    fn protocol() -> Self {
        Self::new(
            "bridge_protocol",
            "The local bridge returned an incompatible response.",
        )
    }

    fn exited() -> Self {
        Self::new(
            "bridge_exited",
            "The local bridge stopped. Refresh to start it again.",
        )
    }

    fn timed_out() -> Self {
        Self::new(
            "bridge_timeout",
            "The local bridge timed out. Refresh to try again.",
        )
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Envelope {
    version: u32,
    id: u64,
    result: Option<Value>,
    error: Option<BridgeError>,
}

pub struct Bridge {
    worker: Mutex<Option<Worker>>,
    pending: Semaphore,
    sequence: AtomicU64,
    closing: watch::Sender<bool>,
}

impl Default for Bridge {
    fn default() -> Self {
        Self {
            worker: Mutex::new(None),
            pending: Semaphore::new(16),
            sequence: AtomicU64::new(2),
            closing: watch::channel(false).0,
        }
    }
}

impl Bridge {
    pub async fn request(&self, op: &str, params: Value) -> Outcome<Value> {
        self.request_with_payload(op, params, &[]).await
    }

    pub async fn request_with_payload(
        &self,
        op: &str,
        mut params: Value,
        payload: &[u8],
    ) -> Outcome<Value> {
        let mut closing = self.closing.subscribe();
        if *closing.borrow() {
            return Err(BridgeError::shutdown());
        }
        let _permit = self.pending.try_acquire().map_err(|_| {
            BridgeError::new("bridge_busy", "The bridge is busy. Refresh to try again.")
        })?;
        let id = self.sequence.fetch_add(1, Ordering::Relaxed);
        params["version"] = json!(PROTOCOL);
        params["id"] = json!(id);
        params["op"] = json!(op);

        let mut guard = tokio::select! {
            _ = closing.changed() => return Err(BridgeError::shutdown()),
            guard = timeout(STARTUP_DEADLINE + DEADLINE, self.worker.lock()) => {
                guard.map_err(|_| BridgeError::timed_out())?
            }
        };
        let mut worker = match guard.take() {
            Some(worker) => worker,
            None => {
                let mut worker = Worker::spawn(bridge_command()?)?;
                let hello = json!({"version": PROTOCOL, "id": 1, "op": "hello"});
                let result = tokio::select! {
                    _ = closing.changed() => Err(BridgeError::shutdown()),
                    result = timeout(STARTUP_DEADLINE, worker.exchange(1, hello, &[])) => {
                        result.unwrap_or_else(|_| Err(BridgeError::timed_out()))
                    }
                };
                match result {
                    Ok(Ok(info))
                        if info["protocol"] == PROTOCOL && info["core_version"].is_string() => {}
                    Ok(_) => {
                        worker.terminate().await;
                        return Err(BridgeError::protocol());
                    }
                    Err(error) => {
                        worker.terminate().await;
                        return Err(error);
                    }
                }
                worker
            }
        };

        let exchange_deadline = if op == "provider_setup" {
            Duration::from_secs(12)
        } else {
            DEADLINE
        };
        match tokio::select! {
            _ = closing.changed() => {
                worker.stop().await;
                return Err(BridgeError::shutdown());
            },
            result = timeout(exchange_deadline, worker.exchange(id, params, payload)) => {
                result.unwrap_or_else(|_| Err(BridgeError::timed_out()))
            }
        } {
            Ok(response) => {
                *guard = Some(worker);
                response
            }
            Err(error) => {
                worker.terminate().await;
                Err(error)
            }
        }
    }

    pub async fn shutdown(&self) {
        self.closing.send_replace(true);
        if let Some(worker) = self.worker.lock().await.take() {
            worker.stop().await;
        }
    }
}

struct Worker {
    child: Child,
    input: ChildStdin,
    output: BufReader<ChildStdout>,
}

impl Worker {
    fn spawn(mut command: Command) -> Outcome<Self> {
        let mut child = command
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .kill_on_drop(true)
            .spawn()
            .map_err(|_| {
                BridgeError::new(
                    "bridge_unavailable",
                    "The local bridge could not start. Check the Desktop installation or development environment.",
                )
            })?;

        Ok(Self {
            input: child.stdin.take().ok_or_else(BridgeError::exited)?,
            output: BufReader::new(child.stdout.take().ok_or_else(BridgeError::exited)?),
            child,
        })
    }

    async fn exchange(&mut self, id: u64, frame: Value, payload: &[u8]) -> Outcome<Outcome<Value>> {
        let bytes = serde_json::to_vec(&frame).map_err(|_| BridgeError::protocol())?;
        if bytes.len() > REQUEST_LIMIT {
            return Ok(Err(BridgeError::new(
                "invalid_request",
                "The workspace request is too large.",
            )));
        }
        self.input
            .write_all(&bytes)
            .await
            .map_err(|_| BridgeError::exited())?;
        self.input
            .write_all(b"\n")
            .await
            .map_err(|_| BridgeError::exited())?;
        self.input
            .write_all(payload)
            .await
            .map_err(|_| BridgeError::exited())?;
        self.input
            .flush()
            .await
            .map_err(|_| BridgeError::exited())?;

        let mut line = Vec::new();
        loop {
            let chunk = self
                .output
                .fill_buf()
                .await
                .map_err(|_| BridgeError::exited())?;
            if chunk.is_empty() {
                return Err(BridgeError::exited());
            }
            let newline = chunk.iter().position(|&byte| byte == b'\n');
            let length = newline.unwrap_or(chunk.len());
            if line.len() + length > FRAME_LIMIT {
                return Err(BridgeError::protocol());
            }
            line.extend_from_slice(&chunk[..length]);
            self.output.consume(length + usize::from(newline.is_some()));
            if newline.is_some() {
                break;
            }
        }
        let reply: Envelope = serde_json::from_slice(&line).map_err(|_| BridgeError::protocol())?;
        if reply.version != PROTOCOL || reply.id != id {
            return Err(BridgeError::protocol());
        }
        match (reply.result, reply.error) {
            (Some(value), None) => Ok(Ok(value)),
            (None, Some(error)) => Ok(Err(error)),
            _ => Err(BridgeError::protocol()),
        }
    }

    async fn terminate(&mut self) {
        let _ = self.child.start_kill();
        let _ = timeout(STOP_GRACE, self.child.wait()).await;
    }

    async fn stop(self) {
        let Self {
            mut child, input, ..
        } = self;
        drop(input);
        if timeout(STOP_GRACE, child.wait()).await.is_err() {
            let _ = child.start_kill();
            let _ = timeout(STOP_GRACE, child.wait()).await;
        }
    }
}

#[cfg(debug_assertions)]
fn bridge_command() -> Outcome<Command> {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..");
    let path = root.join(if cfg!(windows) {
        ".venv/Scripts/python.exe"
    } else {
        ".venv/bin/python"
    });
    let mut command = Command::new(path);
    command.args(["-I", "-m", "cortexshift.desktop.bridge"]);
    // Windows GUI programs have no parent console. Keep the stdio pipes, but hide the child.
    #[cfg(windows)]
    command.creation_flags(0x08000000);
    // Use a trusted working directory and ignore PYTHON* environment overrides with -I.
    command.current_dir(root);
    Ok(command)
}

#[cfg(not(debug_assertions))]
fn bridge_command() -> Outcome<Command> {
    let executable = std::env::current_exe().map_err(|_| BridgeError::exited())?;
    let directory = executable.parent().ok_or_else(BridgeError::exited)?;
    let mut command = Command::new(directory.join(format!(
        "cortexshift-bridge{}",
        std::env::consts::EXE_SUFFIX
    )));
    #[cfg(windows)]
    command.creation_flags(0x08000000);
    command.current_dir(directory);
    // Prevent inherited PyInstaller overrides, including a forged frozen runtime path.
    command.env("PYINSTALLER_RESET_ENVIRONMENT", "1");
    Ok(command)
}

#[cfg(all(test, debug_assertions))]
mod tests {
    use super::*;

    #[tokio::test(flavor = "current_thread")]
    async fn correlates_multiple_requests_and_shuts_down() {
        let bridge = Bridge::default();
        let (first, second) = tokio::join!(
            bridge.request("hello", json!({})),
            bridge.request("hello", json!({})),
        );
        assert_eq!(first.unwrap()["protocol"], 1);
        assert_eq!(second.unwrap()["protocol"], 1);
        bridge.shutdown().await;
        assert_eq!(
            bridge.request("hello", json!({})).await.unwrap_err().code,
            "shutdown"
        );
    }

    #[tokio::test(flavor = "current_thread")]
    async fn a_timed_out_worker_is_discarded_without_replaying_the_request() {
        let bridge = Bridge::default();
        let python = bridge_command().unwrap();
        let mut command = Command::new(python.as_std().get_program());
        command.args(["-I", "-c", "import time; time.sleep(60)"]);
        *bridge.worker.lock().await = Some(Worker::spawn(command).unwrap());

        assert_eq!(
            bridge.request("hello", json!({})).await.unwrap_err().code,
            "bridge_timeout"
        );
        assert!(bridge.worker.lock().await.is_none());
        assert_eq!(
            bridge.request("hello", json!({})).await.unwrap()["protocol"],
            1
        );
        bridge.shutdown().await;
    }

    #[tokio::test(flavor = "current_thread")]
    async fn detects_a_dead_worker_and_restarts_on_next_request() {
        let bridge = Bridge::default();
        bridge.request("hello", json!({})).await.unwrap();
        {
            let mut worker = bridge.worker.lock().await;
            worker.as_mut().unwrap().terminate().await;
        }
        assert_eq!(
            bridge.request("hello", json!({})).await.unwrap_err().code,
            "bridge_exited"
        );
        assert_eq!(
            bridge.request("hello", json!({})).await.unwrap()["protocol"],
            1
        );
        bridge.shutdown().await;
    }
}

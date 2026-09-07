use std::{
    env,
    ffi::OsString,
    io::{BufRead, BufReader, Write},
    process::{Child, ChildStdin, ChildStdout, Command, Stdio},
    sync::{
        atomic::{AtomicU64, Ordering},
        Mutex,
    },
};

use serde_json::{json, Value};
use tauri::State;

const DESKTOP_IPC_CONTRACT_VERSION: &str = "ecatvasp-desktop-ipc-v1";
const FRONTEND_HANDOFF_CONTRACT_VERSION: &str = "ecatvasp-frontend-handoff-v1";
const BACKEND_EXECUTABLE_ENV: &str = "ECATVASP_DESKTOP_BACKEND";
const DEFAULT_BACKEND_EXECUTABLE: &str = "ecatvasp-desktop-backend";
const PROJECT_OPERATIONS: [&str; 3] = ["open_project", "status", "frontend_handoff"];
const HEALTH_OPERATIONS: [&str; 4] = ["health", "open_project", "status", "frontend_handoff"];

#[derive(Default)]
struct BackendState {
    process: Mutex<Option<BackendProcess>>,
    health_sequence: AtomicU64,
}

struct BackendProcess {
    child: Child,
    stdin: Option<ChildStdin>,
    stdout: BufReader<ChildStdout>,
    ready: bool,
}

impl BackendProcess {
    fn spawn() -> Result<Self, String> {
        let executable = env::var_os(BACKEND_EXECUTABLE_ENV)
            .unwrap_or_else(|| OsString::from(DEFAULT_BACKEND_EXECUTABLE));
        let mut child = Command::new(executable)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .spawn()
            .map_err(|error| format!("failed to start desktop backend: {error}"))?;
        let stdin = child
            .stdin
            .take()
            .ok_or_else(|| "desktop backend stdin is unavailable".to_string())?;
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| "desktop backend stdout is unavailable".to_string())?;
        Ok(Self {
            child,
            stdin: Some(stdin),
            stdout: BufReader::new(stdout),
            ready: false,
        })
    }

    fn exchange(&mut self, request: &Value) -> Result<Value, String> {
        let encoded = serde_json::to_string(request)
            .map_err(|error| format!("failed to encode desktop request: {error}"))?;
        let stdin = self
            .stdin
            .as_mut()
            .ok_or_else(|| "desktop backend stdin is closed".to_string())?;
        stdin
            .write_all(encoded.as_bytes())
            .and_then(|()| stdin.write_all(b"\n"))
            .and_then(|()| stdin.flush())
            .map_err(|error| format!("failed to write desktop request: {error}"))?;

        let mut line = String::new();
        let read = self
            .stdout
            .read_line(&mut line)
            .map_err(|error| format!("failed to read desktop response: {error}"))?;
        if read == 0 {
            return Err("desktop backend closed stdout".to_string());
        }
        serde_json::from_str(line.trim_end())
            .map_err(|error| format!("desktop backend returned invalid JSON: {error}"))
    }

    fn shutdown(mut self) -> Result<(), String> {
        self.stdin.take();
        let status = self
            .child
            .wait()
            .map_err(|error| format!("failed to wait for desktop backend: {error}"))?;
        if status.success() {
            Ok(())
        } else {
            Err(format!("desktop backend exited with status: {status}"))
        }
    }
}

impl Drop for BackendProcess {
    fn drop(&mut self) {
        self.stdin.take();
        if matches!(self.child.try_wait(), Ok(None)) {
            let _ = self.child.kill();
            let _ = self.child.wait();
        }
    }
}

#[tauri::command]
fn backend_health(state: State<'_, BackendState>) -> Result<String, String> {
    let mut guard = state
        .process
        .lock()
        .map_err(|_| "desktop backend process lock is poisoned".to_string())?;
    if guard.is_none() {
        *guard = Some(BackendProcess::spawn()?);
    }
    let process = guard
        .as_mut()
        .ok_or_else(|| "desktop backend process is unavailable".to_string())?;
    let sequence = state.health_sequence.fetch_add(1, Ordering::Relaxed) + 1;
    let request = json!({
        "protocol_version": DESKTOP_IPC_CONTRACT_VERSION,
        "request_id": format!("tauri-health-{sequence}"),
        "operation": "health"
    });
    let response = process.exchange(&request)?;
    validate_correlated_response(&request, &response)?;
    validate_health_response(&response)?;
    process.ready = true;
    serde_json::to_string(&response)
        .map_err(|error| format!("failed to encode desktop health response: {error}"))
}

#[tauri::command]
fn backend_exchange(
    request_json: String,
    state: State<'_, BackendState>,
) -> Result<String, String> {
    let request: Value = serde_json::from_str(&request_json)
        .map_err(|error| format!("desktop frontend request is invalid JSON: {error}"))?;
    validate_frontend_request(&request)?;

    let mut guard = state
        .process
        .lock()
        .map_err(|_| "desktop backend process lock is poisoned".to_string())?;
    let process = guard
        .as_mut()
        .ok_or_else(|| "desktop backend health handshake is required".to_string())?;
    if !process.ready {
        return Err("desktop backend health handshake is required".to_string());
    }

    let response = process.exchange(&request)?;
    validate_correlated_response(&request, &response)?;
    serde_json::to_string(&response)
        .map_err(|error| format!("failed to encode desktop backend response: {error}"))
}

#[tauri::command]
fn backend_shutdown(state: State<'_, BackendState>) -> Result<(), String> {
    let process = state
        .process
        .lock()
        .map_err(|_| "desktop backend process lock is poisoned".to_string())?
        .take();
    if let Some(process) = process {
        process.shutdown()?;
    }
    Ok(())
}

fn validate_frontend_request(request: &Value) -> Result<(), String> {
    let object = request
        .as_object()
        .ok_or_else(|| "desktop frontend request must be an object".to_string())?;
    for key in object.keys() {
        if !matches!(
            key.as_str(),
            "protocol_version" | "request_id" | "operation" | "project_root"
        ) {
            return Err(format!("desktop frontend request contains unknown field: {key}"));
        }
    }
    if object.get("protocol_version").and_then(Value::as_str)
        != Some(DESKTOP_IPC_CONTRACT_VERSION)
    {
        return Err("unsupported desktop IPC contract version".to_string());
    }
    let request_id = object
        .get("request_id")
        .and_then(Value::as_str)
        .ok_or_else(|| "desktop frontend request_id must be a string".to_string())?;
    if request_id.trim().is_empty() {
        return Err("desktop frontend request_id must not be blank".to_string());
    }
    let operation = object
        .get("operation")
        .and_then(Value::as_str)
        .ok_or_else(|| "desktop frontend operation must be a string".to_string())?;
    if !PROJECT_OPERATIONS.contains(&operation) {
        return Err("desktop frontend operation is not available in Block 3".to_string());
    }
    let project_root = object
        .get("project_root")
        .and_then(Value::as_str)
        .ok_or_else(|| "desktop frontend project_root must be a string".to_string())?;
    if project_root.trim().is_empty() {
        return Err("desktop frontend project_root must not be blank".to_string());
    }
    Ok(())
}

fn validate_correlated_response(request: &Value, response: &Value) -> Result<(), String> {
    let request_object = request
        .as_object()
        .ok_or_else(|| "desktop request must be an object".to_string())?;
    let response_object = response
        .as_object()
        .ok_or_else(|| "desktop backend response must be an object".to_string())?;
    if response_object
        .get("protocol_version")
        .and_then(Value::as_str)
        != Some(DESKTOP_IPC_CONTRACT_VERSION)
    {
        return Err("desktop backend contract version mismatch".to_string());
    }
    if response_object.get("request_id") != request_object.get("request_id") {
        return Err("desktop backend response request_id mismatch".to_string());
    }
    if response_object.get("operation") != request_object.get("operation") {
        return Err("desktop backend response operation mismatch".to_string());
    }
    if response_object.get("ok").and_then(Value::as_bool).is_none() {
        return Err("desktop backend response ok flag is invalid".to_string());
    }
    Ok(())
}

fn validate_health_response(response: &Value) -> Result<(), String> {
    let object = response
        .as_object()
        .ok_or_else(|| "desktop health response must be an object".to_string())?;
    if object.get("ok").and_then(Value::as_bool) != Some(true) {
        return Err("desktop backend health check failed".to_string());
    }
    let payload = object
        .get("payload")
        .and_then(Value::as_object)
        .ok_or_else(|| "desktop backend health payload is invalid".to_string())?;
    let backend_version = payload
        .get("backend_version")
        .and_then(Value::as_str)
        .ok_or_else(|| "desktop backend version is missing".to_string())?;
    if !backend_version.starts_with("1.") {
        return Err("unsupported backend package major version".to_string());
    }
    if payload
        .get("frontend_handoff_contract_version")
        .and_then(Value::as_str)
        != Some(FRONTEND_HANDOFF_CONTRACT_VERSION)
    {
        return Err("unsupported frontend handoff contract version".to_string());
    }
    if payload
        .get("stateless_project_requests")
        .and_then(Value::as_bool)
        != Some(true)
    {
        return Err("desktop backend must advertise stateless project requests".to_string());
    }
    let operations = payload
        .get("operations")
        .and_then(Value::as_array)
        .ok_or_else(|| "desktop backend operations are invalid".to_string())?;
    for expected in HEALTH_OPERATIONS {
        if !operations.iter().any(|value| value.as_str() == Some(expected)) {
            return Err(format!("desktop backend is missing operation: {expected}"));
        }
    }
    Ok(())
}

pub fn run() {
    tauri::Builder::default()
        .manage(BackendState::default())
        .invoke_handler(tauri::generate_handler![
            backend_health,
            backend_exchange,
            backend_shutdown
        ])
        .run(tauri::generate_context!())
        .expect("error while running ECatVASP desktop application");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn frontend_request_rejects_health_and_future_operations() {
        let health = json!({
            "protocol_version": DESKTOP_IPC_CONTRACT_VERSION,
            "request_id": "request-1",
            "operation": "health"
        });
        let future = json!({
            "protocol_version": DESKTOP_IPC_CONTRACT_VERSION,
            "request_id": "request-2",
            "operation": "future_mutation",
            "project_root": "/project"
        });

        assert!(validate_frontend_request(&health).is_err());
        assert!(validate_frontend_request(&future).is_err());
    }

    #[test]
    fn health_response_rejects_unknown_contract_major() {
        let response = json!({
            "protocol_version": "ecatvasp-desktop-ipc-v2",
            "request_id": "tauri-health-1",
            "operation": "health",
            "ok": true,
            "payload": {
                "backend_version": "1.0.0.dev0",
                "frontend_handoff_contract_version": FRONTEND_HANDOFF_CONTRACT_VERSION,
                "operations": HEALTH_OPERATIONS,
                "stateless_project_requests": true
            }
        });
        let request = json!({
            "protocol_version": DESKTOP_IPC_CONTRACT_VERSION,
            "request_id": "tauri-health-1",
            "operation": "health"
        });

        assert!(validate_correlated_response(&request, &response).is_err());
    }

    #[test]
    fn response_correlation_rejects_request_id_mismatch() {
        let request = json!({
            "protocol_version": DESKTOP_IPC_CONTRACT_VERSION,
            "request_id": "request-a",
            "operation": "status",
            "project_root": "/project"
        });
        let response = json!({
            "protocol_version": DESKTOP_IPC_CONTRACT_VERSION,
            "request_id": "request-b",
            "operation": "status",
            "ok": true,
            "payload": {}
        });

        assert!(validate_correlated_response(&request, &response).is_err());
    }
}

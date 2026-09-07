mod preferences;

use std::{
    env,
    ffi::OsString,
    io::{BufRead, BufReader, Write},
    path::Path,
    process::{Child, ChildStdin, ChildStdout, Command, Stdio},
    sync::{
        atomic::{AtomicU64, Ordering},
        Mutex,
    },
};

use preferences::{desktop_preferences_load, desktop_preferences_save};
use serde_json::{json, Map, Value};
use tauri::State;

const DESKTOP_IPC_CONTRACT_VERSION: &str = "ecatvasp-desktop-ipc-v1";
const FRONTEND_HANDOFF_CONTRACT_VERSION: &str = "ecatvasp-frontend-handoff-v1";
const BACKEND_EXECUTABLE_ENV: &str = "ECATVASP_DESKTOP_BACKEND";
const DEFAULT_BACKEND_EXECUTABLE: &str = "ecatvasp-desktop-backend";
#[cfg(target_os = "windows")]
const BUNDLED_BACKEND_FILENAME: &str = "ecatvasp-desktop-backend.exe";
#[cfg(not(target_os = "windows"))]
const BUNDLED_BACKEND_FILENAME: &str = "ecatvasp-desktop-backend";
const PROJECT_OPERATIONS: [&str; 5] = [
    "open_project",
    "status",
    "frontend_handoff",
    "application_report",
    "prepare_workflow",
];
const HEALTH_OPERATIONS: [&str; 6] = [
    "health",
    "open_project",
    "status",
    "frontend_handoff",
    "application_report",
    "prepare_workflow",
];
const BASE_REQUEST_FIELDS: [&str; 4] = [
    "protocol_version",
    "request_id",
    "operation",
    "project_root",
];

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
        let executable = backend_executable();
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

fn backend_executable() -> OsString {
    resolve_backend_executable(
        env::var_os(BACKEND_EXECUTABLE_ENV),
        env::current_exe().ok().as_deref(),
    )
}

fn resolve_backend_executable(
    explicit_override: Option<OsString>,
    current_executable: Option<&Path>,
) -> OsString {
    if let Some(executable) = explicit_override {
        return executable;
    }
    if let Some(parent) = current_executable.and_then(Path::parent) {
        let bundled = parent.join(BUNDLED_BACKEND_FILENAME);
        if bundled.is_file() {
            return bundled.into_os_string();
        }
    }
    OsString::from(DEFAULT_BACKEND_EXECUTABLE)
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
    if object.get("protocol_version").and_then(Value::as_str)
        != Some(DESKTOP_IPC_CONTRACT_VERSION)
    {
        return Err("unsupported desktop IPC contract version".to_string());
    }
    let request_id = require_nonblank_string(object, "request_id")?;
    if request_id.trim().is_empty() {
        return Err("desktop frontend request_id must not be blank".to_string());
    }
    let operation = require_nonblank_string(object, "operation")?;
    if !PROJECT_OPERATIONS.contains(&operation) {
        return Err("desktop frontend operation is not available in Block 6".to_string());
    }
    require_nonblank_string(object, "project_root")?;

    match operation {
        "open_project" | "status" | "frontend_handoff" => {
            validate_allowed_fields(object, &BASE_REQUEST_FIELDS)?;
        }
        "application_report" => {
            let allowed = [
                "protocol_version",
                "request_id",
                "operation",
                "project_root",
                "report_format",
            ];
            validate_allowed_fields(object, &allowed)?;
            let format = require_nonblank_string(object, "report_format")?;
            if !matches!(format, "json" | "csv" | "markdown") {
                return Err("desktop report format is unsupported".to_string());
            }
        }
        "prepare_workflow" => {
            let allowed = [
                "protocol_version",
                "request_id",
                "operation",
                "project_root",
                "workflow_recipe_id",
                "workflow_recipe_version",
                "root_structure_snapshot_id",
                "parameters_hash",
            ];
            validate_allowed_fields(object, &allowed)?;
            require_nonblank_string(object, "workflow_recipe_id")?;
            require_nonblank_string(object, "workflow_recipe_version")?;
            require_nonblank_string(object, "root_structure_snapshot_id")?;
            if let Some(parameters_hash) = object.get("parameters_hash") {
                let value = parameters_hash
                    .as_str()
                    .ok_or_else(|| "desktop parameters_hash must be a string".to_string())?;
                if !is_sha256(value) {
                    return Err("desktop parameters_hash must be a SHA-256 digest".to_string());
                }
            }
        }
        _ => return Err("desktop frontend operation is not available in Block 6".to_string()),
    }
    Ok(())
}

fn validate_allowed_fields(
    object: &Map<String, Value>,
    allowed: &[&str],
) -> Result<(), String> {
    for key in object.keys() {
        if !allowed.contains(&key.as_str()) {
            return Err(format!("desktop frontend request contains unknown field: {key}"));
        }
    }
    Ok(())
}

fn require_nonblank_string<'a>(
    object: &'a Map<String, Value>,
    field_name: &str,
) -> Result<&'a str, String> {
    let value = object
        .get(field_name)
        .and_then(Value::as_str)
        .ok_or_else(|| format!("desktop frontend {field_name} must be a string"))?;
    if value.trim().is_empty() {
        return Err(format!("desktop frontend {field_name} must not be blank"));
    }
    Ok(value)
}

fn is_sha256(value: &str) -> bool {
    value.len() == 64 && value.bytes().all(|byte| byte.is_ascii_hexdigit())
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
    let recipes = payload
        .get("workflow_recipes")
        .and_then(Value::as_array)
        .ok_or_else(|| "desktop backend workflow recipes are invalid".to_string())?;
    if recipes.is_empty() {
        return Err("desktop backend workflow recipes are empty".to_string());
    }
    for recipe in recipes {
        let recipe = recipe
            .as_object()
            .ok_or_else(|| "desktop backend workflow recipe is invalid".to_string())?;
        require_nonblank_string(recipe, "recipe_id")?;
        require_nonblank_string(recipe, "version")?;
        match recipe.get("description") {
            Some(Value::String(_)) | Some(Value::Null) => {}
            _ => return Err("desktop backend workflow recipe description is invalid".to_string()),
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
            backend_shutdown,
            desktop_preferences_load,
            desktop_preferences_save
        ])
        .run(tauri::generate_context!())
        .expect("error while running ECatVASP desktop application");
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{fs, path::PathBuf};

    fn temporary_runtime_dir(name: &str) -> PathBuf {
        env::temp_dir().join(format!("ecatvasp-{name}-{}", std::process::id()))
    }

    #[test]
    fn bundled_backend_is_preferred_but_explicit_override_wins() {
        let runtime_dir = temporary_runtime_dir("bundled-backend");
        fs::create_dir_all(&runtime_dir).expect("create runtime test directory");
        let app = runtime_dir.join("ECatVASP-test-app");
        let bundled = runtime_dir.join(BUNDLED_BACKEND_FILENAME);
        fs::write(&bundled, b"test").expect("create bundled backend marker");

        let resolved = resolve_backend_executable(None, Some(&app));
        assert_eq!(resolved, bundled.as_os_str());

        let explicit = OsString::from("C:/explicit/ecatvasp-backend.exe");
        let resolved = resolve_backend_executable(Some(explicit.clone()), Some(&app));
        assert_eq!(resolved, explicit);

        fs::remove_dir_all(runtime_dir).expect("remove runtime test directory");
    }

    #[test]
    fn backend_resolution_falls_back_to_development_command() {
        let runtime_dir = temporary_runtime_dir("backend-fallback");
        fs::create_dir_all(&runtime_dir).expect("create runtime test directory");
        let app = runtime_dir.join("ECatVASP-test-app");

        let resolved = resolve_backend_executable(None, Some(&app));
        assert_eq!(resolved, OsString::from(DEFAULT_BACKEND_EXECUTABLE));

        fs::remove_dir_all(runtime_dir).expect("remove runtime test directory");
    }

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
    fn frontend_request_enforces_typed_action_fields() {
        let report = json!({
            "protocol_version": DESKTOP_IPC_CONTRACT_VERSION,
            "request_id": "report-1",
            "operation": "application_report",
            "project_root": "/project",
            "report_format": "json"
        });
        let workflow = json!({
            "protocol_version": DESKTOP_IPC_CONTRACT_VERSION,
            "request_id": "workflow-1",
            "operation": "prepare_workflow",
            "project_root": "/project",
            "workflow_recipe_id": "ECatVASP.Workflow.SlabScientificPreparation",
            "workflow_recipe_version": "1",
            "root_structure_snapshot_id": "018f0e9e-7c3f-7a11-8b22-123456789abc",
            "parameters_hash": "a".repeat(64)
        });
        assert!(validate_frontend_request(&report).is_ok());
        assert!(validate_frontend_request(&workflow).is_ok());

        let report_with_workflow_field = json!({
            "protocol_version": DESKTOP_IPC_CONTRACT_VERSION,
            "request_id": "report-2",
            "operation": "application_report",
            "project_root": "/project",
            "report_format": "json",
            "workflow_recipe_id": "forbidden"
        });
        let generic_payload = json!({
            "protocol_version": DESKTOP_IPC_CONTRACT_VERSION,
            "request_id": "workflow-2",
            "operation": "prepare_workflow",
            "project_root": "/project",
            "workflow_recipe_id": "recipe",
            "workflow_recipe_version": "1",
            "root_structure_snapshot_id": "snapshot",
            "payload": {"arbitrary": true}
        });
        assert!(validate_frontend_request(&report_with_workflow_field).is_err());
        assert!(validate_frontend_request(&generic_payload).is_err());
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
                "stateless_project_requests": true,
                "workflow_recipes": [{
                    "recipe_id": "recipe",
                    "version": "1",
                    "description": null
                }]
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
    fn health_response_requires_typed_action_catalog() {
        let response = json!({
            "protocol_version": DESKTOP_IPC_CONTRACT_VERSION,
            "request_id": "tauri-health-1",
            "operation": "health",
            "ok": true,
            "payload": {
                "backend_version": "1.0.0.dev0",
                "frontend_handoff_contract_version": FRONTEND_HANDOFF_CONTRACT_VERSION,
                "operations": HEALTH_OPERATIONS,
                "stateless_project_requests": true,
                "workflow_recipes": [{
                    "recipe_id": "recipe",
                    "version": "1",
                    "description": null
                }]
            }
        });
        assert!(validate_health_response(&response).is_ok());

        let missing = json!({
            "protocol_version": DESKTOP_IPC_CONTRACT_VERSION,
            "request_id": "tauri-health-2",
            "operation": "health",
            "ok": true,
            "payload": {
                "backend_version": "1.0.0.dev0",
                "frontend_handoff_contract_version": FRONTEND_HANDOFF_CONTRACT_VERSION,
                "operations": ["health", "open_project", "status", "frontend_handoff"],
                "stateless_project_requests": true,
                "workflow_recipes": []
            }
        });
        assert!(validate_health_response(&missing).is_err());
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

mod exports;
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

const DESKTOP_IPC_V1_CONTRACT_VERSION: &str = "ecatvasp-desktop-ipc-v1";
const DESKTOP_IPC_V2_CONTRACT_VERSION: &str = "ecatvasp-desktop-ipc-v2";
const FRONTEND_HANDOFF_CONTRACT_VERSION: &str = "ecatvasp-frontend-handoff-v1";
const BACKEND_EXECUTABLE_ENV: &str = "ECATVASP_DESKTOP_BACKEND";
const DEFAULT_BACKEND_EXECUTABLE: &str = "ecatvasp-desktop-backend";
#[cfg(target_os = "windows")]
const BUNDLED_BACKEND_FILENAME: &str = "ecatvasp-desktop-backend.exe";
#[cfg(not(target_os = "windows"))]
const BUNDLED_BACKEND_FILENAME: &str = "ecatvasp-desktop-backend";

const V2_FRONTEND_OPERATIONS: [&str; 26] = [
    "open_project",
    "status",
    "frontend_handoff",
    "application_report",
    "prepare_workflow",
    "project_dashboard",
    "model_catalog",
    "structure_presentation",
    "create_project",
    "create_catalyst",
    "build_graphene_model",
    "import_structure_model",
    "mutate_structure_model",
    "build_single_metal_site",
    "build_multi_metal_site",
    "create_active_site",
    "build_adsorbate_conformer",
    "calculation_catalog",
    "prepare_calculation_workflow",
    "materialize_calculation_step",
    "job_catalog",
    "prepare_execution",
    "submit_slurm_job",
    "refresh_slurm_job",
    "cancel_slurm_job",
    "retrieve_job_outputs",
];
const V2_HEALTH_OPERATIONS: [&str; 27] = [
    "health",
    "open_project",
    "status",
    "frontend_handoff",
    "application_report",
    "prepare_workflow",
    "project_dashboard",
    "model_catalog",
    "structure_presentation",
    "create_project",
    "create_catalyst",
    "build_graphene_model",
    "import_structure_model",
    "mutate_structure_model",
    "build_single_metal_site",
    "build_multi_metal_site",
    "create_active_site",
    "build_adsorbate_conformer",
    "calculation_catalog",
    "prepare_calculation_workflow",
    "materialize_calculation_step",
    "job_catalog",
    "prepare_execution",
    "submit_slurm_job",
    "refresh_slurm_job",
    "cancel_slurm_job",
    "retrieve_job_outputs",
];
const V2_REQUEST_FIELDS: [&str; 40] = [
    "protocol_version",
    "request_id",
    "operation",
    "project_root",
    "report_format",
    "workflow_recipe_id",
    "workflow_recipe_version",
    "root_structure_snapshot_id",
    "parameters_hash",
    "structure_snapshot_id",
    "name",
    "slug",
    "description",
    "formula_label",
    "support_type",
    "series_key",
    "series_value",
    "tags",
    "catalyst_id",
    "variant_name",
    "nx",
    "ny",
    "bond_length_angstrom",
    "vacuum_gap_angstrom",
    "label",
    "source_path",
    "format",
    "source_variant_id",
    "source_snapshot_id",
    "vacancy_atom_uids",
    "substitutions",
    "metal_element",
    "coordination_atom_uids",
    "side",
    "height_angstrom",
    "centers",
    "metal_metal_topology_intent",
    "structure_variant_id",
    "center_atom_uids",
    "side_labels",
];
const V2_ADSORBATE_FIELDS: [&str; 13] = [
    "active_site_id",
    "state_label",
    "template_key",
    "target_center_atom_uids",
    "binding_mode",
    "contacts",
    "conformer_name",
    "coverage",
    "reaction_role",
    "orientation",
    "rank",
    "topology",
    "coordination_environment",
];
const V2_CALCULATION_FIELDS: [&str; 8] = [
    "task",
    "method",
    "protocol",
    "recipe",
    "workflow_plan_id",
    "step_key",
    "method_fingerprint_id",
    "numerical_evidence",
];
const V2_JOB_FIELDS: [&str; 10] = [
    "calculation_id",
    "potcar_root",
    "execution_settings",
    "frequency_atom_uids",
    "target",
    "remote_potcar",
    "remote_job_id",
    "requested_roles",
    "release_remote_roles",
    "discard_remote_roles",
];
const CALCULATION_METHOD_FIELDS: [&str; 7] = [
    "xc_functional",
    "potcar_family",
    "potcar_root",
    "potcar_symbols",
    "spin_treatment",
    "engine_version",
    "dispersion_model",
];
const CALCULATION_PROTOCOL_FIELDS: [&str; 13] = [
    "encut_ev",
    "kpoint_kind",
    "kpoint_mesh",
    "kpoint_value",
    "kpoint_centering",
    "vacuum_axis",
    "precision",
    "ediff_ev",
    "ediffg_ev_per_angstrom",
    "ismear",
    "sigma_ev",
    "isym",
    "unused_reserved",
];
const CALCULATION_RECIPE_FIELDS: [&str; 4] = [
    "frequency_potim_angstrom",
    "frequency_atom_uids",
    "dos_nedos",
    "lobster_nbands",
];
const ENCUT_EVIDENCE_FIELDS: [&str; 5] = [
    "core_method_hash",
    "potcar_spec_hash",
    "tested_encuts_ev",
    "selected_encut_ev",
    "analysis_hash",
];
const KPOINT_EVIDENCE_FIELDS: [&str; 5] = [
    "core_method_hash",
    "system_kind",
    "tested_plan_hashes",
    "selected_plan_hash",
    "analysis_hash",
];
const EXECUTION_SETTINGS_FIELDS: [&str; 10] = [
    "ncore",
    "kpar",
    "nodes",
    "cores",
    "memory_mb",
    "walltime_seconds",
    "partition",
    "mpi_ranks",
    "omp_threads",
    "executable",
];
const EXECUTION_TARGET_FIELDS: [&str; 7] = [
    "target_id",
    "host_alias",
    "remote_work_root",
    "potcar_resolver_id",
    "vasp_executable",
    "launcher",
    "module_loads",
];
const REMOTE_POTCAR_FIELDS: [&str; 3] = ["resolver_id", "family", "root"];

#[derive(Default)]
struct BackendState {
    process: Mutex<Option<BackendProcess>>,
    health_sequence: AtomicU64,
    restart_count: AtomicU64,
    last_failure_kind: Mutex<Option<&'static str>>,
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

fn record_backend_failure(state: &BackendState, kind: &'static str) {
    if let Ok(mut last_failure) = state.last_failure_kind.lock() {
        *last_failure = Some(kind);
    }
    eprintln!("ecatvasp-desktop: backend failure category: {kind}");
}

fn perform_backend_health(state: &BackendState) -> Result<String, String> {
    let mut guard = state
        .process
        .lock()
        .map_err(|_| "desktop backend runtime state is unavailable".to_string())?;
    if guard.is_none() {
        match BackendProcess::spawn() {
            Ok(process) => *guard = Some(process),
            Err(_) => {
                drop(guard);
                record_backend_failure(state, "spawn");
                return Err("desktop backend could not start".to_string());
            }
        }
    }

    let sequence = state.health_sequence.fetch_add(1, Ordering::Relaxed) + 1;
    let request = json!({
        "protocol_version": DESKTOP_IPC_V2_CONTRACT_VERSION,
        "request_id": format!("tauri-v2-health-{sequence}"),
        "operation": "health"
    });
    let exchange_result = guard
        .as_mut()
        .ok_or_else(|| "desktop backend process is unavailable".to_string())?
        .exchange(&request);
    let response = match exchange_result {
        Ok(response) => response,
        Err(_) => {
            guard.take();
            drop(guard);
            record_backend_failure(state, "transport");
            return Err("desktop backend transport failed; restart required".to_string());
        }
    };
    if validate_correlated_response(&request, &response).is_err()
        || validate_health_response(&response).is_err()
    {
        guard.take();
        drop(guard);
        record_backend_failure(state, "compatibility");
        return Err("desktop backend compatibility check failed".to_string());
    }
    if let Some(process) = guard.as_mut() {
        process.ready = true;
    }
    serde_json::to_string(&response)
        .map_err(|_| "desktop backend health response could not be encoded".to_string())
}

#[tauri::command]
fn backend_health(state: State<'_, BackendState>) -> Result<String, String> {
    perform_backend_health(&state)
}

#[tauri::command]
fn backend_exchange(
    request_json: String,
    state: State<'_, BackendState>,
) -> Result<String, String> {
    let request: Value = serde_json::from_str(&request_json)
        .map_err(|_| "desktop frontend request is invalid JSON".to_string())?;
    validate_frontend_request(&request)?;

    let mut guard = state
        .process
        .lock()
        .map_err(|_| "desktop backend runtime state is unavailable".to_string())?;
    let process = guard
        .as_mut()
        .ok_or_else(|| "desktop backend health handshake is required".to_string())?;
    if !process.ready {
        return Err("desktop backend health handshake is required".to_string());
    }

    let exchange_result = process.exchange(&request);
    let response = match exchange_result {
        Ok(response) => response,
        Err(_) => {
            guard.take();
            drop(guard);
            record_backend_failure(&state, "transport");
            return Err("desktop backend transport failed; restart required".to_string());
        }
    };
    if validate_correlated_response(&request, &response).is_err() {
        guard.take();
        drop(guard);
        record_backend_failure(&state, "compatibility");
        return Err("desktop backend response failed compatibility validation".to_string());
    }
    serde_json::to_string(&response)
        .map_err(|_| "desktop backend response could not be encoded".to_string())
}

#[tauri::command]
fn backend_restart(state: State<'_, BackendState>) -> Result<String, String> {
    let old_process = state
        .process
        .lock()
        .map_err(|_| "desktop backend runtime state is unavailable".to_string())?
        .take();
    if let Some(process) = old_process {
        let _ = process.shutdown();
    }

    let response = perform_backend_health(&state)?;
    state.restart_count.fetch_add(1, Ordering::Relaxed);
    Ok(response)
}

#[tauri::command]
fn backend_diagnostics(state: State<'_, BackendState>) -> Result<String, String> {
    let last_failure = *state
        .last_failure_kind
        .lock()
        .map_err(|_| "desktop backend diagnostics are unavailable".to_string())?;
    let mut process = state
        .process
        .lock()
        .map_err(|_| "desktop backend diagnostics are unavailable".to_string())?;
    let backend_state = runtime_backend_state(&mut process, last_failure.is_some());
    Ok(json!({
        "backend_state": backend_state,
        "restart_count": state.restart_count.load(Ordering::Relaxed),
        "last_failure_kind": last_failure,
    })
    .to_string())
}

fn runtime_backend_state(
    process: &mut Option<BackendProcess>,
    has_failure: bool,
) -> &'static str {
    let Some(process) = process.as_mut() else {
        return if has_failure { "unavailable" } else { "not_started" };
    };
    match process.child.try_wait() {
        Ok(Some(_)) => "exited",
        Ok(None) if process.ready => "ready",
        Ok(None) => "starting",
        Err(_) => "unavailable",
    }
}

#[tauri::command]
fn backend_shutdown(state: State<'_, BackendState>) -> Result<(), String> {
    let process = state
        .process
        .lock()
        .map_err(|_| "desktop backend runtime state is unavailable".to_string())?
        .take();
    if let Some(process) = process {
        if process.shutdown().is_err() {
            record_backend_failure(&state, "shutdown");
            return Err("desktop backend could not shut down cleanly".to_string());
        }
    }
    Ok(())
}

fn validate_frontend_request(request: &Value) -> Result<(), String> {
    let object = request
        .as_object()
        .ok_or_else(|| "desktop frontend request must be an object".to_string())?;
    if object.get("protocol_version").and_then(Value::as_str)
        != Some(DESKTOP_IPC_V2_CONTRACT_VERSION)
    {
        return Err("unsupported desktop IPC contract version".to_string());
    }
    require_nonblank_string(object, "request_id")?;
    let operation = require_nonblank_string(object, "operation")?;
    if !V2_FRONTEND_OPERATIONS.contains(&operation) {
        return Err("desktop frontend operation is not available".to_string());
    }

    if matches!(
        operation,
        "job_catalog"
            | "prepare_execution"
            | "submit_slurm_job"
            | "refresh_slurm_job"
            | "cancel_slurm_job"
            | "retrieve_job_outputs"
    ) && object.contains_key("numerical_evidence")
    {
        return Err("desktop Job Center requests must resolve numerical evidence from ProjectStore".to_string());
    }

    let allowed = |key: &str| {
        V2_REQUEST_FIELDS.contains(&key)
            || V2_ADSORBATE_FIELDS.contains(&key)
            || V2_CALCULATION_FIELDS.contains(&key)
            || V2_JOB_FIELDS.contains(&key)
    };
    if object.keys().any(|key| !allowed(key.as_str())) {
        return Err("desktop frontend request contains an unknown field".to_string());
    }

    require_nonblank_string(object, "project_root")?;
    match operation {
        "application_report" => {
            let format = require_nonblank_string(object, "report_format")?;
            if !matches!(format, "json" | "csv" | "markdown") {
                return Err("desktop report format is unsupported".to_string());
            }
        }
        "prepare_workflow" => {
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
        "create_project" => {
            require_nonblank_string(object, "name")?;
            require_nonblank_string(object, "slug")?;
        }
        "create_catalyst" => {
            require_nonblank_string(object, "name")?;
            require_nonblank_string(object, "slug")?;
        }
        "build_graphene_model" | "import_structure_model" => {
            require_nonblank_string(object, "catalyst_id")?;
            require_nonblank_string(object, "variant_name")?;
        }
        "mutate_structure_model" | "build_single_metal_site" | "build_multi_metal_site" => {
            require_nonblank_string(object, "source_variant_id")?;
            require_nonblank_string(object, "source_snapshot_id")?;
            require_nonblank_string(object, "variant_name")?;
        }
        "create_active_site" | "build_adsorbate_conformer" => {
            require_nonblank_string(object, "structure_variant_id")?;
            require_nonblank_string(object, "source_snapshot_id")?;
        }
        "structure_presentation" => {
            require_nonblank_string(object, "structure_snapshot_id")?;
        }
        "calculation_catalog" | "job_catalog" => {}
        "prepare_calculation_workflow" => validate_prepare_calculation_request(object)?,
        "materialize_calculation_step" => validate_materialize_calculation_request(object)?,
        "prepare_execution" => validate_prepare_execution_request(object)?,
        "submit_slurm_job" => validate_submit_slurm_request(object)?,
        "refresh_slurm_job" | "cancel_slurm_job" => validate_remote_job_request(object)?,
        "retrieve_job_outputs" => validate_retrieve_job_request(object)?,
        _ => {}
    }
    Ok(())
}

fn validate_prepare_calculation_request(object: &Map<String, Value>) -> Result<(), String> {
    validate_calculation_task(object)?;
    require_nonblank_string(object, "root_structure_snapshot_id")?;
    validate_calculation_method(require_object(object, "method")?)?;
    validate_calculation_protocol(require_object(object, "protocol")?)?;
    validate_calculation_recipe(require_object(object, "recipe")?)
}

fn validate_materialize_calculation_request(object: &Map<String, Value>) -> Result<(), String> {
    validate_calculation_task(object)?;
    require_nonblank_string(object, "workflow_plan_id")?;
    require_nonblank_string(object, "step_key")?;
    require_nonblank_string(object, "method_fingerprint_id")?;
    validate_calculation_method(require_object(object, "method")?)?;
    validate_calculation_protocol(require_object(object, "protocol")?)?;
    validate_numerical_evidence(require_object(object, "numerical_evidence")?)
}

fn validate_prepare_execution_request(object: &Map<String, Value>) -> Result<(), String> {
    require_nonblank_string(object, "calculation_id")?;
    require_nonblank_string(object, "potcar_root")?;
    validate_execution_settings(require_object(object, "execution_settings")?)?;
    validate_optional_string_array(object, "frequency_atom_uids")
}

fn validate_submit_slurm_request(object: &Map<String, Value>) -> Result<(), String> {
    validate_prepare_execution_request(object)?;
    validate_execution_target(require_object(object, "target")?)?;
    validate_remote_potcar(require_object(object, "remote_potcar")?)
}

fn validate_remote_job_request(object: &Map<String, Value>) -> Result<(), String> {
    require_nonblank_string(object, "remote_job_id")?;
    validate_execution_target(require_object(object, "target")?)
}

fn validate_retrieve_job_request(object: &Map<String, Value>) -> Result<(), String> {
    validate_remote_job_request(object)?;
    for field in [
        "requested_roles",
        "release_remote_roles",
        "discard_remote_roles",
    ] {
        validate_optional_string_array(object, field)?;
    }
    Ok(())
}

fn validate_execution_settings(object: &Map<String, Value>) -> Result<(), String> {
    reject_unknown_nested(object, &EXECUTION_SETTINGS_FIELDS, "execution settings")?;
    for field in ["nodes", "cores", "mpi_ranks", "walltime_seconds"] {
        require_positive_integer(object, field)?;
    }
    require_nonblank_string(object, "executable")?;
    for field in ["ncore", "kpar", "memory_mb", "omp_threads"] {
        if object.contains_key(field) {
            require_positive_integer(object, field)?;
        }
    }
    if object.contains_key("partition") {
        require_nonblank_string(object, "partition")?;
    }
    Ok(())
}

fn validate_execution_target(object: &Map<String, Value>) -> Result<(), String> {
    reject_unknown_nested(object, &EXECUTION_TARGET_FIELDS, "execution target")?;
    for field in [
        "target_id",
        "host_alias",
        "remote_work_root",
        "potcar_resolver_id",
        "vasp_executable",
    ] {
        require_nonblank_string(object, field)?;
    }
    if object.contains_key("launcher") {
        require_nonblank_string(object, "launcher")?;
    }
    validate_optional_string_array(object, "module_loads")
}

fn validate_remote_potcar(object: &Map<String, Value>) -> Result<(), String> {
    reject_unknown_nested(object, &REMOTE_POTCAR_FIELDS, "remote POTCAR")?;
    for field in REMOTE_POTCAR_FIELDS {
        require_nonblank_string(object, field)?;
    }
    Ok(())
}

fn validate_optional_string_array(
    object: &Map<String, Value>,
    field_name: &str,
) -> Result<(), String> {
    let Some(value) = object.get(field_name) else {
        return Ok(());
    };
    let values = value
        .as_array()
        .ok_or_else(|| format!("desktop frontend {field_name} must be an array"))?;
    if values.iter().any(|item| item.as_str().is_none()) {
        return Err(format!("desktop frontend {field_name} must contain only strings"));
    }
    Ok(())
}

fn require_positive_integer(
    object: &Map<String, Value>,
    field_name: &str,
) -> Result<u64, String> {
    let value = object
        .get(field_name)
        .and_then(Value::as_u64)
        .ok_or_else(|| format!("desktop frontend {field_name} must be a positive integer"))?;
    if value == 0 {
        return Err(format!("desktop frontend {field_name} must be a positive integer"));
    }
    Ok(value)
}

fn validate_calculation_task(object: &Map<String, Value>) -> Result<(), String> {
    let task = require_nonblank_string(object, "task")?;
    if !matches!(task, "slab" | "adsorbate" | "gas_reference") {
        return Err("desktop calculation task is unsupported".to_string());
    }
    Ok(())
}

fn validate_calculation_method(object: &Map<String, Value>) -> Result<(), String> {
    reject_unknown_nested(object, &CALCULATION_METHOD_FIELDS, "calculation method")?;
    require_nonblank_string(object, "xc_functional")?;
    require_nonblank_string(object, "potcar_family")?;
    require_nonblank_string(object, "potcar_root")?;
    let symbols = object
        .get("potcar_symbols")
        .and_then(Value::as_array)
        .ok_or_else(|| "desktop calculation POTCAR symbols must be an array".to_string())?;
    if symbols.is_empty() {
        return Err("desktop calculation POTCAR symbols must not be empty".to_string());
    }
    for symbol in symbols {
        let entry = symbol
            .as_object()
            .ok_or_else(|| "desktop calculation POTCAR symbol must be an object".to_string())?;
        reject_unknown_nested(entry, &["element", "symbol"], "POTCAR symbol")?;
        require_nonblank_string(entry, "element")?;
        require_nonblank_string(entry, "symbol")?;
    }
    Ok(())
}

fn validate_calculation_protocol(object: &Map<String, Value>) -> Result<(), String> {
    let allowed: Vec<&str> = CALCULATION_PROTOCOL_FIELDS
        .iter()
        .copied()
        .filter(|field| *field != "unused_reserved")
        .collect();
    reject_unknown_nested(object, &allowed, "calculation protocol")?;
    require_number(object, "encut_ev")?;
    require_nonblank_string(object, "kpoint_kind")?;
    if let Some(mesh) = object.get("kpoint_mesh") {
        let values = mesh
            .as_array()
            .ok_or_else(|| "desktop calculation k-point mesh must be an array".to_string())?;
        if values.len() != 3 || values.iter().any(|value| value.as_i64().is_none()) {
            return Err("desktop calculation k-point mesh must contain three integers".to_string());
        }
    }
    Ok(())
}

fn validate_calculation_recipe(object: &Map<String, Value>) -> Result<(), String> {
    reject_unknown_nested(object, &CALCULATION_RECIPE_FIELDS, "calculation recipe")?;
    if let Some(uids) = object.get("frequency_atom_uids") {
        let values = uids
            .as_array()
            .ok_or_else(|| "desktop frequency atom UIDs must be an array".to_string())?;
        if values.iter().any(|value| value.as_str().is_none()) {
            return Err("desktop frequency atom UIDs must be strings".to_string());
        }
    }
    Ok(())
}

fn validate_numerical_evidence(object: &Map<String, Value>) -> Result<(), String> {
    reject_unknown_nested(object, &["encut", "kpoints"], "numerical evidence")?;
    let encut = require_object(object, "encut")?;
    reject_unknown_nested(encut, &ENCUT_EVIDENCE_FIELDS, "ENCUT evidence")?;
    for field in ["core_method_hash", "potcar_spec_hash", "analysis_hash"] {
        require_sha256(encut, field)?;
    }
    let tested = encut
        .get("tested_encuts_ev")
        .and_then(Value::as_array)
        .ok_or_else(|| "desktop tested ENCUT values must be an array".to_string())?;
    if tested.is_empty() || tested.iter().any(|value| value.as_f64().is_none()) {
        return Err("desktop tested ENCUT values must be a non-empty numeric array".to_string());
    }
    require_number(encut, "selected_encut_ev")?;

    if let Some(kpoints_value) = object.get("kpoints") {
        let kpoints = kpoints_value
            .as_object()
            .ok_or_else(|| "desktop k-point evidence must be an object".to_string())?;
        reject_unknown_nested(kpoints, &KPOINT_EVIDENCE_FIELDS, "k-point evidence")?;
        for field in ["core_method_hash", "selected_plan_hash", "analysis_hash"] {
            require_sha256(kpoints, field)?;
        }
        require_nonblank_string(kpoints, "system_kind")?;
        let tested_hashes = kpoints
            .get("tested_plan_hashes")
            .and_then(Value::as_array)
            .ok_or_else(|| "desktop tested k-point plan hashes must be an array".to_string())?;
        if tested_hashes.is_empty()
            || tested_hashes
                .iter()
                .any(|value| value.as_str().map(is_sha256) != Some(true))
        {
            return Err("desktop tested k-point plan hashes are invalid".to_string());
        }
    }
    Ok(())
}

fn reject_unknown_nested(
    object: &Map<String, Value>,
    allowed: &[&str],
    label: &str,
) -> Result<(), String> {
    if object.keys().any(|key| !allowed.contains(&key.as_str())) {
        return Err(format!("desktop {label} contains an unknown field"));
    }
    Ok(())
}

fn require_object<'a>(
    object: &'a Map<String, Value>,
    field_name: &str,
) -> Result<&'a Map<String, Value>, String> {
    object
        .get(field_name)
        .and_then(Value::as_object)
        .ok_or_else(|| format!("desktop frontend {field_name} must be an object"))
}

fn require_number(object: &Map<String, Value>, field_name: &str) -> Result<f64, String> {
    object
        .get(field_name)
        .and_then(Value::as_f64)
        .ok_or_else(|| format!("desktop frontend {field_name} must be numeric"))
}

fn require_sha256(object: &Map<String, Value>, field_name: &str) -> Result<(), String> {
    let value = require_nonblank_string(object, field_name)?;
    if !is_sha256(value) {
        return Err(format!("desktop frontend {field_name} must be a SHA-256 digest"));
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
    if response_object.get("protocol_version") != request_object.get("protocol_version") {
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
    if object.get("protocol_version").and_then(Value::as_str)
        != Some(DESKTOP_IPC_V2_CONTRACT_VERSION)
    {
        return Err("desktop health response must use IPC v2".to_string());
    }
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
    for expected in V2_HEALTH_OPERATIONS {
        if !operations.iter().any(|value| value.as_str() == Some(expected)) {
            return Err(format!("desktop backend is missing operation: {expected}"));
        }
    }
    let versions = payload
        .get("supported_protocol_versions")
        .and_then(Value::as_array)
        .ok_or_else(|| "desktop backend protocol catalog is invalid".to_string())?;
    if versions.len() != 2
        || versions[0].as_str() != Some(DESKTOP_IPC_V1_CONTRACT_VERSION)
        || versions[1].as_str() != Some(DESKTOP_IPC_V2_CONTRACT_VERSION)
    {
        return Err("desktop backend protocol compatibility catalog is invalid".to_string());
    }
    let recipes = payload
        .get("workflow_recipes")
        .and_then(Value::as_array)
        .ok_or_else(|| "desktop backend workflow recipes are invalid".to_string())?;
    if recipes.is_empty() {
        return Err("desktop backend workflow recipes are empty".to_string());
    }
    Ok(())
}

pub fn run() {
    tauri::Builder::default()
        .manage(BackendState::default())
        .invoke_handler(tauri::generate_handler![
            backend_health,
            backend_exchange,
            backend_restart,
            backend_diagnostics,
            backend_shutdown,
            exports::desktop_export_report,
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

    fn calculation_method() -> Value {
        json!({
            "xc_functional": "PBE",
            "potcar_family": "PBE_54",
            "potcar_root": "C:/VASP/PBE_54",
            "potcar_symbols": [{"element": "C", "symbol": "C"}],
            "spin_treatment": "collinear"
        })
    }

    fn calculation_protocol() -> Value {
        json!({
            "encut_ev": 450.0,
            "kpoint_kind": "explicit_mesh",
            "kpoint_mesh": [3, 3, 1],
            "kpoint_centering": "gamma",
            "vacuum_axis": "c"
        })
    }

    fn execution_target() -> Value {
        json!({
            "target_id": "cluster-a",
            "host_alias": "cluster-a",
            "remote_work_root": "/scratch/ecatvasp",
            "potcar_resolver_id": "pbe54-remote",
            "vasp_executable": "vasp_std",
            "launcher": "srun",
            "module_loads": ["vasp/6"]
        })
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
    fn runtime_state_without_process_never_exposes_runtime_details() {
        let mut process = None;
        assert_eq!(runtime_backend_state(&mut process, false), "not_started");
        assert_eq!(runtime_backend_state(&mut process, true), "unavailable");
    }

    #[test]
    fn frontend_request_accepts_v2_known_operations_and_rejects_v1_or_future() {
        let catalog = json!({
            "protocol_version": DESKTOP_IPC_V2_CONTRACT_VERSION,
            "request_id": "request-1",
            "operation": "job_catalog",
            "project_root": "/project"
        });
        let legacy = json!({
            "protocol_version": DESKTOP_IPC_V1_CONTRACT_VERSION,
            "request_id": "request-2",
            "operation": "status",
            "project_root": "/project"
        });
        let future = json!({
            "protocol_version": DESKTOP_IPC_V2_CONTRACT_VERSION,
            "request_id": "request-3",
            "operation": "future_mutation",
            "project_root": "/project"
        });
        assert!(validate_frontend_request(&catalog).is_ok());
        assert!(validate_frontend_request(&legacy).is_err());
        assert!(validate_frontend_request(&future).is_err());
    }

    #[test]
    fn frontend_request_rejects_generic_payload_escape_hatch() {
        let request = json!({
            "protocol_version": DESKTOP_IPC_V2_CONTRACT_VERSION,
            "request_id": "model-1",
            "operation": "mutate_structure_model",
            "project_root": "/project",
            "source_variant_id": "variant",
            "source_snapshot_id": "snapshot",
            "variant_name": "child",
            "payload": {"arbitrary": true}
        });
        assert!(validate_frontend_request(&request).is_err());
    }

    #[test]
    fn calculation_request_rejects_nested_escape_hatch() {
        let request = json!({
            "protocol_version": DESKTOP_IPC_V2_CONTRACT_VERSION,
            "request_id": "calc-1",
            "operation": "prepare_calculation_workflow",
            "project_root": "/project",
            "task": "slab",
            "root_structure_snapshot_id": "018f0e9e-7c3f-7a11-8b22-123456789abc",
            "method": {
                "xc_functional": "PBE",
                "potcar_family": "PBE_54",
                "potcar_root": "C:/VASP/PBE_54",
                "potcar_symbols": [{"element": "C", "symbol": "C"}],
                "payload": {"arbitrary": true}
            },
            "protocol": calculation_protocol(),
            "recipe": {}
        });
        assert!(validate_frontend_request(&request).is_err());
    }

    #[test]
    fn calculation_prepare_request_accepts_typed_nested_scientific_fields() {
        let request = json!({
            "protocol_version": DESKTOP_IPC_V2_CONTRACT_VERSION,
            "request_id": "calc-2",
            "operation": "prepare_calculation_workflow",
            "project_root": "/project",
            "task": "slab",
            "root_structure_snapshot_id": "018f0e9e-7c3f-7a11-8b22-123456789abc",
            "method": calculation_method(),
            "protocol": calculation_protocol(),
            "recipe": {"dos_nedos": 2001, "lobster_nbands": 96}
        });
        assert!(validate_frontend_request(&request).is_ok());
    }

    #[test]
    fn job_center_request_accepts_typed_noncredential_target() {
        let request = json!({
            "protocol_version": DESKTOP_IPC_V2_CONTRACT_VERSION,
            "request_id": "job-1",
            "operation": "submit_slurm_job",
            "project_root": "/project",
            "calculation_id": "018f0e9e-7c3f-7a11-8b22-123456789abc",
            "potcar_root": "C:/VASP/PBE_54",
            "execution_settings": {
                "nodes": 1,
                "cores": 32,
                "mpi_ranks": 32,
                "walltime_seconds": 3600,
                "executable": "vasp_std"
            },
            "target": execution_target(),
            "remote_potcar": {
                "resolver_id": "pbe54-remote",
                "family": "PBE_54",
                "root": "/apps/vasp/potpaw_PBE.54"
            }
        });
        assert!(validate_frontend_request(&request).is_ok());
    }

    #[test]
    fn job_center_request_rejects_credentials_and_manual_evidence() {
        let credential_request = json!({
            "protocol_version": DESKTOP_IPC_V2_CONTRACT_VERSION,
            "request_id": "job-secret",
            "operation": "refresh_slurm_job",
            "project_root": "/project",
            "remote_job_id": "018f0e9e-7c3f-7a11-8b22-123456789abc",
            "target": {
                "target_id": "cluster-a",
                "host_alias": "cluster-a",
                "remote_work_root": "/scratch/ecatvasp",
                "potcar_resolver_id": "pbe54-remote",
                "vasp_executable": "vasp_std",
                "module_loads": [],
                "password": "secret"
            }
        });
        let evidence_request = json!({
            "protocol_version": DESKTOP_IPC_V2_CONTRACT_VERSION,
            "request_id": "job-evidence",
            "operation": "prepare_execution",
            "project_root": "/project",
            "calculation_id": "018f0e9e-7c3f-7a11-8b22-123456789abc",
            "potcar_root": "C:/VASP/PBE_54",
            "execution_settings": {
                "nodes": 1,
                "cores": 32,
                "mpi_ranks": 32,
                "walltime_seconds": 3600,
                "executable": "vasp_std"
            },
            "numerical_evidence": {"analysis_hash": "a".repeat(64)}
        });
        assert!(validate_frontend_request(&credential_request).is_err());
        assert!(validate_frontend_request(&evidence_request).is_err());
    }

    #[test]
    fn frontend_request_enforces_typed_common_action_fields() {
        let report = json!({
            "protocol_version": DESKTOP_IPC_V2_CONTRACT_VERSION,
            "request_id": "report-1",
            "operation": "application_report",
            "project_root": "/project",
            "report_format": "json"
        });
        let workflow = json!({
            "protocol_version": DESKTOP_IPC_V2_CONTRACT_VERSION,
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
    }

    #[test]
    fn health_response_requires_v2_catalog_and_v1_compatibility_advertisement() {
        let response = json!({
            "protocol_version": DESKTOP_IPC_V2_CONTRACT_VERSION,
            "request_id": "tauri-v2-health-1",
            "operation": "health",
            "ok": true,
            "payload": {
                "backend_version": "1.1.0.dev0",
                "frontend_handoff_contract_version": FRONTEND_HANDOFF_CONTRACT_VERSION,
                "operations": V2_HEALTH_OPERATIONS,
                "stateless_project_requests": true,
                "supported_protocol_versions": [
                    DESKTOP_IPC_V1_CONTRACT_VERSION,
                    DESKTOP_IPC_V2_CONTRACT_VERSION
                ],
                "workflow_recipes": [{"recipe_id": "recipe", "version": "1", "description": null}]
            }
        });
        assert!(validate_health_response(&response).is_ok());
    }

    #[test]
    fn response_correlation_uses_request_protocol_and_identity() {
        let request = json!({
            "protocol_version": DESKTOP_IPC_V2_CONTRACT_VERSION,
            "request_id": "request-a",
            "operation": "status",
            "project_root": "/project"
        });
        let response = json!({
            "protocol_version": DESKTOP_IPC_V2_CONTRACT_VERSION,
            "request_id": "request-b",
            "operation": "status",
            "ok": true,
            "payload": {}
        });
        assert!(validate_correlated_response(&request, &response).is_err());
    }
}
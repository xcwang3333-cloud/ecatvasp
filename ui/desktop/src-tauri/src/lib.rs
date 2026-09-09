include!("transport_v1_v6.rs");

const BLOCK7_FRONTEND_OPERATIONS: [&str; 7] = [
    "thermochemistry_catalog",
    "materialize_harmonic_thermochemistry",
    "materialize_gas_reference",
    "thermochemistry_view",
    "reaction_preview",
    "materialize_reaction_diagram",
    "reaction_diagram_view",
];

const BLOCK7_BASE_FIELDS: [&str; 4] = [
    "protocol_version",
    "request_id",
    "operation",
    "project_root",
];

#[tauri::command]
fn backend_exchange_block7(
    request_json: String,
    state: State<'_, BackendState>,
) -> Result<String, String> {
    let request: Value = serde_json::from_str(&request_json)
        .map_err(|_| "desktop Block 7 request is invalid JSON".to_string())?;
    validate_block7_frontend_request(&request)?;

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

fn validate_block7_frontend_request(request: &Value) -> Result<(), String> {
    let object = request
        .as_object()
        .ok_or_else(|| "desktop Block 7 request must be an object".to_string())?;
    if object.get("protocol_version").and_then(Value::as_str)
        != Some(DESKTOP_IPC_V2_CONTRACT_VERSION)
    {
        return Err("unsupported desktop IPC contract version".to_string());
    }
    require_nonblank_string(object, "request_id")?;
    require_nonblank_string(object, "project_root")?;
    let operation = require_nonblank_string(object, "operation")?;
    if !BLOCK7_FRONTEND_OPERATIONS.contains(&operation) {
        return Err("desktop Block 7 operation is not available".to_string());
    }

    match operation {
        "thermochemistry_catalog" => reject_unknown_nested(
            object,
            &BLOCK7_BASE_FIELDS,
            "thermochemistry catalog request",
        ),
        "thermochemistry_view" | "reaction_diagram_view" => {
            reject_unknown_nested(
                object,
                &[
                    "protocol_version",
                    "request_id",
                    "operation",
                    "project_root",
                    "analysis_id",
                ],
                "thermochemistry view request",
            )?;
            require_uuid_string(object, "analysis_id")?;
            Ok(())
        }
        "materialize_harmonic_thermochemistry" => validate_block7_harmonic(object),
        "materialize_gas_reference" => validate_block7_gas_reference(object),
        "reaction_preview" | "materialize_reaction_diagram" => {
            validate_block7_reaction_request(object)
        }
        _ => Err("desktop Block 7 operation is not available".to_string()),
    }
}

fn validate_block7_harmonic(object: &Map<String, Value>) -> Result<(), String> {
    reject_unknown_nested(
        object,
        &[
            "protocol_version",
            "request_id",
            "operation",
            "project_root",
            "calculation_id",
            "subject_kind",
            "temperature_k",
            "electronic_energy_kind",
            "electronic_entropy_policy",
            "frequency_cutoff_cm_inverse",
            "imaginary_mode_policy",
            "low_frequency_policy",
            "exclusions",
        ],
        "harmonic thermochemistry request",
    )?;
    require_uuid_string(object, "calculation_id")?;
    require_choice(object, "subject_kind", &["surface", "adsorbate"])?;
    require_positive_number_block7(object, "temperature_k")?;
    require_choice(
        object,
        "electronic_energy_kind",
        &[
            "energy_sigma0_ev",
            "energy_without_entropy_ev",
            "free_energy_toten_ev",
        ],
    )?;
    require_choice(object, "electronic_entropy_policy", &["neglected"])?;
    require_positive_number_block7(object, "frequency_cutoff_cm_inverse")?;
    require_choice(
        object,
        "imaginary_mode_policy",
        &["reject_any", "exclude_explicit"],
    )?;
    require_choice(
        object,
        "low_frequency_policy",
        &["reject_below_cutoff", "exclude_explicit"],
    )?;
    validate_block7_exclusions(object)
}

fn validate_block7_gas_reference(object: &Map<String, Value>) -> Result<(), String> {
    reject_unknown_nested(
        object,
        &[
            "protocol_version",
            "request_id",
            "operation",
            "project_root",
            "calculation_id",
            "species",
            "temperature_k",
            "pressure_pa",
            "standard_state",
            "electronic_energy_kind",
            "electronic_entropy_policy",
            "geometry_kind",
            "symmetry_number",
            "spin_multiplicity",
            "atomic_masses",
            "frequency_cutoff_cm_inverse",
            "imaginary_mode_policy",
            "low_frequency_policy",
            "exclusions",
        ],
        "gas-reference request",
    )?;
    require_uuid_string(object, "calculation_id")?;
    require_choice(object, "species", &["H2", "H2O", "O2", "CO", "CO2"])?;
    require_positive_number_block7(object, "temperature_k")?;
    require_positive_number_block7(object, "pressure_pa")?;
    require_choice(
        object,
        "standard_state",
        &["ideal_gas_1_bar", "ideal_gas_1_atm"],
    )?;
    require_choice(
        object,
        "electronic_energy_kind",
        &[
            "energy_sigma0_ev",
            "energy_without_entropy_ev",
            "free_energy_toten_ev",
        ],
    )?;
    require_choice(
        object,
        "electronic_entropy_policy",
        &["neglected", "spin_degeneracy"],
    )?;
    require_choice(object, "geometry_kind", &["monatomic", "linear", "nonlinear"])?;
    require_positive_integer(object, "symmetry_number")?;
    require_positive_integer(object, "spin_multiplicity")?;
    require_positive_number_block7(object, "frequency_cutoff_cm_inverse")?;
    require_choice(
        object,
        "imaginary_mode_policy",
        &["reject_any", "exclude_explicit"],
    )?;
    require_choice(
        object,
        "low_frequency_policy",
        &["reject_below_cutoff", "exclude_explicit"],
    )?;
    validate_block7_atomic_masses(object)?;
    validate_block7_exclusions(object)
}

fn validate_block7_atomic_masses(object: &Map<String, Value>) -> Result<(), String> {
    let masses = object
        .get("atomic_masses")
        .and_then(Value::as_array)
        .ok_or_else(|| "desktop gas atomic_masses must be an array".to_string())?;
    if masses.is_empty() {
        return Err("desktop gas atomic_masses must not be empty".to_string());
    }
    let mut seen = std::collections::HashSet::new();
    for item in masses {
        let entry = item
            .as_object()
            .ok_or_else(|| "desktop gas atomic mass must be an object".to_string())?;
        reject_unknown_nested(
            entry,
            &["atom_uid", "mass_amu", "isotopologue_label"],
            "gas atomic mass",
        )?;
        let atom_uid = require_uuid_string(entry, "atom_uid")?;
        if !seen.insert(atom_uid.to_string()) {
            return Err("desktop gas atom_uid values must be unique".to_string());
        }
        require_positive_number_block7(entry, "mass_amu")?;
        if entry.contains_key("isotopologue_label") {
            optional_nonblank_string(entry, "isotopologue_label")?;
        }
    }
    Ok(())
}

fn validate_block7_exclusions(object: &Map<String, Value>) -> Result<(), String> {
    let exclusions = object
        .get("exclusions")
        .and_then(Value::as_array)
        .ok_or_else(|| "desktop thermochemistry exclusions must be an array".to_string())?;
    let mut seen = std::collections::HashSet::new();
    for item in exclusions {
        let entry = item
            .as_object()
            .ok_or_else(|| "desktop mode exclusion must be an object".to_string())?;
        reject_unknown_nested(
            entry,
            &["mode_index", "reason", "note"],
            "mode exclusion",
        )?;
        let mode_index = require_positive_integer(entry, "mode_index")?;
        if !seen.insert(mode_index) {
            return Err("desktop mode exclusion indices must be unique".to_string());
        }
        require_choice(
            entry,
            "reason",
            &[
                "imaginary",
                "low_frequency",
                "constrained",
                "translational",
                "rotational",
            ],
        )?;
        if entry.contains_key("note") {
            optional_nonblank_string(entry, "note")?;
        }
    }
    Ok(())
}

fn validate_block7_reaction_request(object: &Map<String, Value>) -> Result<(), String> {
    reject_unknown_nested(
        object,
        &[
            "protocol_version",
            "request_id",
            "operation",
            "project_root",
            "preset_kind",
            "bindings",
            "baseline_conditions",
            "requested_conditions",
        ],
        "reaction request",
    )?;
    let preset = require_choice(
        object,
        "preset_kind",
        &[
            "her_volmer_heyrovsky",
            "orr_associative_4e",
            "oer_associative_4e",
            "co2rr_to_co_2e",
        ],
    )?;
    validate_block7_reaction_bindings(require_object(object, "bindings")?, preset)?;
    let baseline = require_object(object, "baseline_conditions")?;
    let requested = require_object(object, "requested_conditions")?;
    let baseline_temperature = validate_block7_reaction_conditions(baseline)?;
    let requested_temperature = validate_block7_reaction_conditions(requested)?;
    if baseline_temperature != requested_temperature {
        return Err("desktop reaction conditions must use the same temperature_k".to_string());
    }
    Ok(())
}

fn validate_block7_reaction_conditions(object: &Map<String, Value>) -> Result<f64, String> {
    reject_unknown_nested(
        object,
        &[
            "temperature_k",
            "potential_v",
            "ph",
            "potential_reference",
            "ph_semantics",
        ],
        "reaction conditions",
    )?;
    let temperature = require_positive_number_block7(object, "temperature_k")?;
    require_finite_number_block7(object, "potential_v")?;
    require_finite_number_block7(object, "ph")?;
    require_choice(object, "potential_reference", &["she", "rhe"])?;
    require_choice(
        object,
        "ph_semantics",
        &["explicit_activity", "included_in_rhe"],
    )?;
    Ok(temperature)
}

fn validate_block7_reaction_bindings(
    object: &Map<String, Value>,
    preset: &str,
) -> Result<(), String> {
    let fields: &[&str] = match preset {
        "her_volmer_heyrovsky" => &[
            "clean_surface_analysis_id",
            "h_adsorbed_analysis_id",
            "h2_reference_analysis_id",
        ],
        "orr_associative_4e" => &[
            "clean_surface_analysis_id",
            "ooh_adsorbed_analysis_id",
            "o_adsorbed_analysis_id",
            "oh_adsorbed_analysis_id",
            "o2_reference_analysis_id",
            "h2o_reference_analysis_id",
            "h2_reference_analysis_id",
        ],
        "oer_associative_4e" => &[
            "clean_surface_analysis_id",
            "oh_adsorbed_analysis_id",
            "o_adsorbed_analysis_id",
            "ooh_adsorbed_analysis_id",
            "h2o_reference_analysis_id",
            "o2_reference_analysis_id",
            "h2_reference_analysis_id",
        ],
        "co2rr_to_co_2e" => &[
            "clean_surface_analysis_id",
            "cooh_adsorbed_analysis_id",
            "co_adsorbed_analysis_id",
            "co2_reference_analysis_id",
            "h2o_reference_analysis_id",
            "co_reference_analysis_id",
            "h2_reference_analysis_id",
        ],
        _ => return Err("desktop reaction preset is unsupported".to_string()),
    };
    reject_unknown_nested(object, fields, "reaction bindings")?;
    let mut seen = std::collections::HashSet::new();
    for field in fields {
        let value = require_uuid_string(object, field)?;
        if !seen.insert(value.to_string()) {
            return Err("desktop reaction Analysis bindings must be distinct".to_string());
        }
    }
    Ok(())
}

fn require_choice<'a>(
    object: &'a Map<String, Value>,
    field_name: &str,
    allowed: &[&str],
) -> Result<&'a str, String> {
    let value = require_nonblank_string(object, field_name)?;
    if !allowed.contains(&value) {
        return Err(format!("desktop frontend {field_name} has an unsupported value"));
    }
    Ok(value)
}

fn require_finite_number_block7(
    object: &Map<String, Value>,
    field_name: &str,
) -> Result<f64, String> {
    let value = require_number(object, field_name)?;
    if !value.is_finite() {
        return Err(format!("desktop frontend {field_name} must be finite"));
    }
    Ok(value)
}

fn require_positive_number_block7(
    object: &Map<String, Value>,
    field_name: &str,
) -> Result<f64, String> {
    let value = require_finite_number_block7(object, field_name)?;
    if value <= 0.0 {
        return Err(format!("desktop frontend {field_name} must be positive"));
    }
    Ok(value)
}

fn require_uuid_string<'a>(
    object: &'a Map<String, Value>,
    field_name: &str,
) -> Result<&'a str, String> {
    let value = require_nonblank_string(object, field_name)?;
    if !looks_like_uuid(value) {
        return Err(format!("desktop frontend {field_name} must be a UUID"));
    }
    Ok(value)
}

fn looks_like_uuid(value: &str) -> bool {
    if value.len() != 36 {
        return false;
    }
    for (index, byte) in value.bytes().enumerate() {
        if matches!(index, 8 | 13 | 18 | 23) {
            if byte != b'-' {
                return false;
            }
        } else if !byte.is_ascii_hexdigit() {
            return false;
        }
    }
    true
}

pub fn run_block7() {
    tauri::Builder::default()
        .manage(BackendState::default())
        .invoke_handler(tauri::generate_handler![
            backend_health,
            backend_exchange,
            backend_exchange_block7,
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
mod block7_transport_tests {
    use super::*;

    fn base(operation: &str) -> Map<String, Value> {
        json!({
            "protocol_version": DESKTOP_IPC_V2_CONTRACT_VERSION,
            "request_id": format!("block7-{operation}"),
            "operation": operation,
            "project_root": "/project"
        })
        .as_object()
        .expect("base request must be object")
        .clone()
    }

    fn reaction_request(operation: &str) -> Value {
        json!({
            "protocol_version": DESKTOP_IPC_V2_CONTRACT_VERSION,
            "request_id": format!("block7-{operation}"),
            "operation": operation,
            "project_root": "/project",
            "preset_kind": "her_volmer_heyrovsky",
            "bindings": {
                "clean_surface_analysis_id": "018f0e9e-7c3f-7a11-8b22-123456789abc",
                "h_adsorbed_analysis_id": "018f0e9e-7c3f-7a11-8b22-123456789abd",
                "h2_reference_analysis_id": "018f0e9e-7c3f-7a11-8b22-123456789abe"
            },
            "baseline_conditions": {
                "temperature_k": 298.15,
                "potential_v": 0.0,
                "ph": 0.0,
                "potential_reference": "she",
                "ph_semantics": "explicit_activity"
            },
            "requested_conditions": {
                "temperature_k": 298.15,
                "potential_v": -0.2,
                "ph": 0.0,
                "potential_reference": "she",
                "ph_semantics": "explicit_activity"
            }
        })
    }

    #[test]
    fn block7_catalog_and_view_are_explicitly_whitelisted() {
        let catalog = Value::Object(base("thermochemistry_catalog"));
        let mut view = base("reaction_diagram_view");
        view.insert(
            "analysis_id".to_string(),
            Value::String("018f0e9e-7c3f-7a11-8b22-123456789abc".to_string()),
        );
        assert!(validate_block7_frontend_request(&catalog).is_ok());
        assert!(validate_block7_frontend_request(&Value::Object(view)).is_ok());
    }

    #[test]
    fn block7_harmonic_rejects_generic_scientific_escape_hatches() {
        let mut request = base("materialize_harmonic_thermochemistry");
        request.extend(
            json!({
                "calculation_id": "018f0e9e-7c3f-7a11-8b22-123456789abc",
                "subject_kind": "adsorbate",
                "temperature_k": 298.15,
                "electronic_energy_kind": "energy_sigma0_ev",
                "electronic_entropy_policy": "neglected",
                "frequency_cutoff_cm_inverse": 50.0,
                "imaginary_mode_policy": "reject_any",
                "low_frequency_policy": "reject_below_cutoff",
                "exclusions": []
            })
            .as_object()
            .expect("fields must be object")
            .clone(),
        );
        assert!(validate_block7_frontend_request(&Value::Object(request.clone())).is_ok());
        request.insert("gibbs_energy_ev".to_string(), json!(-1.0));
        assert!(validate_block7_frontend_request(&Value::Object(request)).is_err());
    }

    #[test]
    fn block7_gas_rejects_unknown_nested_mass_fields() {
        let request = json!({
            "protocol_version": DESKTOP_IPC_V2_CONTRACT_VERSION,
            "request_id": "block7-gas",
            "operation": "materialize_gas_reference",
            "project_root": "/project",
            "calculation_id": "018f0e9e-7c3f-7a11-8b22-123456789abc",
            "species": "H2",
            "temperature_k": 298.15,
            "pressure_pa": 100000.0,
            "standard_state": "ideal_gas_1_bar",
            "electronic_energy_kind": "energy_sigma0_ev",
            "electronic_entropy_policy": "neglected",
            "geometry_kind": "linear",
            "symmetry_number": 2,
            "spin_multiplicity": 1,
            "atomic_masses": [{
                "atom_uid": "018f0e9e-7c3f-7a11-8b22-123456789abd",
                "mass_amu": 1.00784,
                "energy_override_ev": -0.1
            }],
            "frequency_cutoff_cm_inverse": 50.0,
            "imaginary_mode_policy": "reject_any",
            "low_frequency_policy": "reject_below_cutoff",
            "exclusions": []
        });
        assert!(validate_block7_frontend_request(&request).is_err());
    }

    #[test]
    fn block7_reaction_rejects_free_energy_and_nested_che_escape_hatches() {
        let valid = reaction_request("reaction_preview");
        assert!(validate_block7_frontend_request(&valid).is_ok());

        let mut energy_escape = valid.clone();
        energy_escape
            .as_object_mut()
            .expect("request object")
            .insert("free_energy_table".to_string(), json!([0.0, -0.1]));
        assert!(validate_block7_frontend_request(&energy_escape).is_err());

        let mut che_escape = valid;
        che_escape
            .get_mut("requested_conditions")
            .and_then(Value::as_object_mut)
            .expect("conditions object")
            .insert("che_shift_ev".to_string(), json!(-0.2));
        assert!(validate_block7_frontend_request(&che_escape).is_err());
    }

    #[test]
    fn block7_reaction_requires_same_temperature_and_distinct_bindings() {
        let mut temperature_drift = reaction_request("materialize_reaction_diagram");
        temperature_drift
            .get_mut("requested_conditions")
            .and_then(Value::as_object_mut)
            .expect("conditions object")
            .insert("temperature_k".to_string(), json!(310.0));
        assert!(validate_block7_frontend_request(&temperature_drift).is_err());

        let mut duplicate = reaction_request("reaction_preview");
        duplicate
            .get_mut("bindings")
            .and_then(Value::as_object_mut)
            .expect("bindings object")
            .insert(
                "h_adsorbed_analysis_id".to_string(),
                Value::String("018f0e9e-7c3f-7a11-8b22-123456789abc".to_string()),
            );
        assert!(validate_block7_frontend_request(&duplicate).is_err());
    }
}

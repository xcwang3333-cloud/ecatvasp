use std::{collections::HashSet, fs, path::{Path, PathBuf}};

use serde_json::Value;
use tauri::{AppHandle, Manager};

const DESKTOP_PREFERENCES_CONTRACT_VERSION: &str = "ecatvasp-desktop-preferences-v1";
const PREFERENCES_FILE_NAME: &str = "desktop-preferences-v1.json";
const MAX_RECENT_PROJECTS: usize = 8;

#[tauri::command]
pub(crate) fn desktop_preferences_load(app: AppHandle) -> Result<Option<Value>, String> {
    let path = preferences_path(&app)?;
    load_preferences_from_path(&path)
}

#[tauri::command]
pub(crate) fn desktop_preferences_save(
    preferences: Value,
    app: AppHandle,
) -> Result<(), String> {
    validate_preferences(&preferences)?;
    let path = preferences_path(&app)?;
    save_preferences_to_path(&path, &preferences)
}

fn preferences_path(app: &AppHandle) -> Result<PathBuf, String> {
    app.path()
        .app_config_dir()
        .map(|path| path.join(PREFERENCES_FILE_NAME))
        .map_err(|error| format!("failed to resolve desktop app config directory: {error}"))
}

fn load_preferences_from_path(path: &Path) -> Result<Option<Value>, String> {
    if !path.exists() {
        return Ok(None);
    }
    let encoded = fs::read_to_string(path)
        .map_err(|error| format!("failed to read desktop preferences: {error}"))?;
    let value: Value = serde_json::from_str(&encoded)
        .map_err(|error| format!("desktop preferences are invalid JSON: {error}"))?;
    validate_preferences(&value)?;
    Ok(Some(value))
}

fn save_preferences_to_path(path: &Path, preferences: &Value) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| "desktop preferences path has no parent directory".to_string())?;
    fs::create_dir_all(parent)
        .map_err(|error| format!("failed to create desktop app config directory: {error}"))?;
    let encoded = serde_json::to_string_pretty(preferences)
        .map_err(|error| format!("failed to encode desktop preferences: {error}"))?;
    fs::write(path, format!("{encoded}\n"))
        .map_err(|error| format!("failed to write desktop preferences: {error}"))
}

fn validate_preferences(preferences: &Value) -> Result<(), String> {
    let object = preferences
        .as_object()
        .ok_or_else(|| "desktop preferences must be an object".to_string())?;

    for key in object.keys() {
        if !matches!(
            key.as_str(),
            "contract_version" | "current_project_root" | "recent_project_roots"
        ) {
            return Err(format!("desktop preferences contain unknown field: {key}"));
        }
    }

    if object.get("contract_version").and_then(Value::as_str)
        != Some(DESKTOP_PREFERENCES_CONTRACT_VERSION)
    {
        return Err("unsupported desktop preferences contract version".to_string());
    }

    match object.get("current_project_root") {
        Some(Value::Null) => {}
        Some(Value::String(root)) if !root.trim().is_empty() => {}
        _ => return Err("desktop current project root is invalid".to_string()),
    }

    let recent = object
        .get("recent_project_roots")
        .and_then(Value::as_array)
        .ok_or_else(|| "desktop recent project roots are invalid".to_string())?;
    if recent.len() > MAX_RECENT_PROJECTS {
        return Err("desktop recent project roots exceed the MRU limit".to_string());
    }

    let mut unique = HashSet::new();
    for value in recent {
        let root = value
            .as_str()
            .filter(|root| !root.trim().is_empty())
            .ok_or_else(|| "desktop recent project root is invalid".to_string())?;
        if !unique.insert(root) {
            return Err("desktop recent project roots contain duplicates".to_string());
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::{process, time::{SystemTime, UNIX_EPOCH}};

    fn temp_root(label: &str) -> PathBuf {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("system clock is before unix epoch")
            .as_nanos();
        std::env::temp_dir().join(format!(
            "ecatvasp-{label}-{}-{nanos}",
            process::id()
        ))
    }

    #[test]
    fn preferences_round_trip_preserves_windows_path_strings() {
        let root = temp_root("preferences-round-trip");
        let path = root.join(PREFERENCES_FILE_NAME);
        let windows_path = r"C:\Research Work\ECatVASP\project-a";
        let preferences = json!({
            "contract_version": DESKTOP_PREFERENCES_CONTRACT_VERSION,
            "current_project_root": windows_path,
            "recent_project_roots": [windows_path, "/work/project-b"]
        });

        validate_preferences(&preferences).expect("preferences should be valid");
        save_preferences_to_path(&path, &preferences).expect("preferences should save");
        let loaded = load_preferences_from_path(&path)
            .expect("preferences should load")
            .expect("preferences should exist");

        assert_eq!(loaded, preferences);
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn preferences_fail_closed_on_unknown_fields_and_duplicates() {
        let unknown = json!({
            "contract_version": DESKTOP_PREFERENCES_CONTRACT_VERSION,
            "current_project_root": null,
            "recent_project_roots": [],
            "project_bundle": {}
        });
        let duplicate = json!({
            "contract_version": DESKTOP_PREFERENCES_CONTRACT_VERSION,
            "current_project_root": "/project",
            "recent_project_roots": ["/project", "/project"]
        });

        assert!(validate_preferences(&unknown).is_err());
        assert!(validate_preferences(&duplicate).is_err());
    }

    #[test]
    fn saving_preferences_does_not_modify_project_files() {
        let root = temp_root("preferences-isolation");
        let config_path = root.join("config").join(PREFERENCES_FILE_NAME);
        let project_file = root.join("project").join("project-store.sqlite3");
        fs::create_dir_all(project_file.parent().expect("project file should have parent"))
            .expect("project directory should be created");
        fs::write(&project_file, b"scientific-project-bytes")
            .expect("project fixture should be written");
        let before = fs::read(&project_file).expect("project fixture should be readable");
        let preferences = json!({
            "contract_version": DESKTOP_PREFERENCES_CONTRACT_VERSION,
            "current_project_root": "/project",
            "recent_project_roots": ["/project"]
        });

        save_preferences_to_path(&config_path, &preferences).expect("preferences should save");

        let after = fs::read(&project_file).expect("project fixture should remain readable");
        assert_eq!(before, after);
        let _ = fs::remove_dir_all(root);
    }
}

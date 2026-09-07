use std::{
    fs::{self, OpenOptions},
    io::{self, Write},
    path::Path,
};

use serde_json::json;
use sha2::{Digest, Sha256};

const MAX_EXPORT_BYTES: usize = 32 * 1024 * 1024;

#[tauri::command]
pub(crate) fn desktop_export_report(
    output_directory: String,
    report_format: String,
    content_sha256: String,
    content: String,
) -> Result<String, String> {
    let receipt = export_report(
        Path::new(&output_directory),
        &report_format,
        &content_sha256,
        content.as_bytes(),
    )?;
    Ok(receipt.to_string())
}

fn export_report(
    output_directory: &Path,
    report_format: &str,
    content_sha256: &str,
    content: &[u8],
) -> Result<serde_json::Value, String> {
    if !output_directory.is_absolute() || !output_directory.is_dir() {
        return Err("desktop export directory must be an existing absolute directory".to_string());
    }
    let extension = match report_format {
        "json" => "json",
        "csv" => "csv",
        "markdown" => "md",
        _ => return Err("desktop report export format is unsupported".to_string()),
    };
    if !is_sha256(content_sha256) {
        return Err("desktop report export hash is invalid".to_string());
    }
    if content.len() > MAX_EXPORT_BYTES {
        return Err("desktop report export exceeds the local size limit".to_string());
    }
    let actual_sha256 = format!("{:x}", Sha256::digest(content));
    if content_sha256 != actual_sha256 {
        return Err("desktop report export content hash does not match exact bytes".to_string());
    }

    let file_name = format!("ecatvasp-report-{actual_sha256}.{extension}");
    let destination = output_directory.join(&file_name);
    let reused = match OpenOptions::new().write(true).create_new(true).open(&destination) {
        Ok(mut file) => {
            file.write_all(content)
                .and_then(|()| file.flush())
                .map_err(|_| "desktop report export could not be written".to_string())?;
            false
        }
        Err(error) if error.kind() == io::ErrorKind::AlreadyExists => {
            let existing = fs::read(&destination)
                .map_err(|_| "desktop report export target could not be verified".to_string())?;
            if existing != content {
                return Err("desktop report export target already exists with different content".to_string());
            }
            true
        }
        Err(_) => return Err("desktop report export could not create the target file".to_string()),
    };

    Ok(json!({
        "file_name": file_name,
        "report_format": report_format,
        "content_sha256": actual_sha256,
        "bytes_written": content.len(),
        "reused": reused,
    }))
}

fn is_sha256(value: &str) -> bool {
    value.len() == 64 && value.bytes().all(|byte| byte.is_ascii_hexdigit())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        env,
        fs,
        sync::atomic::{AtomicU64, Ordering},
    };

    static NEXT_EXPORT_TEST: AtomicU64 = AtomicU64::new(1);

    fn temporary_export_dir(name: &str) -> std::path::PathBuf {
        let sequence = NEXT_EXPORT_TEST.fetch_add(1, Ordering::Relaxed);
        let root = env::temp_dir().join(format!(
            "ecatvasp-export-{name}-{}-{sequence}",
            std::process::id()
        ));
        fs::create_dir_all(&root).expect("create export test directory");
        root
    }

    #[test]
    fn report_export_is_hash_named_and_idempotent() {
        let root = temporary_export_dir("deterministic");
        let digest = "f9497d656cead2a39ca3f15f578a9512a5e9feb8cab200e420e297e5d1afa28d";
        let content = b"{\"project\":\"fixture\"}\n";

        let first = export_report(&root, "json", digest, content).expect("first export");
        let second = export_report(&root, "json", digest, content).expect("reused export");

        assert_eq!(first["file_name"], format!("ecatvasp-report-{digest}.json"));
        assert_eq!(first["reused"], false);
        assert_eq!(second["reused"], true);
        assert_eq!(first["bytes_written"], content.len());
        assert_eq!(
            fs::read(root.join(format!("ecatvasp-report-{digest}.json"))).expect("read export"),
            content
        );
        fs::remove_dir_all(root).expect("remove export test directory");
    }

    #[test]
    fn report_export_never_overwrites_different_content() {
        let root = temporary_export_dir("collision");
        let digest = "97b0560280ed60a5a1eaa1bc45492543c8a986ad5a25b468c427eb83c3e88191";
        let destination = root.join(format!("ecatvasp-report-{digest}.csv"));
        fs::write(&destination, b"different").expect("write collision fixture");

        let error = export_report(&root, "csv", digest, b"current")
            .expect_err("different existing content must fail closed");

        assert!(error.contains("different content"));
        assert_eq!(fs::read(destination).expect("read collision fixture"), b"different");
        fs::remove_dir_all(root).expect("remove export test directory");
    }

    #[test]
    fn report_export_rejects_unsafe_or_untyped_inputs() {
        let root = temporary_export_dir("guards");
        let relative = Path::new("relative-export");
        let empty_object_hash =
            "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a";

        assert!(export_report(relative, "json", empty_object_hash, b"{}").is_err());
        assert!(export_report(&root, "html", empty_object_hash, b"{}").is_err());
        assert!(export_report(&root, "json", "not-a-hash", b"{}").is_err());
        let mismatch = export_report(&root, "json", empty_object_hash, b"[]")
            .expect_err("mismatched content hash must fail closed");
        assert!(mismatch.contains("does not match exact bytes"));

        fs::remove_dir_all(root).expect("remove export test directory");
    }
}

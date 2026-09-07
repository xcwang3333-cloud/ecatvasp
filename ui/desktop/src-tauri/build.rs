use std::{env, fs, path::Path};

const PNG_SIGNATURE: &[u8; 8] = b"\x89PNG\r\n\x1a\n";
const ICO_HEADER_LEN: u32 = 22;

fn main() {
    if env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("windows") {
        prepare_windows_icon();
    }
    tauri_build::build()
}

fn prepare_windows_icon() {
    let png_path = Path::new("icons/icon.png");
    let ico_path = Path::new("icons/icon.ico");
    println!("cargo:rerun-if-changed={}", png_path.display());

    let png = fs::read(png_path).expect("failed to read icons/icon.png");
    let (width, height) = png_dimensions(&png);
    let payload_len = u32::try_from(png.len()).expect("icon PNG is too large for ICO");

    let mut ico = Vec::with_capacity(ICO_HEADER_LEN as usize + png.len());
    ico.extend_from_slice(&0_u16.to_le_bytes());
    ico.extend_from_slice(&1_u16.to_le_bytes());
    ico.extend_from_slice(&1_u16.to_le_bytes());
    ico.push(ico_dimension(width));
    ico.push(ico_dimension(height));
    ico.push(0);
    ico.push(0);
    ico.extend_from_slice(&1_u16.to_le_bytes());
    ico.extend_from_slice(&32_u16.to_le_bytes());
    ico.extend_from_slice(&payload_len.to_le_bytes());
    ico.extend_from_slice(&ICO_HEADER_LEN.to_le_bytes());
    ico.extend_from_slice(&png);

    if fs::read(ico_path).ok().as_deref() != Some(ico.as_slice()) {
        fs::write(ico_path, ico).expect("failed to write generated icons/icon.ico");
    }
}

fn png_dimensions(png: &[u8]) -> (u32, u32) {
    assert!(png.len() >= 24, "icons/icon.png is too short");
    assert_eq!(&png[..8], PNG_SIGNATURE, "icons/icon.png has an invalid PNG signature");
    assert_eq!(&png[12..16], b"IHDR", "icons/icon.png has no leading IHDR chunk");

    let width = u32::from_be_bytes(png[16..20].try_into().expect("PNG width bytes missing"));
    let height = u32::from_be_bytes(png[20..24].try_into().expect("PNG height bytes missing"));
    assert!((1..=256).contains(&width), "Windows icon width must be 1..=256");
    assert!((1..=256).contains(&height), "Windows icon height must be 1..=256");
    (width, height)
}

fn ico_dimension(value: u32) -> u8 {
    if value == 256 {
        0
    } else {
        u8::try_from(value).expect("ICO dimension exceeds one-byte range")
    }
}

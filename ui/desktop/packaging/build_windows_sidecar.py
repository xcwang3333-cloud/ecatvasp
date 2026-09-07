"""Build the Windows x64 PyInstaller sidecar expected by Tauri externalBin."""

from __future__ import annotations

import platform
import shutil
import sys
from pathlib import Path

WINDOWS_TARGET_TRIPLE = "x86_64-pc-windows-msvc"
SIDECAR_BASENAME = "ecatvasp-desktop-backend"
ASE_IO_HIDDEN_IMPORTS = (
    "ase.io.vasp",
    "ase.io.cif",
    "ase.io.xyz",
    "ase.io.extxyz",
)


def main() -> int:
    """Build one clean Windows x64 sidecar into src-tauri/binaries."""

    _require_windows_x64()
    try:
        import PyInstaller.__main__ as pyinstaller
    except ImportError as error:  # pragma: no cover - packaging environment guard
        raise RuntimeError(
            "PyInstaller packaging requirements are not installed"
        ) from error

    desktop_root = Path(__file__).resolve().parents[1]
    repository_root = desktop_root.parents[1]
    entry_point = Path(__file__).with_name("backend_entry.py")
    binaries_dir = desktop_root / "src-tauri" / "binaries"
    build_root = desktop_root / ".packaging-build"
    work_dir = build_root / "work"
    spec_dir = build_root / "spec"
    output_name = f"{SIDECAR_BASENAME}-{WINDOWS_TARGET_TRIPLE}"
    hidden_import_args = [
        argument
        for module in ASE_IO_HIDDEN_IMPORTS
        for argument in ("--hidden-import", module)
    ]

    shutil.rmtree(build_root, ignore_errors=True)
    binaries_dir.mkdir(parents=True, exist_ok=True)
    expected_output = binaries_dir / f"{output_name}.exe"
    expected_output.unlink(missing_ok=True)

    pyinstaller.run(
        [
            str(entry_point),
            "--onefile",
            "--clean",
            "--noconfirm",
            "--noupx",
            "--console",
            "--log-level",
            "WARN",
            "--name",
            output_name,
            "--paths",
            str(repository_root / "src"),
            *hidden_import_args,
            "--distpath",
            str(binaries_dir),
            "--workpath",
            str(work_dir),
            "--specpath",
            str(spec_dir),
        ]
    )
    if not expected_output.is_file():
        raise RuntimeError(f"PyInstaller did not create expected sidecar: {expected_output}")
    print(expected_output)
    return 0


def _require_windows_x64() -> None:
    if sys.platform != "win32":
        raise RuntimeError("Windows sidecar packaging must run on Windows")
    machine = platform.machine().casefold()
    if machine not in {"amd64", "x86_64"}:
        raise RuntimeError(f"unsupported Windows packaging architecture: {machine}")


if __name__ == "__main__":  # pragma: no cover - packaging entry point
    raise SystemExit(main())

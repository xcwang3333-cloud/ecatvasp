from __future__ import annotations

import json
import tomllib
from pathlib import Path

from ecatvasp import __version__
from ecatvasp.schema.version import SCHEMA_VERSION

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_DESKTOP_ROOT = _REPOSITORY_ROOT / "ui" / "desktop"


def _json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_windows_packaging_overlay_is_platform_scoped_and_sidecar_typed() -> None:
    base = _json(_DESKTOP_ROOT / "src-tauri" / "tauri.conf.json")
    windows = _json(_DESKTOP_ROOT / "src-tauri" / "tauri.windows.conf.json")

    base_bundle = base["bundle"]
    windows_bundle = windows["bundle"]
    assert isinstance(base_bundle, dict)
    assert isinstance(windows_bundle, dict)
    assert base_bundle == {"active": False}
    assert windows_bundle["active"] is True
    assert windows_bundle["targets"] == ["nsis"]
    assert windows_bundle["externalBin"] == ["binaries/ecatvasp-desktop-backend"]

    windows_options = windows_bundle["windows"]
    assert isinstance(windows_options, dict)
    nsis = windows_options["nsis"]
    assert isinstance(nsis, dict)
    assert nsis["installMode"] == "currentUser"
    assert nsis["languages"] == ["English"]


def test_windows_packaging_tools_are_pinned_outside_scientific_runtime() -> None:
    requirements = (
        _DESKTOP_ROOT / "packaging" / "requirements-windows.txt"
    ).read_text(encoding="utf-8").splitlines()
    assert requirements == [
        "pyinstaller==6.22.2",
        "pyinstaller-hooks-contrib==2026.7",
    ]

    with (_REPOSITORY_ROOT / "pyproject.toml").open("rb") as stream:
        pyproject = tomllib.load(stream)
    project = pyproject["project"]
    assert project["version"] == "1.0.0.dev0"
    assert project["dependencies"] == ["ase>=3.29,<4", "numpy>=1.26"]
    assert all("pyinstaller" not in item.casefold() for item in project["dependencies"])
    assert __version__ == "1.0.0.dev0"
    assert SCHEMA_VERSION == 3


def test_frozen_sidecar_collects_supported_ase_structure_io_adapters() -> None:
    build_script = (
        _DESKTOP_ROOT / "packaging" / "build_windows_sidecar.py"
    ).read_text(encoding="utf-8")
    assert '"--hidden-import"' in build_script
    for module in (
        "ase.io.vasp",
        "ase.io.cif",
        "ase.io.xyz",
        "ase.io.extxyz",
    ):
        assert f'"{module}"' in build_script


def test_desktop_package_scripts_build_sidecar_before_windows_package() -> None:
    package = _json(_DESKTOP_ROOT / "package.json")
    scripts = package["scripts"]
    assert isinstance(scripts, dict)
    assert scripts["build:windows-sidecar"] == "python packaging/build_windows_sidecar.py"
    assert scripts["package:windows"] == (
        "npm run build:windows-sidecar && tauri build --bundles nsis"
    )


def test_windows_icon_resource_is_generated_without_image_runtime_dependency() -> None:
    build_rs = (_DESKTOP_ROOT / "src-tauri" / "build.rs").read_text(encoding="utf-8")
    assert "CARGO_CFG_TARGET_OS" in build_rs
    assert 'Path::new("icons/icon.png")' in build_rs
    assert 'Path::new("icons/icon.ico")' in build_rs
    assert "PNG_SIGNATURE" in build_rs
    assert "IHDR" in build_rs
    assert "Command::new" not in build_rs
    assert (_DESKTOP_ROOT / "src-tauri" / "icons" / "icon.png").is_file()


def test_generated_packaging_outputs_are_gitignored() -> None:
    ignored = (_REPOSITORY_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "ui/desktop/.packaging-build/" in ignored
    assert "ui/desktop/src-tauri/binaries/" in ignored
    assert "ui/desktop/src-tauri/target/" in ignored
    assert "ui/desktop/src-tauri/icons/icon.ico" in ignored


def test_packaging_entrypoints_exist_without_importing_build_dependencies() -> None:
    packaging_root = _DESKTOP_ROOT / "packaging"
    assert (packaging_root / "backend_entry.py").is_file()
    assert (packaging_root / "build_windows_sidecar.py").is_file()
    assert (packaging_root / "smoke_frozen_backend.py").is_file()

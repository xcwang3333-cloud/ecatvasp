from __future__ import annotations

import tomllib
from pathlib import Path

from ecatvasp import __version__
from ecatvasp.desktop.protocol import DesktopOperation
from ecatvasp.schema.version import SCHEMA_VERSION

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_DESKTOP_ROOT = _REPOSITORY_ROOT / "ui" / "desktop"


def test_block8_keeps_python_scientific_and_storage_contracts_frozen() -> None:
    with (_REPOSITORY_ROOT / "pyproject.toml").open("rb") as stream:
        pyproject = tomllib.load(stream)

    project = pyproject["project"]
    assert project["version"] == "1.0.0.dev0"
    assert project["dependencies"] == ["ase>=3.29,<4", "numpy>=1.26"]
    assert __version__ == "1.0.0.dev0"
    assert SCHEMA_VERSION == 3
    assert {operation.value for operation in DesktopOperation} == {
        "health",
        "open_project",
        "status",
        "frontend_handoff",
        "application_report",
        "prepare_workflow",
    }


def test_runtime_recovery_and_exports_stay_in_desktop_boundary() -> None:
    tauri_source = (_DESKTOP_ROOT / "src-tauri" / "src" / "lib.rs").read_text(
        encoding="utf-8"
    )
    export_source = (_DESKTOP_ROOT / "src-tauri" / "src" / "exports.rs").read_text(
        encoding="utf-8"
    )

    assert "fn backend_restart" in tauri_source
    assert "fn backend_diagnostics" in tauri_source
    assert 'record_backend_failure(state, "transport")' in tauri_source
    assert 'record_backend_failure(state, "compatibility")' in tauri_source
    assert "exports::desktop_export_report" in tauri_source
    assert 'Sha256::digest(content)' in export_source
    assert "ecatvasp-report-{actual_sha256}" in export_source
    assert ".create_new(true)" in export_source
    assert "project_root" not in export_source


def test_block8_architecture_record_exists() -> None:
    adr = _REPOSITORY_ROOT / (
        "docs/adr/0084-v10-desktop-resilience-security-diagnostics-exports.md"
    )
    text = adr.read_text(encoding="utf-8")
    assert "SCHEMA_VERSION` remains 3" in text
    assert "ecatvasp-desktop-ipc-v1" in text
    assert "runtime diagnostics and export receipts are non-scientific" in text

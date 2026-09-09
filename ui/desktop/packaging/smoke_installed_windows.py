"""Exercise the Windows NSIS-installed desktop and scientific backend boundaries."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from ecatvasp import __version__
from ecatvasp.desktop import (
    DESKTOP_IPC_CONTRACT_VERSION,
    DESKTOP_IPC_V2_CONTRACT_VERSION,
    DesktopOperation,
    DesktopV2Operation,
)
from ecatvasp.domain import Project
from ecatvasp.schema.version import SCHEMA_VERSION
from ecatvasp.storage import ProjectBundle, ProjectStore
from installed_acceptance_fixture import (
    InstalledAcceptanceFixture,
    build_installed_acceptance_fixture,
)

_DESKTOP_EXE = "ecatvasp-desktop.exe"
_SIDECAR_EXE = "ecatvasp-desktop-backend.exe"
_HOST_PROTOCOL = "ecatvasp-desktop-host-v1"
_BACKEND_OVERRIDE = "ECATVASP_DESKTOP_BACKEND"


@dataclass(frozen=True, slots=True)
class _ScientificIds:
    dos_analysis_id: str
    band_center_analysis_id: str
    clean_thermo_analysis_id: str
    ads_thermo_analysis_id: str
    h2_thermo_analysis_id: str
    reaction_analysis_id: str


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("install_dir", type=Path)
    args = parser.parse_args()

    install_dir = args.install_dir.resolve()
    if not install_dir.is_dir():
        raise RuntimeError(f"installed desktop directory is missing: {install_dir}")

    desktop = _unique_installed_file(install_dir, _DESKTOP_EXE)
    sidecar = _unique_installed_file(install_dir, _SIDECAR_EXE)
    _assert_within_install_dir(install_dir, desktop)
    _assert_within_install_dir(install_dir, sidecar)

    with TemporaryDirectory(prefix="ecatvasp-installed-e2e-") as directory:
        project_root = Path(directory) / "schema 3 project with spaces"
        fixture = build_installed_acceptance_fixture(project_root)
        store = ProjectStore(project_root)

        process = _start(sidecar)
        try:
            _assert_v1_health(
                _exchange(
                    process,
                    {
                        "protocol_version": DESKTOP_IPC_CONTRACT_VERSION,
                        "request_id": "installed-v1-health",
                        "operation": "health",
                    },
                )
            )
            _assert_v2_health(
                _exchange(process, _v2("installed-v2-health", "health"))
            )
            _assert_dashboard(
                _exchange(
                    process,
                    _v2(
                        "installed-dashboard",
                        "project_dashboard",
                        project_root=project_root,
                    ),
                ),
                project_id=fixture.project_id,
            )
            _assert_fail_closed_and_recovery(process, project_root)
            _assert_execution_boundary(process, fixture)
            ids = _materialize_scientific_chain(
                process,
                fixture,
                expect_reused=False,
                expected=None,
                suffix="initial",
            )
            _assert_current_catalogs(process, fixture, ids, suffix="initial")
        finally:
            _finish(process)

        restarted = _start(sidecar)
        try:
            _assert_dashboard(
                _exchange(
                    restarted,
                    _v2(
                        "installed-dashboard-after-restart",
                        "project_dashboard",
                        project_root=project_root,
                    ),
                ),
                project_id=fixture.project_id,
            )
            _assert_v2_health(
                _exchange(
                    restarted,
                    _v2("installed-v2-health-after-restart", "health"),
                )
            )
            reused = _materialize_scientific_chain(
                restarted,
                fixture,
                expect_reused=True,
                expected=ids,
                suffix="restart",
            )
            if reused != ids:
                raise RuntimeError("installed sidecar restart changed durable scientific identities")
            _assert_current_catalogs(restarted, fixture, ids, suffix="restart")

            _drift_scientific_sources(fixture)
            _assert_drifted_catalogs(restarted, fixture, ids)
            _assert_projectstore_tamper_is_rejected(restarted, project_root.parent)
            _assert_v2_health(
                _exchange(
                    restarted,
                    _v2("installed-v2-health-after-tamper-rejection", "health"),
                )
            )
        finally:
            _finish(restarted)

        reopened = store.open()
        _assert_permanent_identities(reopened, fixture)
        _assert_store_schema(store)
        _assert_installed_tauri_lifecycle(desktop, sidecar)

    print(f"Installed desktop: {desktop}")
    print(f"Installed backend: {sidecar}")
    return 0


def _v2(
    request_id: str,
    operation: str,
    *,
    project_root: Path | None = None,
    **fields: object,
) -> dict[str, object]:
    request: dict[str, object] = {
        "protocol_version": DESKTOP_IPC_V2_CONTRACT_VERSION,
        "request_id": request_id,
        "operation": operation,
    }
    if project_root is not None:
        request["project_root"] = str(project_root)
    request.update(fields)
    return request


def _assert_fail_closed_and_recovery(
    process: subprocess.Popen[str],
    project_root: Path,
) -> None:
    invalid_field = _exchange_uncorrelated(
        process,
        {
            "protocol_version": DESKTOP_IPC_V2_CONTRACT_VERSION,
            "request_id": "installed-invalid-field",
            "operation": "health",
            "scientific_override": {"converged": True},
        },
    )
    _assert_host_error(invalid_field, "unknown fields")

    generic_mutation = _exchange_uncorrelated(
        process,
        _v2(
            "installed-generic-mutation",
            "mutate_entity",
            project_root=project_root,
        ),
    )
    _assert_host_error(generic_mutation, "unsupported desktop operation")

    _assert_v2_health(
        _exchange(
            process,
            _v2("installed-v2-health-after-rejection", "health"),
        )
    )


def _assert_execution_boundary(
    process: subprocess.Popen[str],
    fixture: InstalledAcceptanceFixture,
) -> None:
    job_payload = _payload(
        _exchange(
            process,
            _v2(
                "installed-job-catalog",
                "job_catalog",
                project_root=fixture.project_root,
            ),
        )
    )
    job_row = _row_by_id(
        _dict_list(job_payload, "calculations"),
        "calculation_id",
        fixture.execution_calculation_id,
    )
    expected = {
        "scientific_status": "draft",
        "latest_attempt_id": fixture.execution_attempt_id,
        "attempt_status": "exited",
        "remote_job_id": fixture.remote_job_id,
        "scheduler_state": "completed",
        "scheduler_job_id": "987654",
    }
    for field, value in expected.items():
        if job_row.get(field) != value:
            raise RuntimeError(
                f"installed Job Center execution boundary drifted for {field}: {job_row!r}"
            )

    result_payload = _payload(
        _exchange(
            process,
            _v2(
                "installed-result-catalog",
                "result_catalog",
                project_root=fixture.project_root,
            ),
        )
    )
    result_row = _row_by_id(
        _dict_list(result_payload, "calculations"),
        "calculation_id",
        fixture.execution_calculation_id,
    )
    if result_row.get("scientific_status") != "draft":
        raise RuntimeError("scheduler completion was incorrectly promoted to scientific success")
    if result_row.get("attempt_status") != "exited":
        raise RuntimeError("installed Result Center lost execution-attempt truth")
    if result_row.get("analyzed") is not False:
        raise RuntimeError("installed Result Center invented scientific analysis")
    if result_row.get("promotion_ready") is not False:
        raise RuntimeError("installed Result Center invented promotion readiness")


def _materialize_scientific_chain(
    process: subprocess.Popen[str],
    fixture: InstalledAcceptanceFixture,
    *,
    expect_reused: bool,
    expected: _ScientificIds | None,
    suffix: str,
) -> _ScientificIds:
    electronic_catalog = _payload(
        _exchange(
            process,
            _v2(
                f"installed-electronic-catalog-{suffix}",
                "electronic_analysis_catalog",
                project_root=fixture.project_root,
            ),
        )
    )
    dos_source = _row_by_id(
        _dict_list(electronic_catalog, "dos_sources"),
        "calculation_id",
        fixture.dos_calculation_id,
    )
    if dos_source.get("scientific_status") != "converged":
        raise RuntimeError("installed electronic workspace lost DOS scientific status")

    dos = _materialize(
        process,
        _v2(
            f"installed-dos-{suffix}",
            "materialize_dos_analysis",
            project_root=fixture.project_root,
            calculation_id=fixture.dos_calculation_id,
        ),
        expect_reused=expect_reused,
        expected_analysis_id=(expected.dos_analysis_id if expected is not None else None),
    )
    dos_analysis_id = _required_text(dos, "analysis_id")
    _assert_analysis_view(
        process,
        fixture.project_root,
        operation="electronic_analysis_view",
        analysis_id=dos_analysis_id,
        request_id=f"installed-dos-view-{suffix}",
        expected_type="dos",
    )

    band = _materialize(
        process,
        _v2(
            f"installed-band-center-{suffix}",
            "materialize_band_center",
            project_root=fixture.project_root,
            source_analysis_id=dos_analysis_id,
            kind="d_band",
            scope="atom",
            spin="total",
            atom_uid=fixture.dos_atom_uid,
            element="C",
            energy_reference="vasp_native",
            window_lower_ev=-1.0,
            window_upper_ev=1.0,
        ),
        expect_reused=expect_reused,
        expected_analysis_id=(
            expected.band_center_analysis_id if expected is not None else None
        ),
    )
    band_analysis_id = _required_text(band, "analysis_id")
    _assert_analysis_view(
        process,
        fixture.project_root,
        operation="electronic_analysis_view",
        analysis_id=band_analysis_id,
        request_id=f"installed-band-view-{suffix}",
        expected_type="band_center",
    )

    thermo_catalog = _payload(
        _exchange(
            process,
            _v2(
                f"installed-thermo-catalog-prerequisite-{suffix}",
                "thermochemistry_catalog",
                project_root=fixture.project_root,
            ),
        )
    )
    for calculation_id in (
        fixture.clean_frequency_calculation_id,
        fixture.ads_frequency_calculation_id,
        fixture.h2_frequency_calculation_id,
    ):
        row = _row_by_id(
            _dict_list(thermo_catalog, "frequency_sources"),
            "calculation_id",
            calculation_id,
        )
        if row.get("source_ready") is not True:
            raise RuntimeError(f"installed frequency prerequisite is not ready: {row!r}")

    clean = _materialize(
        process,
        _v2(
            f"installed-clean-thermo-{suffix}",
            "materialize_harmonic_thermochemistry",
            project_root=fixture.project_root,
            **_harmonic_fields(
                fixture.clean_frequency_calculation_id,
                subject_kind="surface",
            ),
        ),
        expect_reused=expect_reused,
        expected_analysis_id=(
            expected.clean_thermo_analysis_id if expected is not None else None
        ),
    )
    clean_id = _required_text(clean, "analysis_id")

    ads = _materialize(
        process,
        _v2(
            f"installed-ads-thermo-{suffix}",
            "materialize_harmonic_thermochemistry",
            project_root=fixture.project_root,
            **_harmonic_fields(
                fixture.ads_frequency_calculation_id,
                subject_kind="adsorbate",
            ),
        ),
        expect_reused=expect_reused,
        expected_analysis_id=(
            expected.ads_thermo_analysis_id if expected is not None else None
        ),
    )
    ads_id = _required_text(ads, "analysis_id")

    h2 = _materialize(
        process,
        _v2(
            f"installed-h2-thermo-{suffix}",
            "materialize_gas_reference",
            project_root=fixture.project_root,
            **_h2_fields(fixture),
        ),
        expect_reused=expect_reused,
        expected_analysis_id=(
            expected.h2_thermo_analysis_id if expected is not None else None
        ),
    )
    h2_id = _required_text(h2, "analysis_id")

    for name, analysis_id in (
        ("clean", clean_id),
        ("ads", ads_id),
        ("h2", h2_id),
    ):
        _assert_analysis_view(
            process,
            fixture.project_root,
            operation="thermochemistry_view",
            analysis_id=analysis_id,
            request_id=f"installed-{name}-thermo-view-{suffix}",
            expected_type="thermochemistry",
        )

    reaction_fields = _reaction_fields(clean_id, ads_id, h2_id)
    preview = _payload(
        _exchange(
            process,
            _v2(
                f"installed-her-preview-{suffix}",
                "reaction_preview",
                project_root=fixture.project_root,
                **reaction_fields,
            ),
        )
    )
    if preview.get("preset_kind") != "her_volmer_heyrovsky":
        raise RuntimeError("installed HER preview returned the wrong preset")
    for field in ("preset_hash", "pathway_definition", "baseline_result", "potential_view"):
        if preview.get(field) is None:
            raise RuntimeError(f"installed HER preview is missing {field}")

    reaction = _materialize(
        process,
        _v2(
            f"installed-her-diagram-{suffix}",
            "materialize_reaction_diagram",
            project_root=fixture.project_root,
            **reaction_fields,
        ),
        expect_reused=expect_reused,
        expected_analysis_id=(expected.reaction_analysis_id if expected is not None else None),
    )
    reaction_id = _required_text(reaction, "analysis_id")
    _assert_analysis_view(
        process,
        fixture.project_root,
        operation="reaction_diagram_view",
        analysis_id=reaction_id,
        request_id=f"installed-her-diagram-view-{suffix}",
        expected_type="reaction_diagram",
    )

    return _ScientificIds(
        dos_analysis_id=dos_analysis_id,
        band_center_analysis_id=band_analysis_id,
        clean_thermo_analysis_id=clean_id,
        ads_thermo_analysis_id=ads_id,
        h2_thermo_analysis_id=h2_id,
        reaction_analysis_id=reaction_id,
    )


def _harmonic_fields(calculation_id: str, *, subject_kind: str) -> dict[str, object]:
    return {
        "calculation_id": calculation_id,
        "subject_kind": subject_kind,
        "temperature_k": 298.15,
        "electronic_energy_kind": "energy_sigma0_ev",
        "electronic_entropy_policy": "neglected",
        "frequency_cutoff_cm_inverse": 50.0,
        "imaginary_mode_policy": "reject_any",
        "low_frequency_policy": "reject_below_cutoff",
        "exclusions": [],
    }


def _h2_fields(fixture: InstalledAcceptanceFixture) -> dict[str, object]:
    return {
        "calculation_id": fixture.h2_frequency_calculation_id,
        "species": "H2",
        "temperature_k": 298.15,
        "pressure_pa": 100000.0,
        "standard_state": "ideal_gas_1_bar",
        "electronic_energy_kind": "energy_sigma0_ev",
        "electronic_entropy_policy": "neglected",
        "geometry_kind": "linear",
        "symmetry_number": 2,
        "spin_multiplicity": 1,
        "atomic_masses": [
            {"atom_uid": atom_uid, "mass_amu": 1.00784}
            for atom_uid in fixture.h2_atom_uids
        ],
        "frequency_cutoff_cm_inverse": 50.0,
        "imaginary_mode_policy": "reject_any",
        "low_frequency_policy": "exclude_explicit",
        "exclusions": [
            {"mode_index": 1, "reason": "translational"},
            {"mode_index": 2, "reason": "translational"},
            {"mode_index": 3, "reason": "translational"},
            {"mode_index": 4, "reason": "rotational"},
            {"mode_index": 5, "reason": "rotational"},
        ],
    }


def _reaction_fields(clean_id: str, ads_id: str, h2_id: str) -> dict[str, object]:
    return {
        "preset_kind": "her_volmer_heyrovsky",
        "bindings": {
            "clean_surface_analysis_id": clean_id,
            "h_adsorbed_analysis_id": ads_id,
            "h2_reference_analysis_id": h2_id,
        },
        "baseline_conditions": _conditions(potential_v=0.0, ph=0.0),
        "requested_conditions": _conditions(potential_v=-0.25, ph=7.0),
    }


def _conditions(*, potential_v: float, ph: float) -> dict[str, object]:
    return {
        "temperature_k": 298.15,
        "potential_v": potential_v,
        "ph": ph,
        "potential_reference": "she",
        "ph_semantics": "explicit_activity",
    }


def _materialize(
    process: subprocess.Popen[str],
    request: dict[str, object],
    *,
    expect_reused: bool,
    expected_analysis_id: str | None,
) -> dict[str, Any]:
    payload = _payload(_exchange(process, request))
    if payload.get("reused") is not expect_reused:
        raise RuntimeError(
            f"installed materialization reuse contract drifted: {request['operation']!r} {payload!r}"
        )
    analysis_id = _required_text(payload, "analysis_id")
    if expected_analysis_id is not None and analysis_id != expected_analysis_id:
        raise RuntimeError(
            f"installed materialization changed exact Analysis identity: {request['operation']!r}"
        )
    if not _required_text(payload, "artifact_id"):
        raise RuntimeError("installed materialization did not return an Artifact identity")
    return payload


def _assert_analysis_view(
    process: subprocess.Popen[str],
    project_root: Path,
    *,
    operation: str,
    analysis_id: str,
    request_id: str,
    expected_type: str,
) -> None:
    payload = _payload(
        _exchange(
            process,
            _v2(
                request_id,
                operation,
                project_root=project_root,
                analysis_id=analysis_id,
            ),
        )
    )
    if payload.get("analysis_id") != analysis_id:
        raise RuntimeError("installed scientific view returned a different Analysis identity")
    if payload.get("analysis_type") != expected_type:
        raise RuntimeError(f"installed scientific view returned the wrong type: {payload!r}")
    _assert_freshness(payload.get("freshness"), expected_state="completed")
    if not isinstance(payload.get("view"), dict):
        raise RuntimeError("installed scientific view payload is missing canonical view data")


def _assert_current_catalogs(
    process: subprocess.Popen[str],
    fixture: InstalledAcceptanceFixture,
    ids: _ScientificIds,
    *,
    suffix: str,
) -> None:
    electronic = _payload(
        _exchange(
            process,
            _v2(
                f"installed-electronic-current-{suffix}",
                "electronic_analysis_catalog",
                project_root=fixture.project_root,
            ),
        )
    )
    electronic_rows = _dict_list(electronic, "analyses")
    for analysis_id in (ids.dos_analysis_id, ids.band_center_analysis_id):
        _assert_freshness(
            _row_by_id(electronic_rows, "analysis_id", analysis_id).get("freshness"),
            expected_state="completed",
        )

    thermo = _payload(
        _exchange(
            process,
            _v2(
                f"installed-thermo-current-{suffix}",
                "thermochemistry_catalog",
                project_root=fixture.project_root,
            ),
        )
    )
    thermo_rows = _dict_list(thermo, "analyses")
    for analysis_id in (
        ids.clean_thermo_analysis_id,
        ids.ads_thermo_analysis_id,
        ids.h2_thermo_analysis_id,
        ids.reaction_analysis_id,
    ):
        _assert_freshness(
            _row_by_id(thermo_rows, "analysis_id", analysis_id).get("freshness"),
            expected_state="completed",
        )


def _drift_scientific_sources(fixture: InstalledAcceptanceFixture) -> None:
    with fixture.doscar_path.open("ab") as handle:
        handle.write(b"installed deterministic DOSCAR drift\n")
    with fixture.ads_outcar_path.open("ab") as handle:
        handle.write(b"installed deterministic adsorbate OUTCAR drift\n")


def _assert_drifted_catalogs(
    process: subprocess.Popen[str],
    fixture: InstalledAcceptanceFixture,
    ids: _ScientificIds,
) -> None:
    electronic = _payload(
        _exchange(
            process,
            _v2(
                "installed-electronic-after-drift",
                "electronic_analysis_catalog",
                project_root=fixture.project_root,
            ),
        )
    )
    electronic_rows = _dict_list(electronic, "analyses")
    for analysis_id in (ids.dos_analysis_id, ids.band_center_analysis_id):
        _assert_freshness(
            _row_by_id(electronic_rows, "analysis_id", analysis_id).get("freshness"),
            expected_state="stale",
        )

    thermo = _payload(
        _exchange(
            process,
            _v2(
                "installed-thermo-after-drift",
                "thermochemistry_catalog",
                project_root=fixture.project_root,
            ),
        )
    )
    rows = _dict_list(thermo, "analyses")
    _assert_freshness(
        _row_by_id(rows, "analysis_id", ids.clean_thermo_analysis_id).get("freshness"),
        expected_state="completed",
    )
    _assert_freshness(
        _row_by_id(rows, "analysis_id", ids.h2_thermo_analysis_id).get("freshness"),
        expected_state="completed",
    )
    for analysis_id in (ids.ads_thermo_analysis_id, ids.reaction_analysis_id):
        _assert_freshness(
            _row_by_id(rows, "analysis_id", analysis_id).get("freshness"),
            expected_state="stale",
        )


def _assert_freshness(value: object, *, expected_state: str) -> None:
    if not isinstance(value, dict):
        raise RuntimeError("installed scientific freshness projection is missing")
    expected_readiness = "satisfied" if expected_state == "completed" else "blocked"
    if value.get("scientific_state") != expected_state:
        raise RuntimeError(
            f"installed scientific freshness state drifted; expected {expected_state}: {value!r}"
        )
    if value.get("readiness") != expected_readiness:
        raise RuntimeError(
            f"installed scientific readiness drifted; expected {expected_readiness}: {value!r}"
        )


def _assert_projectstore_tamper_is_rejected(
    process: subprocess.Popen[str],
    parent: Path,
) -> None:
    tamper_root = parent / "tampered schema 3 project"
    tamper_project = Project(name="Tamper acceptance", slug="tamper-acceptance")
    tamper_store = ProjectStore(tamper_root)
    tamper_store.save(ProjectBundle(project=tamper_project))

    connection = sqlite3.connect(tamper_store.database_path)
    try:
        connection.execute(
            "UPDATE metadata SET value = 'tampered' WHERE key = 'format'"
        )
        connection.commit()
    finally:
        connection.close()

    response = _exchange(
        process,
        _v2(
            "installed-tampered-dashboard",
            "project_dashboard",
            project_root=tamper_root,
        ),
    )
    if response.get("ok") is not False:
        raise RuntimeError("installed backend silently accepted a tampered ProjectStore")
    error = response.get("error")
    if not isinstance(error, dict) or error.get("code") != "project_unavailable":
        raise RuntimeError(f"installed backend tamper error contract drifted: {response!r}")


def _assert_permanent_identities(
    bundle: ProjectBundle,
    fixture: InstalledAcceptanceFixture,
) -> None:
    if str(bundle.project.id) != fixture.project_id:
        raise RuntimeError("installed sidecar changed permanent Project identity")
    if fixture.execution_calculation_id not in {str(item.id) for item in bundle.calculations}:
        raise RuntimeError("installed acceptance lost permanent Calculation identity")
    if fixture.execution_attempt_id not in {str(item.id) for item in bundle.execution_attempts}:
        raise RuntimeError("installed acceptance lost permanent ExecutionAttempt identity")
    if fixture.remote_job_id not in {str(item.id) for item in bundle.remote_jobs}:
        raise RuntimeError("installed acceptance lost permanent RemoteJob identity")


def _assert_store_schema(store: ProjectStore) -> None:
    connection = sqlite3.connect(store.database_path)
    try:
        row = connection.execute(
            "SELECT value FROM metadata WHERE key = 'schema_version'"
        ).fetchone()
    finally:
        connection.close()
    if row != (str(SCHEMA_VERSION),):
        raise RuntimeError(
            "installed acceptance ProjectStore schema drifted: "
            f"expected {SCHEMA_VERSION}, got {row!r}"
        )


def _assert_installed_tauri_lifecycle(desktop: Path, sidecar: Path) -> None:
    _kill_installed_backend(sidecar)
    env = os.environ.copy()
    env.pop(_BACKEND_OVERRIDE, None)
    process = subprocess.Popen(
        [str(desktop)],
        cwd=desktop.parent,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        record = _wait_for_bundled_backend(sidecar, parent_pid=process.pid, timeout_seconds=30)
        if process.poll() is not None:
            stderr = "" if process.stderr is None else process.stderr.read()
            raise RuntimeError(f"installed Tauri desktop exited during startup: {stderr.strip()}")
        if int(record.get("ParentProcessId", -1)) != process.pid:
            raise RuntimeError("installed backend is not a child of the installed Tauri desktop")
        _close_main_window(process)
        _wait_for_backend_exit(sidecar, parent_pid=process.pid, timeout_seconds=15)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        _kill_installed_backend(sidecar)


def _wait_for_bundled_backend(
    sidecar: Path,
    *,
    parent_pid: int,
    timeout_seconds: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        for record in _windows_process_records(_SIDECAR_EXE):
            path = record.get("ExecutablePath")
            if not isinstance(path, str):
                continue
            if _same_windows_path(Path(path), sidecar) and record.get("ParentProcessId") == parent_pid:
                return record
        time.sleep(0.25)
    raise RuntimeError("installed Tauri desktop did not spawn its bundled sibling backend")


def _wait_for_backend_exit(
    sidecar: Path,
    *,
    parent_pid: int,
    timeout_seconds: float,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        matching = [
            record
            for record in _windows_process_records(_SIDECAR_EXE)
            if isinstance(record.get("ExecutablePath"), str)
            and _same_windows_path(Path(str(record["ExecutablePath"])), sidecar)
            and record.get("ParentProcessId") == parent_pid
        ]
        if not matching:
            return
        time.sleep(0.25)
    raise RuntimeError("installed Tauri desktop left its bundled backend running after window close")


def _close_main_window(process: subprocess.Popen[str]) -> None:
    script = (
        f"$p = Get-Process -Id {process.pid} -ErrorAction Stop; "
        "if (-not $p.CloseMainWindow()) { exit 3 }; "
        "if (-not $p.WaitForExit(15000)) { exit 4 }"
    )
    completed = subprocess.run(
        ["pwsh", "-NoLogo", "-NoProfile", "-Command", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "installed Tauri desktop did not support a clean main-window close "
            f"(exit {completed.returncode}): {completed.stderr.strip()}"
        )
    process.wait(timeout=5)


def _windows_process_records(name: str) -> list[dict[str, Any]]:
    escaped = name.replace("'", "''")
    script = (
        f"$rows = @(Get-CimInstance Win32_Process -Filter \"Name = '{escaped}'\" | "
        "Select-Object ProcessId,ParentProcessId,ExecutablePath); "
        "ConvertTo-Json -InputObject $rows -Compress"
    )
    completed = subprocess.run(
        ["pwsh", "-NoLogo", "-NoProfile", "-Command", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    value = json.loads(completed.stdout.strip() or "[]")
    if not isinstance(value, list):
        raise RuntimeError("Windows process query did not return an array")
    return [item for item in value if isinstance(item, dict)]


def _kill_installed_backend(sidecar: Path) -> None:
    for record in _windows_process_records(_SIDECAR_EXE):
        path = record.get("ExecutablePath")
        pid = record.get("ProcessId")
        if not isinstance(path, str) or not isinstance(pid, int):
            continue
        if not _same_windows_path(Path(path), sidecar):
            continue
        subprocess.run(
            [
                "pwsh",
                "-NoLogo",
                "-NoProfile",
                "-Command",
                f"Stop-Process -Id {pid} -Force -ErrorAction SilentlyContinue",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )


def _same_windows_path(left: Path, right: Path) -> bool:
    return str(left.resolve()).casefold() == str(right.resolve()).casefold()


def _unique_installed_file(install_dir: Path, name: str) -> Path:
    matches = [path.resolve() for path in install_dir.rglob(name) if path.is_file()]
    if len(matches) != 1:
        raise RuntimeError(
            f"expected exactly one installed {name}, found {len(matches)} under {install_dir}"
        )
    return matches[0]


def _assert_within_install_dir(install_dir: Path, path: Path) -> None:
    try:
        path.relative_to(install_dir)
    except ValueError as error:
        raise RuntimeError(f"installed artifact escaped install directory: {path}") from error


def _start(sidecar: Path) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [str(sidecar)],
        cwd=sidecar.parent,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )


def _finish(process: subprocess.Popen[str]) -> None:
    if process.stdin is not None:
        process.stdin.close()
    return_code = process.wait(timeout=30)
    stderr = "" if process.stderr is None else process.stderr.read()
    if return_code != 0:
        raise RuntimeError(f"installed backend exited with {return_code}: {stderr.strip()}")
    if stderr.strip():
        raise RuntimeError(f"installed backend wrote unexpected stderr: {stderr.strip()}")


def _write_and_read(
    process: subprocess.Popen[str], request: dict[str, object]
) -> dict[str, Any]:
    if process.stdin is None or process.stdout is None:
        raise RuntimeError("installed backend stdio pipes are unavailable")
    process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
    process.stdin.flush()
    line = process.stdout.readline()
    if not line:
        raise RuntimeError("installed backend closed stdout before responding")
    response = json.loads(line)
    if not isinstance(response, dict):
        raise RuntimeError("installed backend response is not a JSON object")
    return response


def _exchange(
    process: subprocess.Popen[str], request: dict[str, object]
) -> dict[str, Any]:
    response = _write_and_read(process, request)
    if response.get("request_id") != request["request_id"]:
        raise RuntimeError("installed backend response request_id mismatch")
    if response.get("operation") != request["operation"]:
        raise RuntimeError("installed backend response operation mismatch")
    if response.get("protocol_version") != request["protocol_version"]:
        raise RuntimeError("installed backend response protocol_version mismatch")
    return response


def _exchange_uncorrelated(
    process: subprocess.Popen[str], request: dict[str, object]
) -> dict[str, Any]:
    return _write_and_read(process, request)


def _payload(response: dict[str, Any]) -> dict[str, Any]:
    if response.get("ok") is not True:
        raise RuntimeError(f"installed backend operation failed: {response!r}")
    payload = response.get("payload")
    if not isinstance(payload, dict):
        raise RuntimeError("installed backend response payload is invalid")
    return payload


def _required_text(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"installed backend payload requires non-empty {field}: {payload!r}")
    return value


def _dict_list(payload: dict[str, Any], field: str) -> list[dict[str, Any]]:
    value = payload.get(field)
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise RuntimeError(f"installed backend payload requires object array {field}")
    return list(value)


def _row_by_id(
    rows: list[dict[str, Any]],
    field: str,
    expected: str,
) -> dict[str, Any]:
    matches = [row for row in rows if row.get(field) == expected]
    if len(matches) != 1:
        raise RuntimeError(
            f"expected one installed catalog row for {field}={expected!r}, found {len(matches)}"
        )
    return matches[0]


def _assert_v1_health(response: dict[str, Any]) -> None:
    payload = _payload(response)
    if response.get("protocol_version") != DESKTOP_IPC_CONTRACT_VERSION:
        raise RuntimeError("installed backend v1 protocol drifted")
    if payload.get("backend_version") != __version__:
        raise RuntimeError("installed backend package version mismatch")
    if payload.get("operations") != [operation.value for operation in DesktopOperation]:
        raise RuntimeError("installed backend v1 operation set drifted")
    if payload.get("stateless_project_requests") is not True:
        raise RuntimeError("installed backend v1 stateless request contract drifted")


def _assert_v2_health(response: dict[str, Any]) -> None:
    payload = _payload(response)
    if response.get("protocol_version") != DESKTOP_IPC_V2_CONTRACT_VERSION:
        raise RuntimeError("installed backend v2 protocol drifted")
    if payload.get("backend_version") != __version__:
        raise RuntimeError("installed backend package version mismatch")
    if payload.get("operations") != [operation.value for operation in DesktopV2Operation]:
        raise RuntimeError("installed backend v2 operation set drifted")
    if payload.get("supported_protocol_versions") != [
        DESKTOP_IPC_CONTRACT_VERSION,
        DESKTOP_IPC_V2_CONTRACT_VERSION,
    ]:
        raise RuntimeError("installed backend supported protocol set drifted")
    if payload.get("stateless_project_requests") is not True:
        raise RuntimeError("installed backend v2 stateless request contract drifted")


def _assert_dashboard(response: dict[str, Any], *, project_id: str) -> None:
    payload = _payload(response)
    dashboard = payload.get("dashboard")
    if not isinstance(dashboard, dict):
        raise RuntimeError("installed backend project dashboard is missing")
    if dashboard.get("project_id") != project_id:
        raise RuntimeError("installed backend dashboard belongs to the wrong Project")
    if dashboard.get("schema_version") != SCHEMA_VERSION:
        raise RuntimeError("installed backend dashboard schema mismatch")
    for namespace in (
        "model_counts",
        "workflow_counts",
        "calculations",
        "analyses",
        "execution_attempts",
        "scheduler_jobs",
        "freshness",
    ):
        if namespace not in dashboard:
            raise RuntimeError(f"installed backend dashboard is missing {namespace}")


def _assert_host_error(response: dict[str, Any], message_fragment: str) -> None:
    if response.get("host_protocol_version") != _HOST_PROTOCOL:
        raise RuntimeError("installed backend host error protocol drifted")
    if response.get("frame_type") != "host_error" or response.get("ok") is not False:
        raise RuntimeError("installed backend did not emit a fail-closed host error")
    error = response.get("error")
    if not isinstance(error, dict) or error.get("code") != "invalid_request":
        raise RuntimeError("installed backend host error payload is invalid")
    message = error.get("message")
    if not isinstance(message, str) or message_fragment not in message:
        raise RuntimeError(
            f"installed backend host error did not contain {message_fragment!r}: {response!r}"
        )


if __name__ == "__main__":  # pragma: no cover - packaging entry point
    raise SystemExit(main())

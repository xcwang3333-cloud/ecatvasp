from __future__ import annotations

import json
from pathlib import Path

import pytest

from ecatvasp.api.result_center import (
    ProjectResultCenterApplicationService,
    _mark_attempt_parsed,
)
from ecatvasp.desktop.protocol import DesktopIPCError
from ecatvasp.desktop.protocol_v2 import DesktopBackendV2, decode_desktop_v2_request
from ecatvasp.desktop.protocol_v2_result_center import (
    DesktopV2AnalyzeResultRequest,
    DesktopV2ResultCatalogRequest,
)
from ecatvasp.domain import (
    Calculation,
    CalculationScientificStatus,
    CalculationType,
    ExecutionAttempt,
    ExecutionAttemptStatus,
    KPointPolicy,
    KPointPolicyKind,
    Lattice,
    MethodDefinition,
    MethodFingerprint,
    PotcarIdentity,
    Project,
    ProtocolDefinition,
    RecipeIdentity,
    StructureSite,
    StructureSnapshot,
    new_atom_uid,
)
from ecatvasp.storage import ProjectBundle, ProjectStore


def _snapshot() -> StructureSnapshot:
    return StructureSnapshot(
        lattice=Lattice(
            vectors=((8.0, 0.0, 0.0), (0.0, 8.0, 0.0), (0.0, 0.0, 15.0))
        ),
        sites=(StructureSite(new_atom_uid(), "C", (0.5, 0.5, 0.5)),),
        label="result-center-root",
    )


def _fingerprint() -> MethodFingerprint:
    return MethodFingerprint(
        method=MethodDefinition(
            xc_functional="PBE",
            potcar_family="PBE_54",
            potcars=(PotcarIdentity("C", "C", "1" * 64),),
        ),
        protocol=ProtocolDefinition(
            encut_ev=450.0,
            kpoints=KPointPolicy(KPointPolicyKind.GAMMA_ONLY),
            ediffg_ev_per_angstrom=-0.02,
        ),
        recipe=RecipeIdentity("ECatVASP.VASP.SlabRelax"),
    )


def _case(
    tmp_path: Path,
    *,
    status: ExecutionAttemptStatus = ExecutionAttemptStatus.EXITED,
) -> tuple[ProjectStore, Calculation, ExecutionAttempt]:
    project = Project(name="Result Center", slug="result-center")
    snapshot = _snapshot()
    fingerprint = _fingerprint()
    calculation = Calculation(
        project_id=project.id,
        calculation_type=CalculationType.RELAX,
        input_structure_snapshot_id=snapshot.id,
        recipe_id=fingerprint.recipe.recipe_id,
        method_fingerprint_id=fingerprint.id,
        status=CalculationScientificStatus.DRAFT,
    )
    attempt = ExecutionAttempt(
        calculation_id=calculation.id,
        attempt_number=1,
        status=status,
    )
    store = ProjectStore(tmp_path)
    store.save(
        ProjectBundle(
            project=project,
            structure_snapshots=(snapshot,),
            method_fingerprints=(fingerprint,),
            calculations=(calculation,),
            execution_attempts=(attempt,),
        )
    )
    return store, calculation, attempt


def _request(operation: str, **extra: object) -> str:
    return json.dumps(
        {
            "protocol_version": "ecatvasp-desktop-ipc-v2",
            "request_id": "result-1",
            "operation": operation,
            "project_root": "/tmp/project",
            **extra,
        }
    )


def test_result_center_ipc_decodes_closed_request_types() -> None:
    catalog = decode_desktop_v2_request(_request("result_catalog"))
    assert isinstance(catalog, DesktopV2ResultCatalogRequest)

    analyze = decode_desktop_v2_request(
        _request(
            "analyze_result",
            calculation_id="11111111-1111-1111-1111-111111111111",
        )
    )
    assert isinstance(analyze, DesktopV2AnalyzeResultRequest)


def test_result_center_ipc_rejects_plan_hash_or_verdict_escape_hatches() -> None:
    with pytest.raises(DesktopIPCError, match="unknown fields"):
        decode_desktop_v2_request(
            _request(
                "analyze_result",
                calculation_id="11111111-1111-1111-1111-111111111111",
                plan_hash="a" * 64,
            )
        )
    with pytest.raises(DesktopIPCError, match="unknown fields"):
        decode_desktop_v2_request(
            _request(
                "promote_result_structure",
                calculation_id="11111111-1111-1111-1111-111111111111",
                convergence_verdict="converged",
            )
        )


def test_v2_health_advertises_result_center_operations() -> None:
    request = decode_desktop_v2_request(
        json.dumps(
            {
                "protocol_version": "ecatvasp-desktop-ipc-v2",
                "request_id": "health-1",
                "operation": "health",
            }
        )
    )
    response = DesktopBackendV2().handle(request)
    assert response.ok is True
    assert response.payload is not None
    operations = response.payload["operations"]
    assert isinstance(operations, list)
    assert "result_catalog" in operations
    assert "analyze_result" in operations
    assert "promote_result_structure" in operations


@pytest.mark.parametrize(
    "initial",
    (ExecutionAttemptStatus.EXITED, ExecutionAttemptStatus.RETRIEVING),
)
def test_result_parsing_marks_successful_attempt_parsed(
    tmp_path: Path,
    initial: ExecutionAttemptStatus,
) -> None:
    store, _, attempt = _case(tmp_path, status=initial)
    persisted = _mark_attempt_parsed(store, attempt.id)
    assert persisted.status is ExecutionAttemptStatus.PARSED
    reopened = store.open()
    current = next(item for item in reopened.execution_attempts if item.id == attempt.id)
    assert current.status is ExecutionAttemptStatus.PARSED


@pytest.mark.parametrize(
    "terminal",
    (ExecutionAttemptStatus.FAILED, ExecutionAttemptStatus.CANCELLED),
)
def test_result_parsing_never_erases_failed_or_cancelled_execution_truth(
    tmp_path: Path,
    terminal: ExecutionAttemptStatus,
) -> None:
    store, _, attempt = _case(tmp_path, status=terminal)
    persisted = _mark_attempt_parsed(store, attempt.id)
    assert persisted.status is terminal


def test_result_catalog_does_not_infer_scientific_success_from_exited_attempt(
    tmp_path: Path,
) -> None:
    store, calculation, attempt = _case(tmp_path)
    payload = ProjectResultCenterApplicationService(store).catalog()
    rows = payload["calculations"]
    assert isinstance(rows, list)
    assert len(rows) == 1
    row = rows[0]
    assert isinstance(row, dict)
    assert row["calculation_id"] == str(calculation.id)
    assert row["scientific_status"] == CalculationScientificStatus.DRAFT.value
    assert row["attempt_status"] == attempt.status.value
    assert row["analysis_ready"] is False
    assert row["promotion_ready"] is False

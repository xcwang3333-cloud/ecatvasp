from __future__ import annotations

from pathlib import Path

import pytest

from ecatvasp.api import ProjectApplicationService
from ecatvasp.desktop import (
    DESKTOP_IPC_CONTRACT_VERSION,
    DesktopBackend,
    DesktopIPCError,
    DesktopOperation,
    DesktopRequest,
    decode_desktop_request,
)
from ecatvasp.domain import (
    Lattice,
    Project,
    StructureOrigin,
    StructureSite,
    StructureSnapshot,
    new_atom_uid,
    new_structure_snapshot_id,
)
from ecatvasp.schema.version import SCHEMA_VERSION
from ecatvasp.storage import ProjectBundle, ProjectStore
from ecatvasp.workflow import WORKFLOW_RECIPE_SLAB_SCIENTIFIC_PREPARATION


def _snapshot() -> StructureSnapshot:
    return StructureSnapshot(
        lattice=Lattice(
            vectors=((3.0, 0.0, 0.0), (0.0, 3.0, 0.0), (0.0, 0.0, 15.0))
        ),
        sites=(
            StructureSite(
                atom_uid=new_atom_uid(),
                element="C",
                fractional_coords=(0.1, 0.2, 0.3),
            ),
        ),
        origin=StructureOrigin.IMPORTED,
    )


def _store(root: Path) -> tuple[ProjectStore, Project, StructureSnapshot]:
    project = Project(name="Desktop actions", slug="desktop-actions")
    snapshot = _snapshot()
    store = ProjectStore(root)
    store.save(ProjectBundle(project=project, structure_snapshots=(snapshot,)))
    return store, project, snapshot


def test_action_request_codec_is_operation_specific_and_fail_closed() -> None:
    report = decode_desktop_request(
        (
            '{"protocol_version":"ecatvasp-desktop-ipc-v1",'
            '"request_id":"report-1","operation":"application_report",'
            '"project_root":"/project","report_format":"json"}'
        )
    )
    assert report.operation is DesktopOperation.APPLICATION_REPORT
    assert report.report_format == "json"

    with pytest.raises(DesktopIPCError, match="unknown fields"):
        decode_desktop_request(
            (
                '{"protocol_version":"ecatvasp-desktop-ipc-v1",'
                '"request_id":"report-2","operation":"application_report",'
                '"project_root":"/project","report_format":"json",'
                '"workflow_recipe_id":"forbidden"}'
            )
        )

    with pytest.raises(DesktopIPCError, match="must be a UUID"):
        decode_desktop_request(
            (
                '{"protocol_version":"ecatvasp-desktop-ipc-v1",'
                '"request_id":"workflow-1","operation":"prepare_workflow",'
                '"project_root":"/project","workflow_recipe_id":"recipe",'
                '"workflow_recipe_version":"1",'
                '"root_structure_snapshot_id":"not-a-uuid"}'
            )
        )

    with pytest.raises(DesktopIPCError, match="unsupported desktop operation"):
        decode_desktop_request(
            (
                '{"protocol_version":"ecatvasp-desktop-ipc-v1",'
                '"request_id":"promotion-1","operation":"promote_vasp_structure",'
                '"project_root":"/project"}'
            )
        )


def test_application_report_receipt_matches_application_service(tmp_path: Path) -> None:
    store, project, _snapshot_value = _store(tmp_path)
    direct = ProjectApplicationService(store).report(format="json")
    request = DesktopRequest(
        request_id="report-1",
        operation=DesktopOperation.APPLICATION_REPORT,
        project_root=str(tmp_path),
        report_format="json",
    )
    response = DesktopBackend().handle(request)

    assert response.ok is True
    assert response.payload is not None
    assert response.payload["project_id"] == str(project.id)
    assert response.payload["report_format"] == "json"
    assert response.payload["report_contract_version"] == direct.report.contract_version
    assert response.payload["report_hash"] == direct.report.report_hash
    assert response.payload["content"] == direct.content
    assert len(str(response.payload["content_sha256"])) == 64
    assert store.open().workflow_plans == ()


def test_prepare_workflow_round_trip_persists_and_reuses_exact_plan(tmp_path: Path) -> None:
    store, project, snapshot = _store(tmp_path)
    backend = DesktopBackend()
    request = DesktopRequest(
        request_id="workflow-1",
        operation=DesktopOperation.PREPARE_WORKFLOW,
        project_root=str(tmp_path),
        workflow_recipe_id=WORKFLOW_RECIPE_SLAB_SCIENTIFIC_PREPARATION,
        workflow_recipe_version="1",
        root_structure_snapshot_id=str(snapshot.id),
    )
    first = backend.handle(request)
    second = backend.handle(
        DesktopRequest(
            request_id="workflow-2",
            operation=DesktopOperation.PREPARE_WORKFLOW,
            project_root=str(tmp_path),
            workflow_recipe_id=WORKFLOW_RECIPE_SLAB_SCIENTIFIC_PREPARATION,
            workflow_recipe_version="1",
            root_structure_snapshot_id=str(snapshot.id),
        )
    )
    reopened = store.open()

    assert first.ok is True and first.payload is not None
    assert second.ok is True and second.payload is not None
    assert first.payload["project_id"] == str(project.id)
    assert first.payload["workflow_plan_id"] == second.payload["workflow_plan_id"]
    assert first.payload["plan_hash"] == second.payload["plan_hash"]
    assert first.payload["planning_hash"] == second.payload["planning_hash"]
    assert first.payload["reused"] is False
    assert second.payload["reused"] is True
    assert len(reopened.workflow_plans) == 1
    assert str(reopened.workflow_plans[0].id) == first.payload["workflow_plan_id"]
    assert reopened.workflow_plans[0].plan_hash == first.payload["plan_hash"]


def test_prepare_workflow_foreign_or_unknown_inputs_fail_without_mutation(tmp_path: Path) -> None:
    store, _project, snapshot = _store(tmp_path)
    backend = DesktopBackend()
    baseline = store.open()

    foreign = backend.handle(
        DesktopRequest(
            request_id="workflow-foreign",
            operation=DesktopOperation.PREPARE_WORKFLOW,
            project_root=str(tmp_path),
            workflow_recipe_id=WORKFLOW_RECIPE_SLAB_SCIENTIFIC_PREPARATION,
            workflow_recipe_version="1",
            root_structure_snapshot_id=str(new_structure_snapshot_id()),
        )
    )
    unknown_recipe = backend.handle(
        DesktopRequest(
            request_id="workflow-recipe",
            operation=DesktopOperation.PREPARE_WORKFLOW,
            project_root=str(tmp_path),
            workflow_recipe_id="ECatVASP.Workflow.Unknown",
            workflow_recipe_version="1",
            root_structure_snapshot_id=str(snapshot.id),
        )
    )

    assert foreign.ok is False and foreign.error is not None
    assert foreign.error.code == "application_rejected"
    assert "StructureSnapshot" in foreign.error.message
    assert unknown_recipe.ok is False and unknown_recipe.error is not None
    assert unknown_recipe.error.code == "application_rejected"
    assert "unknown workflow recipe" in unknown_recipe.error.message
    assert store.open() == baseline


def test_block6_keeps_version_schema_and_no_generic_mutation_contract() -> None:
    assert DESKTOP_IPC_CONTRACT_VERSION == "ecatvasp-desktop-ipc-v1"
    assert SCHEMA_VERSION == 3
    values = {operation.value for operation in DesktopOperation}
    assert "application_report" in values
    assert "prepare_workflow" in values
    assert "promote_vasp_structure" not in values
    assert "analyze_vasp_result" not in values
    assert "run_workflow" not in values
    assert "prepare_workflow_step" not in values

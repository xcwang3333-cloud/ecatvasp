from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from ecatvasp import __version__
from ecatvasp.api import open_project
from ecatvasp.desktop import DESKTOP_IPC_CONTRACT_VERSION, DesktopOperation
from ecatvasp.domain import (
    Analysis,
    AnalysisStatus,
    AnalysisType,
    Lattice,
    Project,
    StructureSite,
    StructureSnapshot,
    new_atom_uid,
)
from ecatvasp.provenance import (
    DependencyKind,
    DependencyRecord,
    FreshnessState,
    ProvenanceRecord,
    scientific_hash,
)
from ecatvasp.reporting import ScientificReportError
from ecatvasp.schema.version import SCHEMA_VERSION
from ecatvasp.storage import ProjectBundle, ProjectStore
from ecatvasp.visualization import build_structure_presentation
from ecatvasp.workflow import WORKFLOW_RECIPE_SLAB_SCIENTIFIC_PREPARATION


def _snapshot(label: str) -> StructureSnapshot:
    return StructureSnapshot(
        label=label,
        lattice=Lattice(
            vectors=((3.0, 0.0, 0.0), (0.0, 3.0, 0.0), (0.0, 0.0, 15.0))
        ),
        sites=(
            StructureSite(
                atom_uid=new_atom_uid(),
                element="C",
                fractional_coords=(0.0, 0.0, 0.5),
            ),
        ),
    )


def _project_a(root: Path) -> tuple[ProjectStore, Project, StructureSnapshot, Analysis]:
    project = Project(name="v1.0 final project A", slug="v10-final-project-a")
    snapshot = _snapshot("project A root")
    analysis = Analysis(
        project_id=project.id,
        analysis_type=AnalysisType.GEOMETRY,
        input_artifact_ids=(),
        status=AnalysisStatus.COMPLETED,
        tool="ecatvasp.geometry",
        tool_version=__version__,
    )
    provenance = ProvenanceRecord(
        subject_id=analysis.id,
        tool="ecatvasp.geometry",
        tool_version=__version__,
        parameters_hash="a" * 64,
    )
    dependency = DependencyRecord(
        upstream_id=snapshot.id,
        downstream_id=analysis.id,
        kind=DependencyKind.SCIENTIFIC,
        role="input_structure",
        recorded_hash=scientific_hash(snapshot),
    )
    store = ProjectStore(root)
    store.save(
        ProjectBundle(
            project=project,
            structure_snapshots=(snapshot,),
            analyses=(analysis,),
            provenance_records=(provenance,),
            dependency_records=(dependency,),
        )
    )
    return store, project, snapshot, analysis


def _project_b(root: Path) -> tuple[ProjectStore, Project, StructureSnapshot]:
    project = Project(name="v1.0 final project B", slug="v10-final-project-b")
    snapshot = _snapshot("project B root")
    store = ProjectStore(root)
    store.save(ProjectBundle(project=project, structure_snapshots=(snapshot,)))
    return store, project, snapshot


def _request(
    request_id: str,
    operation: str,
    *,
    project_root: Path | None = None,
    **fields: object,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "protocol_version": DESKTOP_IPC_CONTRACT_VERSION,
        "request_id": request_id,
        "operation": operation,
    }
    if project_root is not None:
        payload["project_root"] = str(project_root)
    payload.update(fields)
    return payload


def _run_sidecar(requests: list[dict[str, object]]) -> list[dict[str, Any]]:
    encoded = "".join(
        json.dumps(request, separators=(",", ":")) + "\n" for request in requests
    )
    completed = subprocess.run(
        [sys.executable, "-m", "ecatvasp.desktop"],
        input=encoded,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    lines = [line for line in completed.stdout.splitlines() if line]
    assert len(lines) == len(requests)
    responses = [json.loads(line) for line in lines]
    assert all(isinstance(response, dict) for response in responses)
    for request, response in zip(requests, responses, strict=True):
        assert response["protocol_version"] == DESKTOP_IPC_CONTRACT_VERSION
        assert response["request_id"] == request["request_id"]
        assert response["operation"] == request["operation"]
    return responses


def _payload(response: dict[str, Any]) -> dict[str, Any]:
    assert response["ok"] is True, response
    payload = response["payload"]
    assert isinstance(payload, dict)
    return payload


def _handoff_report(response: dict[str, Any]) -> dict[str, Any]:
    handoff = _payload(response)["handoff"]
    assert isinstance(handoff, dict)
    report = handoff["report"]
    assert isinstance(report, dict)
    return report


def test_v10_production_desktop_cross_layer_e2e_and_scientific_drift(
    tmp_path: Path,
) -> None:
    root_a = tmp_path / "project-a"
    root_b = tmp_path / "project-b"
    store_a, project_a, snapshot_a, analysis_a = _project_a(root_a)
    _store_b, project_b, _snapshot_b = _project_b(root_b)
    initial_source_hash = scientific_hash(snapshot_a)

    first_requests = [
        _request("health-1", "health"),
        _request("open-a-1", "open_project", project_root=root_a),
        _request("status-a-1", "status", project_root=root_a),
        _request("handoff-a-1", "frontend_handoff", project_root=root_a),
        _request(
            "report-a-1",
            "application_report",
            project_root=root_a,
            report_format="json",
        ),
        _request(
            "workflow-a-1",
            "prepare_workflow",
            project_root=root_a,
            workflow_recipe_id=WORKFLOW_RECIPE_SLAB_SCIENTIFIC_PREPARATION,
            workflow_recipe_version="1",
            root_structure_snapshot_id=str(snapshot_a.id),
        ),
        _request("open-b-1", "open_project", project_root=root_b),
        _request("status-b-1", "status", project_root=root_b),
        _request("open-a-2", "open_project", project_root=root_a),
    ]
    first = _run_sidecar(first_requests)

    health = _payload(first[0])
    assert health["backend_version"] == __version__ == "1.0.0.dev0"
    assert health["stateless_project_requests"] is True
    assert health["operations"] == [operation.value for operation in DesktopOperation]

    opened_a = _payload(first[1])
    assert opened_a["project_id"] == str(project_a.id)
    assert opened_a["schema_version"] == SCHEMA_VERSION == 3

    status_a = _payload(first[2])
    assert status_a["project_id"] == str(project_a.id)
    for namespace in (
        "calculations",
        "analyses",
        "execution_attempts",
        "scheduler_jobs",
        "freshness",
    ):
        assert isinstance(status_a[namespace], list)
    assert ["stale", 1] not in status_a["freshness"]
    assert status_a["attention_rows"] == 0

    report_before = _handoff_report(first[3])
    assert isinstance(report_before["workflow_readiness"], list)
    presentations_before = report_before["presentations"]
    assert isinstance(presentations_before, list) and len(presentations_before) == 1
    structure_before = presentations_before[0]["payload"]
    assert structure_before["structure_snapshot_id"] == str(snapshot_a.id)
    assert structure_before["source_scientific_hash"] == initial_source_hash
    atom_map = structure_before["matterviz"]["atom_index_map"]
    assert atom_map["atom_uid_by_viewer_index"] == [str(snapshot_a.sites[0].atom_uid)]
    assert atom_map["viewer_index_by_atom_uid"] == {
        str(snapshot_a.sites[0].atom_uid): 0
    }

    report_receipt = _payload(first[4])
    report_content = report_receipt["content"]
    assert isinstance(report_content, str)
    assert report_receipt["content_sha256"] == hashlib.sha256(
        report_content.encode("utf-8")
    ).hexdigest()
    assert json.loads(report_content)["project"]["project_id"] == str(project_a.id)

    workflow_receipt = _payload(first[5])
    assert workflow_receipt["project_id"] == str(project_a.id)
    assert workflow_receipt["workflow_recipe_id"] == (
        WORKFLOW_RECIPE_SLAB_SCIENTIFIC_PREPARATION
    )
    assert workflow_receipt["reused"] is False
    persisted_after_action = store_a.open()
    assert len(persisted_after_action.workflow_plans) == 1
    assert str(persisted_after_action.workflow_plans[0].id) == (
        workflow_receipt["workflow_plan_id"]
    )

    opened_b = _payload(first[6])
    status_b = _payload(first[7])
    reopened_a = _payload(first[8])
    assert opened_b["project_id"] == str(project_b.id)
    assert status_b["project_id"] == str(project_b.id)
    assert reopened_a["project_id"] == str(project_a.id)
    assert reopened_a["project_id"] != opened_b["project_id"]

    old_presentation = build_structure_presentation(snapshot_a)
    current_bundle = store_a.open()
    changed_site = replace(
        snapshot_a.sites[0],
        fractional_coords=(0.125, 0.0, 0.5),
    )
    changed_snapshot = replace(snapshot_a, sites=(changed_site,))
    assert changed_snapshot.id == snapshot_a.id
    changed_source_hash = scientific_hash(changed_snapshot)
    assert changed_source_hash != initial_source_hash
    store_a.save(replace(current_bundle, structure_snapshots=(changed_snapshot,)))

    second_requests = [
        _request("health-2", "health"),
        _request("status-a-2", "status", project_root=root_a),
        _request("handoff-a-2", "frontend_handoff", project_root=root_a),
        _request(
            "report-a-2",
            "application_report",
            project_root=root_a,
            report_format="json",
        ),
    ]
    second = _run_sidecar(second_requests)

    assert _payload(second[0])["backend_version"] == __version__
    status_after = _payload(second[1])
    assert status_after["project_id"] == str(project_a.id)
    assert ["stale", 1] in status_after["freshness"]
    assert status_after["attention_rows"] == 1

    report_after = _handoff_report(second[2])
    presentations_after = report_after["presentations"]
    assert isinstance(presentations_after, list) and len(presentations_after) == 1
    structure_after = presentations_after[0]["payload"]
    assert structure_after["structure_snapshot_id"] == str(snapshot_a.id)
    assert structure_after["source_scientific_hash"] == changed_source_hash
    assert structure_after["source_scientific_hash"] != initial_source_hash

    analysis_row = next(
        row
        for row in report_after["inventory"]["rows"]
        if row["entity_id"] == str(analysis_a.id)
    )
    assert analysis_row["freshness"]["state"] == FreshnessState.STALE.value
    assert [reason["code"] for reason in analysis_row["freshness"]["reasons"]] == [
        "scientific_hash_changed"
    ]
    assert "stale_scientific_dependency" in analysis_row["attention_codes"]

    restarted_report = _payload(second[3])
    restarted_content = restarted_report["content"]
    assert isinstance(restarted_content, str)
    restarted_json = json.loads(restarted_content)
    restarted_analysis_row = next(
        row
        for row in restarted_json["inventory"]["rows"]
        if row["entity_id"] == str(analysis_a.id)
    )
    assert restarted_analysis_row["freshness"]["state"] == "stale"

    facade = open_project(root_a)
    after_inspection = facade.inspect()
    assert after_inspection.inventory.row(analysis_a.id).freshness.state is FreshnessState.STALE
    with pytest.raises(ScientificReportError, match="source hash does not match"):
        facade.frontend_handoff(
            inventory=after_inspection.inventory,
            presentations=(old_presentation,),
        )

    current_presentation = build_structure_presentation(changed_snapshot)
    current_handoff = facade.frontend_handoff(
        inventory=after_inspection.inventory,
        presentations=(current_presentation,),
    )
    current_analysis_row = next(
        row
        for row in current_handoff.report.to_dict()["inventory"]["rows"]
        if row["entity_id"] == str(analysis_a.id)
    )
    assert current_analysis_row["freshness"]["state"] == "stale"
    assert current_presentation.source_scientific_hash == changed_source_hash
    assert SCHEMA_VERSION == 3

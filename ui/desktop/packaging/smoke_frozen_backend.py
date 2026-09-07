"""Exercise the frozen Windows backend through the final v1.0 stdio E2E contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from ecatvasp import __version__
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
    ProvenanceRecord,
    scientific_hash,
)
from ecatvasp.schema.version import SCHEMA_VERSION
from ecatvasp.storage import ProjectBundle, ProjectStore
from ecatvasp.workflow import WORKFLOW_RECIPE_SLAB_SCIENTIFIC_PREPARATION


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("sidecar", type=Path)
    args = parser.parse_args()
    sidecar = args.sidecar.resolve()
    if not sidecar.is_file():
        raise RuntimeError(f"frozen backend is missing: {sidecar}")

    with TemporaryDirectory(prefix="ecatvasp-packaging-e2e-") as directory:
        root = Path(directory)
        store_a, project_a, snapshot_a, analysis_a = _project_a(root / "project-a")
        _store_b, project_b = _project_b(root / "project-b")
        source_hash_before = scientific_hash(snapshot_a)

        first = _start(sidecar)
        try:
            health = _exchange(first, _request("health-1", "health"))
            _assert_health(health)
            opened_a = _exchange(
                first,
                _request("open-a-1", "open_project", store_a.root),
            )
            _assert_project(opened_a, project_id=str(project_a.id))
            status_a = _exchange(
                first,
                _request("status-a-1", "status", store_a.root),
            )
            _assert_status(status_a, project_id=str(project_a.id), stale=False)
            handoff_a = _exchange(
                first,
                _request("handoff-a-1", "frontend_handoff", store_a.root),
            )
            _assert_handoff(
                handoff_a,
                project_id=str(project_a.id),
                snapshot_id=str(snapshot_a.id),
                source_hash=source_hash_before,
                analysis_id=str(analysis_a.id),
                stale=False,
            )
            report_a = _exchange(
                first,
                _request(
                    "report-a-1",
                    "application_report",
                    store_a.root,
                    report_format="json",
                ),
            )
            _assert_report(report_a, project_id=str(project_a.id), stale_analysis_id=None)
            workflow_a = _exchange(
                first,
                _request(
                    "workflow-a-1",
                    "prepare_workflow",
                    store_a.root,
                    workflow_recipe_id=WORKFLOW_RECIPE_SLAB_SCIENTIFIC_PREPARATION,
                    workflow_recipe_version="1",
                    root_structure_snapshot_id=str(snapshot_a.id),
                ),
            )
            _assert_workflow(workflow_a, project_id=str(project_a.id))

            opened_b = _exchange(
                first,
                _request("open-b-1", "open_project", root / "project-b"),
            )
            _assert_project(opened_b, project_id=str(project_b.id))
            status_b = _exchange(
                first,
                _request("status-b-1", "status", root / "project-b"),
            )
            _assert_status(status_b, project_id=str(project_b.id), stale=False)
            reopened_a = _exchange(
                first,
                _request("open-a-2", "open_project", store_a.root),
            )
            _assert_project(reopened_a, project_id=str(project_a.id))
        finally:
            _finish(first)

        persisted = store_a.open()
        if len(persisted.workflow_plans) != 1:
            raise RuntimeError("typed workflow preparation did not persist one workflow plan")
        changed_site = replace(
            snapshot_a.sites[0],
            fractional_coords=(0.125, 0.0, 0.5),
        )
        changed_snapshot = replace(snapshot_a, sites=(changed_site,))
        if changed_snapshot.id != snapshot_a.id:
            raise RuntimeError("scientific drift fixture changed permanent snapshot identity")
        source_hash_after = scientific_hash(changed_snapshot)
        if source_hash_after == source_hash_before:
            raise RuntimeError("scientific drift fixture did not change the source hash")
        store_a.save(replace(persisted, structure_snapshots=(changed_snapshot,)))

        restarted = _start(sidecar)
        try:
            _assert_health(_exchange(restarted, _request("health-2", "health")))
            status_after = _exchange(
                restarted,
                _request("status-a-2", "status", store_a.root),
            )
            _assert_status(status_after, project_id=str(project_a.id), stale=True)
            handoff_after = _exchange(
                restarted,
                _request("handoff-a-2", "frontend_handoff", store_a.root),
            )
            _assert_handoff(
                handoff_after,
                project_id=str(project_a.id),
                snapshot_id=str(snapshot_a.id),
                source_hash=source_hash_after,
                analysis_id=str(analysis_a.id),
                stale=True,
            )
            report_after = _exchange(
                restarted,
                _request(
                    "report-a-2",
                    "application_report",
                    store_a.root,
                    report_format="json",
                ),
            )
            _assert_report(
                report_after,
                project_id=str(project_a.id),
                stale_analysis_id=str(analysis_a.id),
            )
        finally:
            _finish(restarted)
    return 0


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
    project = Project(name="Packaged project A", slug="packaged-project-a")
    snapshot = _snapshot("packaged A root")
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


def _project_b(root: Path) -> tuple[ProjectStore, Project]:
    project = Project(name="Packaged project B", slug="packaged-project-b")
    store = ProjectStore(root)
    store.save(ProjectBundle(project=project, structure_snapshots=(_snapshot("B root"),)))
    return store, project


def _start(sidecar: Path) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [str(sidecar)],
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
        raise RuntimeError(f"frozen backend exited with {return_code}: {stderr.strip()}")
    if stderr.strip():
        raise RuntimeError(f"frozen backend wrote unexpected stderr: {stderr.strip()}")


def _request(
    request_id: str,
    operation: str,
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


def _exchange(
    process: subprocess.Popen[str],
    request: dict[str, object],
) -> dict[str, Any]:
    if process.stdin is None or process.stdout is None:
        raise RuntimeError("frozen backend stdio pipes are unavailable")
    process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
    process.stdin.flush()
    line = process.stdout.readline()
    if not line:
        raise RuntimeError("frozen backend closed stdout before responding")
    response = json.loads(line)
    if not isinstance(response, dict):
        raise RuntimeError("frozen backend response is not a JSON object")
    if response.get("request_id") != request["request_id"]:
        raise RuntimeError("frozen backend response request_id mismatch")
    if response.get("operation") != request["operation"]:
        raise RuntimeError("frozen backend response operation mismatch")
    return response


def _payload(response: dict[str, Any]) -> dict[str, Any]:
    if response.get("ok") is not True:
        raise RuntimeError(f"frozen backend operation failed: {response!r}")
    payload = response.get("payload")
    if not isinstance(payload, dict):
        raise RuntimeError("frozen backend response payload is invalid")
    return payload


def _assert_health(response: dict[str, Any]) -> None:
    payload = _payload(response)
    if response.get("protocol_version") != DESKTOP_IPC_CONTRACT_VERSION:
        raise RuntimeError("frozen backend IPC version mismatch")
    if payload.get("backend_version") != __version__:
        raise RuntimeError("frozen backend package version mismatch")
    if payload.get("stateless_project_requests") is not True:
        raise RuntimeError("frozen backend must preserve stateless project requests")
    expected_operations = [operation.value for operation in DesktopOperation]
    if payload.get("operations") != expected_operations:
        raise RuntimeError("frozen backend operation set drifted")


def _assert_project(response: dict[str, Any], *, project_id: str) -> None:
    payload = _payload(response)
    if payload.get("project_id") != project_id:
        raise RuntimeError("frozen backend opened the wrong Project")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise RuntimeError("frozen backend ProjectStore schema mismatch")


def _assert_status(
    response: dict[str, Any],
    *,
    project_id: str,
    stale: bool,
) -> None:
    payload = _payload(response)
    if payload.get("project_id") != project_id:
        raise RuntimeError("frozen backend status belongs to the wrong Project")
    for namespace in (
        "calculations",
        "analyses",
        "execution_attempts",
        "scheduler_jobs",
        "freshness",
    ):
        if not isinstance(payload.get(namespace), list):
            raise RuntimeError(f"frozen backend status namespace is missing: {namespace}")
    freshness = payload["freshness"]
    has_stale = ["stale", 1] in freshness
    if has_stale != stale:
        raise RuntimeError("frozen backend freshness did not match expected scientific drift")
    expected_attention = 1 if stale else 0
    if payload.get("attention_rows") != expected_attention:
        raise RuntimeError("frozen backend attention count did not match freshness")


def _assert_handoff(
    response: dict[str, Any],
    *,
    project_id: str,
    snapshot_id: str,
    source_hash: str,
    analysis_id: str,
    stale: bool,
) -> None:
    payload = _payload(response)
    handoff = payload.get("handoff")
    if not isinstance(handoff, dict):
        raise RuntimeError("frozen backend handoff is invalid")
    report = handoff.get("report")
    if not isinstance(report, dict):
        raise RuntimeError("frozen backend handoff report is invalid")
    project = report.get("project")
    if not isinstance(project, dict) or project.get("project_id") != project_id:
        raise RuntimeError("frozen backend handoff belongs to the wrong Project")
    if not isinstance(report.get("workflow_readiness"), list):
        raise RuntimeError("workflow readiness is not a distinct handoff namespace")
    presentations = report.get("presentations")
    if not isinstance(presentations, list) or len(presentations) != 1:
        raise RuntimeError("frozen backend handoff structure presentation is missing")
    structure = presentations[0].get("payload")
    if not isinstance(structure, dict):
        raise RuntimeError("frozen backend structure presentation payload is invalid")
    if structure.get("structure_snapshot_id") != snapshot_id:
        raise RuntimeError("frozen backend structure presentation identity drifted")
    if structure.get("source_scientific_hash") != source_hash:
        raise RuntimeError("frozen backend structure presentation source hash is not current")

    inventory = report.get("inventory")
    if not isinstance(inventory, dict) or not isinstance(inventory.get("rows"), list):
        raise RuntimeError("frozen backend scientific inventory is missing")
    analysis_row = next(
        (row for row in inventory["rows"] if row.get("entity_id") == analysis_id),
        None,
    )
    if not isinstance(analysis_row, dict):
        raise RuntimeError("frozen backend Analysis inventory row is missing")
    freshness = analysis_row.get("freshness")
    if not isinstance(freshness, dict):
        raise RuntimeError("frozen backend Analysis freshness is missing")
    expected_state = "stale" if stale else "fresh"
    if freshness.get("state") != expected_state:
        raise RuntimeError("frozen backend Analysis freshness state is incorrect")
    if stale:
        reasons = freshness.get("reasons")
        if not isinstance(reasons, list) or [item.get("code") for item in reasons] != [
            "scientific_hash_changed"
        ]:
            raise RuntimeError("frozen backend stale reason was not preserved")


def _assert_report(
    response: dict[str, Any],
    *,
    project_id: str,
    stale_analysis_id: str | None,
) -> None:
    payload = _payload(response)
    content = payload.get("content")
    digest = payload.get("content_sha256")
    if not isinstance(content, str) or not isinstance(digest, str):
        raise RuntimeError("frozen backend report receipt is incomplete")
    if digest != hashlib.sha256(content.encode("utf-8")).hexdigest():
        raise RuntimeError("frozen backend report content SHA-256 mismatch")
    report = json.loads(content)
    if report["project"]["project_id"] != project_id:
        raise RuntimeError("frozen backend report belongs to the wrong Project")
    if stale_analysis_id is not None:
        row = next(
            item
            for item in report["inventory"]["rows"]
            if item["entity_id"] == stale_analysis_id
        )
        if row["freshness"]["state"] != "stale":
            raise RuntimeError("frozen backend report hid downstream stale state")


def _assert_workflow(response: dict[str, Any], *, project_id: str) -> None:
    payload = _payload(response)
    if payload.get("project_id") != project_id:
        raise RuntimeError("frozen backend workflow receipt belongs to the wrong Project")
    if payload.get("workflow_recipe_id") != WORKFLOW_RECIPE_SLAB_SCIENTIFIC_PREPARATION:
        raise RuntimeError("frozen backend prepared the wrong workflow recipe")
    if payload.get("reused") is not False:
        raise RuntimeError("first frozen backend workflow preparation unexpectedly reused a plan")
    if not isinstance(payload.get("plan_hash"), str):
        raise RuntimeError("frozen backend workflow receipt is missing plan_hash")


if __name__ == "__main__":  # pragma: no cover - packaging entry point
    raise SystemExit(main())

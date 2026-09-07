from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from uuid import UUID

from ecatvasp.desktop import DesktopBackend, DesktopOperation, DesktopRequest
from ecatvasp.domain import AtomUid, Lattice, Project, StructureSite, StructureSnapshot
from ecatvasp.provenance import scientific_hash
from ecatvasp.schema.version import SCHEMA_VERSION
from ecatvasp.storage import ProjectBundle, ProjectStore
from ecatvasp.visualization import PRESENTATION_CONTRACT_VERSION
from ecatvasp.visualization.matterviz import MATTERVIZ_CONTRACT_VERSION, MATTERVIZ_TARGET_VERSION


def _snapshot() -> StructureSnapshot:
    return StructureSnapshot(
        label="desktop structure",
        lattice=Lattice(
            vectors=((3.0, 0.0, 0.0), (0.0, 3.0, 0.0), (0.0, 0.0, 15.0))
        ),
        sites=(
            StructureSite(
                atom_uid=AtomUid(UUID("018f3000-0000-7000-8000-000000000001")),
                element="C",
                fractional_coords=(0.0, 0.0, 0.5),
            ),
            StructureSite(
                atom_uid=AtomUid(UUID("018f3000-0000-7000-8000-000000000002")),
                element="O",
                fractional_coords=(0.5, 0.5, 0.55),
            ),
        ),
    )


def _handoff(root: Path) -> dict[str, object]:
    response = DesktopBackend().handle(
        DesktopRequest(
            request_id="workspace-handoff",
            operation=DesktopOperation.FRONTEND_HANDOFF,
            project_root=str(root),
        )
    )
    assert response.ok is True
    assert response.payload is not None
    handoff = response.payload["handoff"]
    assert isinstance(handoff, dict)
    return handoff


def test_desktop_handoff_builds_exact_current_structure_presentation(tmp_path: Path) -> None:
    root = tmp_path / "project"
    project = Project(name="Desktop workspace", slug="desktop-workspace")
    snapshot = _snapshot()
    ProjectStore(root).save(
        ProjectBundle(project=project, structure_snapshots=(snapshot,))
    )

    handoff = _handoff(root)
    report = handoff["report"]
    assert isinstance(report, dict)
    assert report["workflow_readiness"] == []
    presentations = report["presentations"]
    assert isinstance(presentations, list)
    assert len(presentations) == 1
    record = presentations[0]
    assert isinstance(record, dict)
    assert record["kind"] == "structure"
    payload = record["payload"]
    assert isinstance(payload, dict)

    assert payload["contract_version"] == PRESENTATION_CONTRACT_VERSION
    assert payload["structure_snapshot_id"] == str(snapshot.id)
    assert payload["source_scientific_hash"] == scientific_hash(snapshot)
    matterviz = payload["matterviz"]
    assert isinstance(matterviz, dict)
    assert matterviz["contract_version"] == MATTERVIZ_CONTRACT_VERSION
    assert matterviz["target_version"] == MATTERVIZ_TARGET_VERSION
    atom_map = matterviz["atom_index_map"]
    assert isinstance(atom_map, dict)
    assert atom_map["atom_uid_by_viewer_index"] == [
        str(snapshot.sites[0].atom_uid),
        str(snapshot.sites[1].atom_uid),
    ]
    assert atom_map["viewer_index_by_atom_uid"] == {
        str(snapshot.sites[0].atom_uid): 0,
        str(snapshot.sites[1].atom_uid): 1,
    }


def test_desktop_handoff_refreshes_same_snapshot_uuid_after_scientific_change(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    project = Project(name="Desktop refresh", slug="desktop-refresh")
    snapshot = _snapshot()
    store = ProjectStore(root)
    store.save(ProjectBundle(project=project, structure_snapshots=(snapshot,)))

    first = _handoff(root)
    first_report = first["report"]
    assert isinstance(first_report, dict)
    first_presentations = first_report["presentations"]
    assert isinstance(first_presentations, list)
    first_record = first_presentations[0]
    assert isinstance(first_record, dict)
    first_payload = first_record["payload"]
    assert isinstance(first_payload, dict)
    first_hash = first_payload["source_scientific_hash"]

    changed_site = replace(snapshot.sites[0], fractional_coords=(0.125, 0.0, 0.5))
    changed = replace(snapshot, sites=(changed_site, snapshot.sites[1]))
    assert changed.id == snapshot.id
    assert scientific_hash(changed) != scientific_hash(snapshot)
    store.save(ProjectBundle(project=project, structure_snapshots=(changed,)))

    second = _handoff(root)
    second_report = second["report"]
    assert isinstance(second_report, dict)
    second_presentations = second_report["presentations"]
    assert isinstance(second_presentations, list)
    second_record = second_presentations[0]
    assert isinstance(second_record, dict)
    second_payload = second_record["payload"]
    assert isinstance(second_payload, dict)

    assert second_payload["structure_snapshot_id"] == str(snapshot.id)
    assert second_payload["source_scientific_hash"] == scientific_hash(changed)
    assert second_payload["source_scientific_hash"] != first_hash
    assert second["handoff_hash"] != first["handoff_hash"]


def test_block5_desktop_workspace_keeps_schema_v3() -> None:
    assert SCHEMA_VERSION == 3

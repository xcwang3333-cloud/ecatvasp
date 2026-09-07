from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ecatvasp.api import open_project
from ecatvasp.domain import (
    Analysis,
    AnalysisStatus,
    AnalysisType,
    AtomUid,
    Lattice,
    Project,
    StructureSite,
    StructureSnapshot,
)
from ecatvasp.frontend import (
    FRONTEND_HANDOFF_CONTRACT_VERSION,
    build_frontend_handoff,
    render_frontend_handoff_json,
)
from ecatvasp.provenance import DependencyKind, DependencyRecord, FreshnessState, scientific_hash
from ecatvasp.reporting import ScientificReportError
from ecatvasp.schema.version import SCHEMA_VERSION
from ecatvasp.storage import ProjectBundle, ProjectStore
from ecatvasp.visualization import PRESENTATION_CONTRACT_VERSION, build_structure_presentation
from ecatvasp.visualization.matterviz import MATTERVIZ_CONTRACT_VERSION, MATTERVIZ_TARGET_VERSION
from ecatvasp.workspace import build_scientific_inventory


def _project() -> Project:
    return Project(
        name="Frontend handoff acceptance",
        slug="frontend-handoff-acceptance",
        schema_version=SCHEMA_VERSION,
        created_at=datetime(2026, 9, 7, 0, 55, tzinfo=UTC),
    )


def _snapshot() -> StructureSnapshot:
    return StructureSnapshot(
        label="frontend slab",
        lattice=Lattice(
            vectors=((3.0, 0.0, 0.0), (0.0, 3.0, 0.0), (0.0, 0.0, 15.0))
        ),
        sites=(
            StructureSite(
                atom_uid=AtomUid("018f2000-0000-7000-8000-000000000001"),
                element="C",
                fractional_coords=(0.0, 0.0, 0.5),
            ),
        ),
    )


def test_frontend_handoff_is_deterministic_and_versioned() -> None:
    bundle = ProjectBundle(project=_project())

    first = build_frontend_handoff(bundle)
    second = build_frontend_handoff(bundle)
    first_json = render_frontend_handoff_json(first)
    second_json = render_frontend_handoff_json(second)
    payload = json.loads(first_json)

    assert first == second
    assert first_json == second_json
    assert first.handoff_hash == second.handoff_hash
    assert payload["contract_version"] == FRONTEND_HANDOFF_CONTRACT_VERSION
    assert payload["matterviz_target_version"] == MATTERVIZ_TARGET_VERSION
    assert payload["report"]["project"]["schema_version"] == SCHEMA_VERSION
    assert payload["capabilities"] == [
        {
            "contract_version": "ecatvasp-scientific-report-v1",
            "name": "workspace-report",
        },
        {
            "contract_version": PRESENTATION_CONTRACT_VERSION,
            "name": "scientific-presentation",
        },
        {"contract_version": MATTERVIZ_CONTRACT_VERSION, "name": "matterviz"},
    ]


def test_structure_presentation_crosses_handoff_without_identity_loss() -> None:
    project = _project()
    snapshot = _snapshot()
    bundle = ProjectBundle(project=project, structure_snapshots=(snapshot,))
    presentation = build_structure_presentation(snapshot)

    handoff = build_frontend_handoff(bundle, presentations=(presentation,))
    payload = handoff.to_dict()
    presentation_payload = payload["report"]["presentations"][0]

    assert presentation_payload["contract_version"] == PRESENTATION_CONTRACT_VERSION
    assert presentation_payload["structure_snapshot_id"] == str(snapshot.id)
    assert presentation_payload["source_scientific_hash"] == scientific_hash(snapshot)
    assert presentation_payload["matterviz"]["contract_version"] == MATTERVIZ_CONTRACT_VERSION
    assert presentation_payload["matterviz"]["target_version"] == MATTERVIZ_TARGET_VERSION


def test_handoff_preserves_authoritative_stale_inventory_visibility() -> None:
    project = _project()
    snapshot = _snapshot()
    analysis = Analysis(
        project_id=project.id,
        analysis_type=AnalysisType.GEOMETRY,
        input_artifact_ids=(),
        status=AnalysisStatus.COMPLETED,
    )
    dependency = DependencyRecord(
        upstream_id=snapshot.id,
        downstream_id=analysis.id,
        kind=DependencyKind.SCIENTIFIC,
        role="input_structure",
        recorded_hash=scientific_hash(snapshot),
    )
    bundle = ProjectBundle(
        project=project,
        structure_snapshots=(snapshot,),
        analyses=(analysis,),
        dependency_records=(dependency,),
    )
    inventory = build_scientific_inventory(
        bundle,
        observed_hashes={snapshot.id: "f" * 64},
    )

    handoff = build_frontend_handoff(bundle, inventory=inventory)
    row = next(
        item
        for item in handoff.to_dict()["report"]["inventory"]["rows"]
        if item["entity_id"] == str(analysis.id)
    )

    assert inventory.row(analysis.id).freshness.state is FreshnessState.STALE
    assert row["freshness"]["state"] == "stale"
    assert row["freshness"]["reasons"][0]["code"] == "scientific_hash_changed"


def test_project_facade_frontend_handoff_reopens_latest_store_state(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path / "project")
    project = _project()
    store.save(ProjectBundle(project=project))
    facade = open_project(store.root)

    first = facade.frontend_handoff()
    store.save(ProjectBundle(project=replace(project, name="Frontend handoff updated")))
    second = facade.frontend_handoff()

    assert first.report.projection.project_name == "Frontend handoff acceptance"
    assert second.report.projection.project_name == "Frontend handoff updated"
    assert first.handoff_hash != second.handoff_hash


def test_foreign_presentation_fails_closed_through_reporting_boundary() -> None:
    project = _project()
    bundle = ProjectBundle(project=project)
    presentation = build_structure_presentation(_snapshot())

    with pytest.raises(ScientificReportError, match="another Project"):
        build_frontend_handoff(bundle, presentations=(presentation,))


def test_block8_does_not_advance_schema() -> None:
    assert SCHEMA_VERSION == 3

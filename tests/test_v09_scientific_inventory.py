from datetime import UTC, datetime
from uuid import UUID

import pytest

from ecatvasp.domain import (
    Analysis,
    AnalysisId,
    AnalysisProducerRef,
    AnalysisStatus,
    AnalysisType,
    Artifact,
    ArtifactType,
    AtomUid,
    Lattice,
    Project,
    ProjectId,
    StructureSite,
    StructureSnapshot,
    StructureSnapshotId,
)
from ecatvasp.provenance import (
    DependencyKind,
    DependencyRecord,
    FreshnessState,
    ProvenanceRecord,
    scientific_hash,
)
from ecatvasp.schema.version import SCHEMA_VERSION
from ecatvasp.storage import ProjectBundle
from ecatvasp.workspace import (
    WorkspaceEntityKind,
    WorkspaceInspectionError,
    WorkspaceStatusDomain,
    build_scientific_inventory,
)

PROJECT_ID = ProjectId(UUID("018f1000-0000-7000-8000-000000000001"))
SNAPSHOT_ID = StructureSnapshotId(UUID("018f1000-0000-7000-8000-000000000002"))
TARGET_ANALYSIS_ID = AnalysisId(UUID("018f1000-0000-7000-8000-000000000003"))
SOURCE_ANALYSIS_ID = AnalysisId(UUID("018f1000-0000-7000-8000-000000000004"))


def _project() -> Project:
    return Project(
        name="Inventory acceptance",
        slug="inventory-acceptance",
        id=PROJECT_ID,
        schema_version=SCHEMA_VERSION,
        created_at=datetime(2026, 9, 7, 6, 30, tzinfo=UTC),
    )


def _snapshot() -> StructureSnapshot:
    return StructureSnapshot(
        id=SNAPSHOT_ID,
        label="accepted slab",
        lattice=Lattice(
            vectors=((3.0, 0.0, 0.0), (0.0, 3.0, 0.0), (0.0, 0.0, 15.0))
        ),
        sites=(
            StructureSite(
                atom_uid=AtomUid(UUID("018f1000-0000-7000-8000-000000000010")),
                element="C",
                fractional_coords=(0.0, 0.0, 0.5),
            ),
        ),
    )


def _source_linked_bundle(
    *,
    dependency_kind: DependencyKind = DependencyKind.SCIENTIFIC,
    analysis_status: AnalysisStatus = AnalysisStatus.BLOCKED,
) -> tuple[ProjectBundle, StructureSnapshot, Analysis, DependencyRecord, ProvenanceRecord]:
    project = _project()
    snapshot = _snapshot()
    analysis = Analysis(
        id=TARGET_ANALYSIS_ID,
        project_id=project.id,
        analysis_type=AnalysisType.GEOMETRY,
        input_artifact_ids=(),
        status=analysis_status,
        tool="ecatvasp.geometry",
        tool_version="0.9",
    )
    provenance = ProvenanceRecord(
        subject_id=analysis.id,
        tool="ecatvasp.geometry",
        tool_version="0.9",
        parameters_hash="a" * 64,
        created_at=datetime(2026, 9, 7, 6, 31, tzinfo=UTC),
    )
    dependency = DependencyRecord(
        upstream_id=snapshot.id,
        downstream_id=analysis.id,
        kind=dependency_kind,
        role="input_structure",
        recorded_hash=scientific_hash(snapshot),
    )
    bundle = ProjectBundle(
        project=project,
        structure_snapshots=(snapshot,),
        analyses=(analysis,),
        provenance_records=(provenance,),
        dependency_records=(dependency,),
    )
    return bundle, snapshot, analysis, dependency, provenance


def test_inventory_exposes_exact_provenance_dependencies_and_separate_status() -> None:
    bundle, snapshot, analysis, dependency, provenance = _source_linked_bundle()

    first = build_scientific_inventory(bundle)
    second = build_scientific_inventory(bundle)
    row = first.row(analysis.id)

    assert first == second
    assert first.project_id == bundle.project.id
    assert row.entity_kind is WorkspaceEntityKind.ANALYSIS
    assert row.display_label == "geometry"
    assert row.status_domain is WorkspaceStatusDomain.ANALYSIS
    assert row.status == "blocked"
    assert row.attention_codes == ("analysis_status_blocked",)
    assert row.current_scientific_hash == scientific_hash(analysis)
    assert row.provenance[0].provenance_id == provenance.id
    assert row.provenance[0].parameters_hash == "a" * 64
    assert row.incoming_dependencies[0].dependency_id == dependency.id
    assert row.incoming_dependencies[0].upstream_id == snapshot.id
    assert row.incoming_dependencies[0].kind is DependencyKind.SCIENTIFIC
    assert row.scientific_ancestor_ids == (snapshot.id,)
    assert row.freshness.state is FreshnessState.FRESH
    assert row.freshness.reasons == ()

    source_row = first.row(snapshot.id)
    assert source_row.entity_kind is WorkspaceEntityKind.STRUCTURE_SNAPSHOT
    assert source_row.outgoing_dependencies[0].downstream_id == analysis.id
    assert source_row.current_scientific_hash == scientific_hash(snapshot)


def test_scientific_hash_drift_is_visible_with_exact_freshness_reason() -> None:
    bundle, snapshot, analysis, dependency, _ = _source_linked_bundle(
        analysis_status=AnalysisStatus.COMPLETED
    )

    inventory = build_scientific_inventory(
        bundle,
        observed_hashes={snapshot.id: "f" * 64},
    )
    row = inventory.row(analysis.id)

    assert row.status == "completed"
    assert row.freshness.state is FreshnessState.STALE
    assert row.attention_codes == ("freshness_stale",)
    assert tuple(reason.code for reason in row.freshness.reasons) == (
        "scientific_hash_changed",
    )
    assert row.freshness.reasons[0].upstream_id == snapshot.id
    assert row.freshness.reasons[0].dependency_id == dependency.id
    assert inventory.row(snapshot.id).current_scientific_hash == "f" * 64


def test_display_dependency_does_not_propagate_scientific_staleness() -> None:
    bundle, snapshot, analysis, _, _ = _source_linked_bundle(
        dependency_kind=DependencyKind.DISPLAY,
        analysis_status=AnalysisStatus.COMPLETED,
    )

    row = build_scientific_inventory(
        bundle,
        observed_hashes={snapshot.id: "f" * 64},
    ).row(analysis.id)

    assert row.freshness.state is FreshnessState.FRESH
    assert row.scientific_ancestor_ids == ()


def test_missing_scientific_artifact_hash_fails_closed_in_freshness_projection() -> None:
    project = _project()
    source_analysis = Analysis(
        id=SOURCE_ANALYSIS_ID,
        project_id=project.id,
        analysis_type=AnalysisType.RESULT_PARSE,
        input_artifact_ids=(),
        status=AnalysisStatus.COMPLETED,
    )
    source_artifact = Artifact(
        artifact_type=ArtifactType.DERIVED_DATASET,
        producer=AnalysisProducerRef(source_analysis.id),
    )
    target_analysis = Analysis(
        id=TARGET_ANALYSIS_ID,
        project_id=project.id,
        analysis_type=AnalysisType.GEOMETRY,
        input_artifact_ids=(source_artifact.id,),
        status=AnalysisStatus.COMPLETED,
    )
    provenance = ProvenanceRecord(
        subject_id=target_analysis.id,
        tool="ecatvasp.geometry",
        tool_version="0.9",
    )
    dependency = DependencyRecord(
        upstream_id=source_artifact.id,
        downstream_id=target_analysis.id,
        kind=DependencyKind.SCIENTIFIC,
        role="parsed_result",
        recorded_hash="b" * 64,
    )
    bundle = ProjectBundle(
        project=project,
        artifacts=(source_artifact,),
        analyses=(source_analysis, target_analysis),
        provenance_records=(provenance,),
        dependency_records=(dependency,),
    )

    row = build_scientific_inventory(bundle).row(target_analysis.id)

    assert row.freshness.state is FreshnessState.INVALID
    assert row.attention_codes == ("freshness_invalid",)
    assert tuple(reason.code for reason in row.freshness.reasons) == (
        "scientific_hash_missing",
    )
    assert row.freshness.reasons[0].upstream_id == source_artifact.id


def test_invalid_lifecycle_status_is_forwarded_into_authoritative_freshness_override() -> None:
    bundle, _, analysis, _, _ = _source_linked_bundle(
        analysis_status=AnalysisStatus.INVALID
    )

    row = build_scientific_inventory(bundle).row(analysis.id)

    assert row.status == "invalid"
    assert row.freshness.state is FreshnessState.INVALID
    assert tuple(reason.code for reason in row.freshness.reasons) == ("explicitly_invalid",)
    assert row.attention_codes == ("analysis_status_invalid", "freshness_invalid")


def test_inventory_rejects_unknown_or_malformed_observed_hashes() -> None:
    bundle, snapshot, _, _, _ = _source_linked_bundle()

    with pytest.raises(WorkspaceInspectionError, match="outside the project inventory"):
        build_scientific_inventory(bundle, observed_hashes={UUID(int=99): "a" * 64})

    with pytest.raises(WorkspaceInspectionError, match="64-character"):
        build_scientific_inventory(bundle, observed_hashes={snapshot.id: "not-a-hash"})


def test_inventory_does_not_advance_project_schema() -> None:
    bundle, _, _, _, _ = _source_linked_bundle()

    inventory = build_scientific_inventory(bundle)

    assert SCHEMA_VERSION == 3
    assert bundle.project.schema_version == SCHEMA_VERSION
    assert inventory.project_id == bundle.project.id

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

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
from ecatvasp.provenance import (
    DependencyKind,
    DependencyRecord,
    ProvenanceRecord,
    scientific_hash,
)
from ecatvasp.reporting import (
    REPORT_CONTRACT_VERSION,
    ScientificReportError,
    build_scientific_report,
    render_inventory_csv,
    render_report_json,
    render_report_markdown,
)
from ecatvasp.schema.version import SCHEMA_VERSION
from ecatvasp.storage import ProjectBundle
from ecatvasp.visualization import (
    CHEConditionsPresentation,
    ReactionDiagramDescriptorPresentation,
    ReactionDiagramPresentationDataset,
    ReactionDiagramSourcePresentation,
    ReactionDiagramStatePresentation,
    ReactionDiagramStepPresentation,
    build_structure_presentation,
)
from ecatvasp.workspace import build_scientific_inventory


def _bundle() -> tuple[ProjectBundle, StructureSnapshot, Analysis, ProvenanceRecord]:
    project = Project(
        name="Reporting acceptance",
        slug="reporting-acceptance",
        schema_version=SCHEMA_VERSION,
        created_at=datetime(2026, 9, 7, 7, 0, tzinfo=UTC),
    )
    snapshot = StructureSnapshot(
        label="accepted slab",
        lattice=Lattice(
            vectors=((3.0, 0.0, 0.0), (0.0, 3.0, 0.0), (0.0, 0.0, 15.0))
        ),
        sites=(
            StructureSite(
                atom_uid=AtomUid(project.id),
                element="C",
                fractional_coords=(0.0, 0.0, 0.5),
            ),
        ),
    )
    analysis = Analysis(
        project_id=project.id,
        analysis_type=AnalysisType.GEOMETRY,
        input_artifact_ids=(),
        status=AnalysisStatus.BLOCKED,
        tool="ecatvasp.geometry",
        tool_version="0.9",
    )
    provenance = ProvenanceRecord(
        subject_id=analysis.id,
        tool="ecatvasp.geometry",
        tool_version="0.9",
        parameters_hash="a" * 64,
        created_at=datetime(2026, 9, 7, 7, 1, tzinfo=UTC),
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
        provenance_records=(provenance,),
        dependency_records=(dependency,),
    )
    bundle.validate()
    return bundle, snapshot, analysis, provenance


def _reaction_presentation(project: Project) -> ReactionDiagramPresentationDataset:
    baseline = CHEConditionsPresentation(
        temperature_k=298.15,
        potential_v=0.0,
        ph=0.0,
        potential_reference="rhe",
        ph_semantics="included_in_rhe",
        parameters_hash="1" * 64,
    )
    requested = CHEConditionsPresentation(
        temperature_k=298.15,
        potential_v=-0.2,
        ph=7.0,
        potential_reference="rhe",
        ph_semantics="included_in_rhe",
        parameters_hash="2" * 64,
    )
    return ReactionDiagramPresentationDataset(
        project_id=project.id,
        source_result_hash="3" * 64,
        potential_view_result_hash="4" * 64,
        pathway_definition_hash="5" * 64,
        baseline_pathway_result_hash="6" * 64,
        baseline_conditions=baseline,
        requested_conditions=requested,
        states=(
            ReactionDiagramStatePresentation(
                index=0,
                state_key="*",
                cumulative_free_energy_ev=0.0,
            ),
            ReactionDiagramStatePresentation(
                index=1,
                state_key="*COOH",
                cumulative_free_energy_ev=0.45,
            ),
        ),
        steps=(
            ReactionDiagramStepPresentation(
                index=0,
                step_key="cooh-formation",
                from_state_key="*",
                to_state_key="*COOH",
                che_coefficient=1.0,
                potential_slope_ev_per_v=-1.0,
                baseline_delta_g_ev=0.65,
                target_delta_g_ev=0.45,
            ),
        ),
        descriptors=(
            ReactionDiagramDescriptorPresentation(
                key="limiting-potential",
                kind="limiting_potential",
                value=-0.45,
                unit="V",
                source_result_hash="7" * 64,
                definition_hash="8" * 64,
            ),
        ),
        sources=(
            ReactionDiagramSourcePresentation(
                species_key="surface",
                source_kind="thermochemistry",
                source_hash="9" * 64,
                analysis_id="018f1000-0000-7000-8000-000000000001",
                artifact_id="018f1000-0000-7000-8000-000000000002",
                artifact_sha256="a" * 64,
                artifact_result_hash="b" * 64,
            ),
        ),
    )


def test_report_exports_are_deterministic_and_preserve_stale_blocked_context() -> None:
    bundle, snapshot, analysis, provenance = _bundle()
    inventory = build_scientific_inventory(
        bundle,
        observed_hashes={snapshot.id: "f" * 64},
    )
    structure = build_structure_presentation(snapshot, matterviz_available=False)
    reaction = _reaction_presentation(bundle.project)

    first = build_scientific_report(
        bundle,
        inventory=inventory,
        presentations=(reaction, structure),
    )
    second = build_scientific_report(
        bundle,
        inventory=inventory,
        presentations=(structure, reaction),
    )

    assert first.report_hash == second.report_hash
    assert render_report_json(first) == render_report_json(second)
    assert render_inventory_csv(first) == render_inventory_csv(second)
    assert render_report_markdown(first) == render_report_markdown(second)

    analysis_row = first.inventory.row(analysis.id)
    assert analysis_row.status == "blocked"
    assert analysis_row.freshness.state.value == "stale"
    assert analysis_row.attention_codes == (
        "analysis_status_blocked",
        "freshness_stale",
    )

    json_text = render_report_json(first)
    assert '"status": "blocked"' in json_text
    assert '"state": "stale"' in json_text
    assert '"code": "scientific_hash_changed"' in json_text
    assert '"potential_v": -0.2' in json_text
    assert '"free_energy_unit": "eV"' in json_text
    assert '"kind": "limiting_potential"' in json_text

    csv_text = render_inventory_csv(first)
    assert str(analysis.id) in csv_text
    assert "analysis_status_blocked;freshness_stale" in csv_text
    assert "scientific_hash_changed" in csv_text
    assert str(provenance.id) in csv_text

    markdown = render_report_markdown(first)
    assert "## Attention and freshness" in markdown
    assert "analysis_status_blocked" in markdown
    assert "freshness_stale" in markdown
    assert "scientific_hash_changed" in markdown
    assert "U=-0.2 V vs RHE" in markdown
    assert "limiting-potential" in markdown
    assert "Free-energy unit: eV" in markdown


def test_report_hash_changes_with_authoritative_inventory_content() -> None:
    bundle, snapshot, _, _ = _bundle()
    fresh = build_scientific_inventory(bundle)
    stale = build_scientific_inventory(
        bundle,
        observed_hashes={snapshot.id: "f" * 64},
    )

    fresh_report = build_scientific_report(bundle, inventory=fresh)
    stale_report = build_scientific_report(bundle, inventory=stale)

    assert fresh_report.report_hash != stale_report.report_hash
    assert fresh_report.projection.projection_hash == stale_report.projection.projection_hash


def test_report_rejects_foreign_inventory_and_presentation() -> None:
    bundle, snapshot, _, _ = _bundle()
    inventory = build_scientific_inventory(bundle)
    structure = build_structure_presentation(snapshot, matterviz_available=False)

    other_bundle, _, _, _ = _bundle()
    other_inventory = build_scientific_inventory(other_bundle)
    with pytest.raises(ScientificReportError, match="another Project"):
        build_scientific_report(bundle, inventory=other_inventory)

    foreign_reaction = replace(
        _reaction_presentation(bundle.project),
        project_id=other_bundle.project.id,
    )
    with pytest.raises(ScientificReportError, match="another Project"):
        build_scientific_report(
            bundle,
            inventory=inventory,
            presentations=(structure, foreign_reaction),
        )


def test_report_rejects_stale_inventory_entity_set() -> None:
    bundle, _, _, _ = _bundle()
    inventory = build_scientific_inventory(bundle)
    stale_inventory = replace(inventory, rows=inventory.rows[:-1])

    with pytest.raises(ScientificReportError, match="exact current project entity set"):
        build_scientific_report(bundle, inventory=stale_inventory)


def test_reporting_is_non_scientific_and_does_not_advance_schema() -> None:
    bundle, _, _, _ = _bundle()
    report = build_scientific_report(bundle)

    assert REPORT_CONTRACT_VERSION == "ecatvasp-scientific-report-v1"
    assert SCHEMA_VERSION == 3
    assert bundle.project.schema_version == SCHEMA_VERSION
    assert report.contract_version == REPORT_CONTRACT_VERSION
    assert len(report.report_hash) == 64

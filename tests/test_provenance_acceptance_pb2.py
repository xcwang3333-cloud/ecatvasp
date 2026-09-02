from __future__ import annotations

from dataclasses import replace
from hashlib import sha256

from ecatvasp.domain import (
    ActiveSite,
    AdsorptionState,
    Analysis,
    AnalysisStatus,
    AnalysisType,
    Artifact,
    ArtifactAvailability,
    ArtifactType,
    BindingEdge,
    BindingMode,
    Calculation,
    CalculationProducerRef,
    CalculationScientificStatus,
    CalculationType,
    Catalyst,
    KPointPolicy,
    KPointPolicyKind,
    Lattice,
    MethodDefinition,
    MethodFingerprint,
    PotcarIdentity,
    Project,
    ProtocolDefinition,
    RecipeIdentity,
    RetrievalPolicy,
    SideLabel,
    SiteSide,
    StateConformer,
    StructureOrigin,
    StructureSite,
    StructureSnapshot,
    StructureVariant,
    VariantType,
    new_atom_uid,
    new_structure_snapshot_id,
)
from ecatvasp.domain.ids import new_uuid7
from ecatvasp.provenance import (
    DependencyKind,
    DependencyRecord,
    FreshnessEngine,
    FreshnessState,
    scientific_hash,
)


def _digest(label: str) -> str:
    return sha256(label.encode()).hexdigest()


def test_pb2_current_structure_change_stales_only_dependent_analysis_chain() -> None:
    project = Project(name="Pb nuclearity CO2RR", slug="pb-nuclearity-co2rr")
    catalyst = Catalyst(
        project_id=project.id,
        name="Pb2-NC",
        slug="pb2-nc",
        series_key="nuclearity",
        series_value=2,
    )

    carbon = new_atom_uid()
    pb_top = new_atom_uid()
    pb_bottom = new_atom_uid()
    cooh_c = new_atom_uid()
    cooh_o = new_atom_uid()
    lattice = Lattice(
        vectors=((10.0, 0.0, 0.0), (0.0, 10.0, 0.0), (0.0, 0.0, 20.0))
    )
    old_snapshot = StructureSnapshot(
        lattice=lattice,
        sites=(
            StructureSite(carbon, "C", (0.1, 0.1, 0.5)),
            StructureSite(pb_top, "Pb", (0.4, 0.4, 0.62)),
            StructureSite(pb_bottom, "Pb", (0.6, 0.6, 0.38)),
            StructureSite(cooh_c, "C", (0.45, 0.45, 0.68)),
            StructureSite(cooh_o, "O", (0.56, 0.56, 0.44)),
        ),
        label="Pb2-*COOH v1",
        origin=StructureOrigin.BUILT,
    )
    variant = StructureVariant(
        catalyst_id=catalyst.id,
        name="opposite-side Pb2",
        variant_type=VariantType.SITE_TOPOLOGY,
        topology_tags=("opposite-side",),
        current_structure_snapshot_id=old_snapshot.id,
    )
    active_site = ActiveSite(
        structure_variant_id=variant.id,
        center_atom_uids=(pb_top, pb_bottom),
        topology="opposite-side",
        side_labels=(
            SideLabel(pb_top, SiteSide.TOP),
            SideLabel(pb_bottom, SiteSide.BOTTOM),
        ),
    )
    state = AdsorptionState(
        structure_variant_id=variant.id,
        state_label="*COOH",
        active_site_id=active_site.id,
        adsorbates=("COOH",),
    )
    conformer = StateConformer(
        adsorption_state_id=state.id,
        structure_snapshot_id=old_snapshot.id,
        name="multicenter COOH",
        binding_mode=BindingMode.MULTICENTER,
        binding_edges=(
            BindingEdge(cooh_c, pb_top, "C-Pb_top"),
            BindingEdge(cooh_o, pb_bottom, "O-Pb_bottom"),
        ),
    )

    method = MethodFingerprint(
        method=MethodDefinition(
            xc_functional="PBE",
            potcar_family="PBE_54",
            potcars=(
                PotcarIdentity("C", "C", _digest("C-potcar")),
                PotcarIdentity("O", "O", _digest("O-potcar")),
                PotcarIdentity("Pb", "Pb_d", _digest("Pb-potcar")),
            ),
            dispersion_model="D3(BJ)",
        ),
        protocol=ProtocolDefinition(
            encut_ev=450.0,
            kpoints=KPointPolicy(KPointPolicyKind.EXPLICIT_MESH, mesh=(3, 3, 1)),
            ediffg_ev_per_angstrom=-0.02,
        ),
        recipe=RecipeIdentity("WXC.VASP.AdsorbateRelax"),
    )
    calculation = Calculation(
        project_id=project.id,
        calculation_type=CalculationType.RELAX,
        input_structure_snapshot_id=old_snapshot.id,
        recipe_id="WXC.VASP.AdsorbateRelax",
        method_fingerprint_id=method.id,
        status=CalculationScientificStatus.CONVERGED,
    )
    chgcar = Artifact(
        artifact_type=ArtifactType.CHGCAR,
        producer=CalculationProducerRef(calculation.id),
        availability=ArtifactAvailability.LOCAL,
        retrieval_policy=RetrievalPolicy.ON_DEMAND,
        local_path="calculations/pb2-cooh/CHGCAR",
        sha256=_digest("chgcar-v1"),
    )
    bader = Analysis(
        project_id=project.id,
        analysis_type=AnalysisType.BADER,
        input_artifact_ids=(chgcar.id,),
        status=AnalysisStatus.COMPLETED,
        tool="bader",
        tool_version="1.05",
    )

    execution_profile_id = new_uuid7()
    dependencies = (
        DependencyRecord(
            upstream_id=variant.id,
            downstream_id=calculation.id,
            kind=DependencyKind.SCIENTIFIC,
            role="current_structure_content",
            recorded_hash=scientific_hash(old_snapshot),
        ),
        DependencyRecord(
            upstream_id=method.id,
            downstream_id=calculation.id,
            kind=DependencyKind.SCIENTIFIC,
            role="method_fingerprint",
            recorded_hash=scientific_hash(method),
        ),
        DependencyRecord(
            upstream_id=execution_profile_id,
            downstream_id=calculation.id,
            kind=DependencyKind.EXECUTION,
            role="execution_profile",
            recorded_hash=_digest("ncore-4"),
        ),
        DependencyRecord(
            upstream_id=calculation.id,
            downstream_id=chgcar.id,
            kind=DependencyKind.SCIENTIFIC,
            role="producer_calculation",
            recorded_hash=scientific_hash(calculation),
        ),
        DependencyRecord(
            upstream_id=chgcar.id,
            downstream_id=bader.id,
            kind=DependencyKind.SCIENTIFIC,
            role="input_charge_density",
            recorded_hash=scientific_hash(chgcar),
        ),
    )
    engine = FreshnessEngine(dependencies)
    node_ids = {
        variant.id,
        method.id,
        execution_profile_id,
        calculation.id,
        chgcar.id,
        bader.id,
    }

    execution_only_change = engine.evaluate(
        node_ids=node_ids,
        current_hashes={
            variant.id: scientific_hash(old_snapshot),
            method.id: scientific_hash(method),
            execution_profile_id: _digest("ncore-8"),
            calculation.id: scientific_hash(calculation),
            chgcar.id: scientific_hash(chgcar),
        },
    )
    assert execution_only_change[calculation.id].state is FreshnessState.FRESH
    assert execution_only_change[bader.id].state is FreshnessState.FRESH

    new_snapshot = StructureSnapshot(
        lattice=lattice,
        sites=(
            StructureSite(carbon, "C", (0.1, 0.1, 0.5)),
            StructureSite(pb_top, "Pb", (0.4, 0.4, 0.621)),
            StructureSite(pb_bottom, "Pb", (0.6, 0.6, 0.379)),
            StructureSite(cooh_c, "C", (0.451, 0.45, 0.681)),
            StructureSite(cooh_o, "O", (0.559, 0.56, 0.439)),
        ),
        id=new_structure_snapshot_id(),
        label="Pb2-*COOH v2",
        origin=StructureOrigin.EDITED,
        parent_snapshot_id=old_snapshot.id,
    )
    current_variant = replace(
        variant,
        current_structure_snapshot_id=new_snapshot.id,
    )
    assert current_variant.id == variant.id
    structure_change = engine.evaluate(
        node_ids=node_ids,
        current_hashes={
            variant.id: scientific_hash(new_snapshot),
            method.id: scientific_hash(method),
            execution_profile_id: _digest("ncore-8"),
            calculation.id: scientific_hash(calculation),
            chgcar.id: scientific_hash(chgcar),
        },
    )

    assert structure_change[variant.id].state is FreshnessState.FRESH
    assert structure_change[calculation.id].state is FreshnessState.STALE
    assert structure_change[chgcar.id].state is FreshnessState.STALE
    assert structure_change[bader.id].state is FreshnessState.STALE

    assert conformer.structure_snapshot_id == old_snapshot.id
    assert active_site.center_atom_uids == (pb_top, pb_bottom)

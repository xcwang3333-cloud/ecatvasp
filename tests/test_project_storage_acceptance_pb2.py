from __future__ import annotations

from ecatvasp.domain import (
    ActiveSite,
    AdsorptionState,
    BindingEdge,
    BindingMode,
    Calculation,
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
    ScientificInputDigest,
    SideLabel,
    SiteSide,
    StateConformer,
    StructureOrigin,
    StructureSite,
    StructureSnapshot,
    StructureVariant,
    VariantType,
    new_atom_uid,
)
from ecatvasp.storage import ProjectBundle, ProjectStore


def _sha(character: str) -> str:
    return character * 64


def test_pb2_multicenter_project_survives_save_close_reopen(tmp_path) -> None:
    project = Project(name="Pb2 CO2RR", slug="pb2-co2rr")
    catalyst = Catalyst(
        project_id=project.id,
        name="Pb2-NC",
        slug="pb2-nc",
        support_type="N-doped carbon",
        series_key="Pb_n",
        series_value=2,
    )

    pb_top = new_atom_uid()
    pb_bottom = new_atom_uid()
    carbon = new_atom_uid()
    cooh_c = new_atom_uid()
    cooh_o = new_atom_uid()
    cooh_h = new_atom_uid()
    snapshot = StructureSnapshot(
        lattice=Lattice(
            vectors=(
                (15.0, 0.0, 0.0),
                (0.0, 15.0, 0.0),
                (0.0, 0.0, 20.0),
            )
        ),
        sites=(
            StructureSite(pb_top, "Pb", (0.50, 0.50, 0.58)),
            StructureSite(pb_bottom, "Pb", (0.50, 0.50, 0.42)),
            StructureSite(carbon, "C", (0.25, 0.25, 0.50)),
            StructureSite(cooh_c, "C", (0.50, 0.50, 0.67)),
            StructureSite(cooh_o, "O", (0.50, 0.50, 0.35)),
            StructureSite(cooh_h, "H", (0.55, 0.50, 0.31)),
        ),
        label="Pb2-*COOH initial",
        origin=StructureOrigin.BUILT,
    )
    variant = StructureVariant(
        catalyst_id=catalyst.id,
        name="opposite-side",
        variant_type=VariantType.SITE_TOPOLOGY,
        topology_tags=("opposite-side",),
        current_structure_snapshot_id=snapshot.id,
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
        structure_snapshot_id=snapshot.id,
        name="dual-center-cooh",
        binding_mode=BindingMode.MULTICENTER,
        binding_edges=(
            BindingEdge(cooh_c, pb_top, "C-Pb_top"),
            BindingEdge(cooh_o, pb_bottom, "O-Pb_bottom"),
        ),
    )

    method = MethodDefinition(
        xc_functional="PBE",
        potcar_family="PBE_54",
        potcars=(
            PotcarIdentity("C", "C", _sha("a")),
            PotcarIdentity("H", "H", _sha("b")),
            PotcarIdentity("O", "O", _sha("c")),
            PotcarIdentity("Pb", "Pb_d", _sha("d")),
        ),
        dispersion_model="D3(BJ)",
    )
    protocol = ProtocolDefinition(
        encut_ev=450.0,
        kpoints=KPointPolicy(KPointPolicyKind.EXPLICIT_MESH, mesh=(3, 3, 1)),
        ediffg_ev_per_angstrom=-0.02,
    )
    recipe = RecipeIdentity("ECatVASP.VASP.AdsorbateRelax")
    fingerprint = MethodFingerprint(
        method=method,
        protocol=protocol,
        recipe=recipe,
        input_digests=(ScientificInputDigest("structure", _sha("e")),),
    )
    calculation = Calculation(
        project_id=project.id,
        calculation_type=CalculationType.RELAX,
        input_structure_snapshot_id=snapshot.id,
        recipe_id=recipe.recipe_id,
        method_fingerprint_id=fingerprint.id,
        slug="pb2-cooh-relax",
    )

    bundle = ProjectBundle(
        project=project,
        catalysts=(catalyst,),
        structure_variants=(variant,),
        structure_snapshots=(snapshot,),
        active_sites=(active_site,),
        adsorption_states=(state,),
        state_conformers=(conformer,),
        method_fingerprints=(fingerprint,),
        calculations=(calculation,),
    )

    ProjectStore(tmp_path).save(bundle)
    reopened = ProjectStore(tmp_path).open()

    assert reopened == bundle
    assert reopened.active_sites[0].center_atom_uids == (pb_top, pb_bottom)
    assert reopened.state_conformers[0].binding_edges == conformer.binding_edges
    assert reopened.method_fingerprints[0].core_method_hash == fingerprint.core_method_hash
    assert reopened.method_fingerprints[0].protocol_hash == fingerprint.protocol_hash
    assert reopened.method_fingerprints[0].instance_hash == fingerprint.instance_hash

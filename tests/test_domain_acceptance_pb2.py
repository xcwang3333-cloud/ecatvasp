"""Scientific acceptance fixture: opposite-side Pb2 with multicenter *COOH."""

from ecatvasp.domain import (
    ActiveSite,
    AdsorptionState,
    BindingEdge,
    BindingMode,
    Catalyst,
    Lattice,
    Project,
    SideLabel,
    SiteSide,
    StateConformer,
    StructureOrigin,
    StructureSite,
    StructureSnapshot,
    StructureVariant,
    VariantType,
    new_atom_uid,
    validate_conformer_context,
)


def test_pb2_opposite_side_multicenter_cooh_is_unambiguous() -> None:
    project = Project(name="Pb atomic ensemble CO2RR", slug="pb-ensemble-co2rr")
    catalyst = Catalyst(
        project_id=project.id,
        name="Pb2-NC",
        slug="pb2-nc",
        support_type="N-doped carbon",
        series_key="Pb_n",
        series_value=2,
    )
    variant = StructureVariant(
        catalyst_id=catalyst.id,
        name="opposite-side",
        variant_type=VariantType.SITE_TOPOLOGY,
        topology_tags=("opposite-side", "dual-atom"),
    )

    pb_top = new_atom_uid()
    pb_bottom = new_atom_uid()
    carbon = new_atom_uid()
    cooh_c = new_atom_uid()
    cooh_o = new_atom_uid()
    cooh_oh_o = new_atom_uid()
    cooh_h = new_atom_uid()
    snapshot = StructureSnapshot(
        lattice=Lattice(((12.0, 0.0, 0.0), (0.0, 12.0, 0.0), (0.0, 0.0, 20.0))),
        sites=(
            StructureSite(pb_top, "Pb", (0.50, 0.50, 0.58)),
            StructureSite(pb_bottom, "Pb", (0.50, 0.50, 0.42)),
            StructureSite(carbon, "C", (0.50, 0.50, 0.50)),
            StructureSite(cooh_c, "C", (0.50, 0.50, 0.65)),
            StructureSite(cooh_o, "O", (0.55, 0.50, 0.61)),
            StructureSite(cooh_oh_o, "O", (0.45, 0.50, 0.69)),
            StructureSite(cooh_h, "H", (0.42, 0.50, 0.73)),
        ),
        label="Pb2-*COOH multicenter initial",
        origin=StructureOrigin.BUILT,
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
        active_site_id=active_site.id,
        state_label="*COOH",
        adsorbates=("COOH",),
        reaction_role="CO2RR_CO_intermediate",
    )
    conformer = StateConformer(
        adsorption_state_id=state.id,
        structure_snapshot_id=snapshot.id,
        name="dual-center-COOH",
        binding_mode=BindingMode.MULTICENTER,
        binding_edges=(
            BindingEdge(cooh_c, pb_top, "COOH.C-Pb_top"),
            BindingEdge(cooh_o, pb_bottom, "COOH.O-Pb_bottom"),
        ),
    )

    validate_conformer_context(
        active_site=active_site, state=state, conformer=conformer, snapshot=snapshot
    )

    assert catalyst.project_id == project.id
    assert variant.catalyst_id == catalyst.id
    assert active_site.nuclearity == 2
    assert active_site.topology == "opposite-side"
    assert state.active_site_id == active_site.id
    assert conformer.structure_snapshot_id == snapshot.id
    assert {edge.site_atom_uid for edge in conformer.binding_edges} == {pb_top, pb_bottom}
    assert all(snapshot.contains_atom(edge.adsorbate_atom_uid) for edge in conformer.binding_edges)
    assert all(snapshot.contains_atom(edge.site_atom_uid) for edge in conformer.binding_edges)

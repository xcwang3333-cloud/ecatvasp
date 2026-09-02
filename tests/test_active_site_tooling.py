from __future__ import annotations

from dataclasses import replace

import pytest

from ecatvasp.domain import (
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
from ecatvasp.structures import (
    ActiveSiteToolingError,
    MultiMetalCenterSpec,
    MultiMetalSiteSpec,
    SingleMetalSiteSpec,
    active_site_from_multi_metal,
    active_site_from_single_metal,
    build_multi_metal_site,
    build_single_metal_site,
    create_active_site,
    normalize_coordination_environment,
    normalize_topology,
    resolve_active_site_centers,
    validate_active_site_current_context,
    validate_active_site_snapshot_compatibility,
)


def _lattice() -> Lattice:
    return Lattice(vectors=((10.0, 0.0, 0.0), (0.0, 10.0, 0.0), (0.0, 0.0, 20.0)))


def _variant(snapshot: StructureSnapshot) -> StructureVariant:
    project = Project(name="Active site tooling", slug="active-site-tooling")
    catalyst = Catalyst(project_id=project.id, name="model", slug="model")
    return StructureVariant(
        catalyst_id=catalyst.id,
        name="site model",
        variant_type=VariantType.SITE_TOPOLOGY,
        current_structure_snapshot_id=snapshot.id,
    )


def _single_support() -> StructureSnapshot:
    return StructureSnapshot(
        lattice=_lattice(),
        sites=(
            StructureSite(new_atom_uid(), "N", (0.40, 0.40, 0.50)),
            StructureSite(new_atom_uid(), "N", (0.60, 0.40, 0.50)),
            StructureSite(new_atom_uid(), "N", (0.40, 0.60, 0.50)),
            StructureSite(new_atom_uid(), "N", (0.60, 0.60, 0.50)),
        ),
        origin=StructureOrigin.BUILT,
    )


def _multi_support() -> StructureSnapshot:
    return StructureSnapshot(
        lattice=_lattice(),
        sites=(
            StructureSite(new_atom_uid(), "N", (0.10, 0.10, 0.50)),
            StructureSite(new_atom_uid(), "N", (0.30, 0.10, 0.50)),
            StructureSite(new_atom_uid(), "N", (0.60, 0.10, 0.50)),
            StructureSite(new_atom_uid(), "C", (0.80, 0.10, 0.50)),
            StructureSite(new_atom_uid(), "O", (0.10, 0.60, 0.50)),
            StructureSite(new_atom_uid(), "C", (0.30, 0.60, 0.50)),
        ),
        origin=StructureOrigin.BUILT,
    )


def test_manual_active_site_requires_current_snapshot_and_normalizes_metadata() -> None:
    pb_top = new_atom_uid()
    pb_bottom = new_atom_uid()
    snapshot = StructureSnapshot(
        lattice=_lattice(),
        sites=(
            StructureSite(pb_top, "Pb", (0.45, 0.50, 0.60)),
            StructureSite(pb_bottom, "Pb", (0.55, 0.50, 0.40)),
        ),
        origin=StructureOrigin.BUILT,
    )
    variant = _variant(snapshot)
    site = create_active_site(
        variant=variant,
        snapshot=snapshot,
        center_atom_uids=(pb_top, pb_bottom),
        side_labels=(
            SideLabel(pb_top, SiteSide.TOP),
            SideLabel(pb_bottom, SiteSide.BOTTOM),
        ),
        topology=" Opposite_Side ",
        coordination_environment=" Pb(N2) | Pb(N2) ",
    )

    assert site.center_atom_uids == (pb_top, pb_bottom)
    assert site.topology == "opposite-side"
    assert site.coordination_environment == "Pb(N2)|Pb(N2)"
    assert site.nuclearity == 2
    validate_active_site_current_context(site, variant, snapshot)


def test_create_active_site_rejects_missing_or_noncurrent_snapshot() -> None:
    snapshot = _single_support()
    center = snapshot.sites[0].atom_uid
    variant = _variant(snapshot)
    no_current = replace(variant, current_structure_snapshot_id=None)
    other_snapshot = replace(snapshot, id=type(snapshot.id)(new_atom_uid()))

    with pytest.raises(ActiveSiteToolingError, match="current_structure_snapshot_id"):
        create_active_site(
            variant=no_current,
            snapshot=snapshot,
            center_atom_uids=(center,),
        )
    with pytest.raises(ActiveSiteToolingError, match="not the StructureVariant current"):
        create_active_site(
            variant=variant,
            snapshot=other_snapshot,
            center_atom_uids=(center,),
        )


def test_create_active_site_rejects_partial_or_reordered_side_labels() -> None:
    snapshot = _single_support()
    first = snapshot.sites[0].atom_uid
    second = snapshot.sites[1].atom_uid
    variant = _variant(snapshot)

    with pytest.raises(ActiveSiteToolingError, match="cover every active center"):
        create_active_site(
            variant=variant,
            snapshot=snapshot,
            center_atom_uids=(first, second),
            side_labels=(SideLabel(first, SiteSide.TOP),),
        )
    with pytest.raises(ActiveSiteToolingError, match="follow center_atom_uids order"):
        create_active_site(
            variant=variant,
            snapshot=snapshot,
            center_atom_uids=(first, second),
            side_labels=(
                SideLabel(second, SiteSide.TOP),
                SideLabel(first, SiteSide.BOTTOM),
            ),
        )


def test_single_metal_adapter_preserves_builder_identity_and_intent() -> None:
    source = _single_support()
    result = build_single_metal_site(
        source,
        SingleMetalSiteSpec(
            metal_element="Fe",
            coordination_atom_uids=tuple(site.atom_uid for site in source.sites),
            side=SiteSide.TOP,
            height_angstrom=1.5,
        ),
    )
    variant = _variant(result.snapshot)
    site = active_site_from_single_metal(variant=variant, result=result)

    assert site.center_atom_uids == (result.metal_atom_uid,)
    assert site.side_labels == (SideLabel(result.metal_atom_uid, SiteSide.TOP),)
    assert site.topology == "single-center"
    assert site.coordination_environment == "N4"
    resolved = resolve_active_site_centers(site, result.snapshot)
    assert tuple(item.atom_uid for item in resolved) == site.center_atom_uids


def test_pb2_opposite_side_adapter_preserves_order_sides_and_shared_intent() -> None:
    source = _single_support()
    anchors = tuple(site.atom_uid for site in source.sites)
    result = build_multi_metal_site(
        source,
        MultiMetalSiteSpec(
            centers=(
                MultiMetalCenterSpec("Pb", anchors, SiteSide.TOP, 1.5),
                MultiMetalCenterSpec("Pb", anchors, SiteSide.BOTTOM, 1.5),
            ),
            metal_metal_topology_intent="Opposite_Side_Pair",
        ),
    )
    variant = _variant(result.snapshot)
    site = active_site_from_multi_metal(variant=variant, result=result)

    assert site.center_atom_uids == result.metal_atom_uids
    assert site.side_labels == (
        SideLabel(result.centers[0].metal_atom_uid, SiteSide.TOP),
        SideLabel(result.centers[1].metal_atom_uid, SiteSide.BOTTOM),
    )
    assert site.topology == "opposite-side-pair"
    assert site.coordination_environment == "Pb(N4)|Pb(N4)"
    assert len(set(site.center_atom_uids)) == 2


def test_triple_metal_adapter_keeps_center_order_and_coordination_signatures() -> None:
    source = _multi_support()
    sites = source.sites
    result = build_multi_metal_site(
        source,
        MultiMetalSiteSpec(
            centers=(
                MultiMetalCenterSpec(
                    "Fe",
                    (sites[0].atom_uid, sites[1].atom_uid),
                    SiteSide.TOP,
                    1.4,
                ),
                MultiMetalCenterSpec(
                    "Co",
                    (sites[2].atom_uid, sites[3].atom_uid),
                    SiteSide.TOP,
                    1.4,
                ),
                MultiMetalCenterSpec(
                    "Ni",
                    (sites[4].atom_uid, sites[5].atom_uid),
                    SiteSide.TOP,
                    1.4,
                ),
            ),
            metal_metal_topology_intent="Compact Ensemble",
        ),
    )
    variant = _variant(result.snapshot)
    site = active_site_from_multi_metal(variant=variant, result=result)

    assert site.nuclearity == 3
    assert site.center_atom_uids == result.metal_atom_uids
    assert site.topology == "compact-ensemble"
    assert site.coordination_environment == "Fe(N2)|Co(NC)|Ni(OC)"
    assert tuple(label.atom_uid for label in site.side_labels) == result.metal_atom_uids


def test_snapshot_evolution_preserves_active_site_only_while_center_uid_survives() -> None:
    center = new_atom_uid()
    support = new_atom_uid()
    snapshot_v1 = StructureSnapshot(
        lattice=_lattice(),
        sites=(
            StructureSite(center, "Fe", (0.50, 0.50, 0.58)),
            StructureSite(support, "N", (0.50, 0.50, 0.50)),
        ),
        origin=StructureOrigin.BUILT,
    )
    variant = _variant(snapshot_v1)
    site = create_active_site(
        variant=variant,
        snapshot=snapshot_v1,
        center_atom_uids=(center,),
        side_labels=(SideLabel(center, SiteSide.TOP),),
        topology="single-center",
        coordination_environment="N",
    )
    snapshot_v2 = StructureSnapshot(
        lattice=snapshot_v1.lattice,
        sites=(
            StructureSite(center, "Fe", (0.51, 0.49, 0.57)),
            StructureSite(support, "N", (0.50, 0.50, 0.50)),
        ),
        origin=StructureOrigin.RELAXED,
        parent_snapshot_id=snapshot_v1.id,
    )
    updated_variant = replace(variant, current_structure_snapshot_id=snapshot_v2.id)

    validate_active_site_snapshot_compatibility(site, snapshot_v2)
    validate_active_site_current_context(site, updated_variant, snapshot_v2)
    assert resolve_active_site_centers(site, snapshot_v2)[0].fractional_coords == (
        0.51,
        0.49,
        0.57,
    )

    replacement_uid = new_atom_uid()
    snapshot_v3 = StructureSnapshot(
        lattice=snapshot_v2.lattice,
        sites=(
            StructureSite(replacement_uid, "Fe", (0.51, 0.49, 0.57)),
            StructureSite(support, "N", (0.50, 0.50, 0.50)),
        ),
        origin=StructureOrigin.EDITED,
        parent_snapshot_id=snapshot_v2.id,
    )
    with pytest.raises(ActiveSiteToolingError, match="absent"):
        validate_active_site_snapshot_compatibility(site, snapshot_v3)


def test_block7_block8_interface_allows_adsorbate_conformer_snapshot() -> None:
    center = new_atom_uid()
    support = new_atom_uid()
    base_snapshot = StructureSnapshot(
        lattice=_lattice(),
        sites=(
            StructureSite(center, "Fe", (0.50, 0.50, 0.58)),
            StructureSite(support, "N", (0.50, 0.50, 0.50)),
        ),
        origin=StructureOrigin.BUILT,
    )
    variant = _variant(base_snapshot)
    active_site = create_active_site(
        variant=variant,
        snapshot=base_snapshot,
        center_atom_uids=(center,),
        side_labels=(SideLabel(center, SiteSide.TOP),),
        topology="single-center",
        coordination_environment="N",
    )

    adsorbate_c = new_atom_uid()
    adsorbate_o = new_atom_uid()
    conformer_snapshot = StructureSnapshot(
        lattice=base_snapshot.lattice,
        sites=(
            *base_snapshot.sites,
            StructureSite(adsorbate_c, "C", (0.50, 0.50, 0.66)),
            StructureSite(adsorbate_o, "O", (0.55, 0.50, 0.70)),
        ),
        origin=StructureOrigin.EDITED,
        parent_snapshot_id=base_snapshot.id,
    )

    resolved = resolve_active_site_centers(active_site, conformer_snapshot)
    assert resolved[0].atom_uid == center
    state = AdsorptionState(
        structure_variant_id=variant.id,
        state_label="*COOH",
        active_site_id=active_site.id,
        adsorbates=("COOH",),
    )
    conformer = StateConformer(
        adsorption_state_id=state.id,
        structure_snapshot_id=conformer_snapshot.id,
        name="single-center COOH",
        binding_mode=BindingMode.SINGLE_CENTER,
        binding_edges=(BindingEdge(adsorbate_c, center, "C-Fe"),),
    )
    validate_conformer_context(
        active_site=active_site,
        state=state,
        conformer=conformer,
        snapshot=conformer_snapshot,
    )


def test_text_normalizers_fail_closed_on_blank_segments() -> None:
    assert normalize_topology(" Same_Side ") == "same-side"
    assert normalize_coordination_environment("Fe(N2) | Co(NC)") == "Fe(N2)|Co(NC)"
    with pytest.raises(ActiveSiteToolingError, match="topology"):
        normalize_topology("  ")
    with pytest.raises(ActiveSiteToolingError, match="empty center segments"):
        normalize_coordination_environment("Fe(N2)||Co(NC)")

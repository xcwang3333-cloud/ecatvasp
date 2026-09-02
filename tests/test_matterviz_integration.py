from __future__ import annotations

from dataclasses import replace

import pytest

from ecatvasp import domain, structures
from ecatvasp import visualization


def _snapshot(
    elements: tuple[str, ...],
    coordinates: tuple[tuple[float, float, float], ...],
) -> domain.StructureSnapshot:
    return domain.StructureSnapshot(
        lattice=domain.Lattice(
            ((10.0, 0.0, 0.0), (0.0, 10.0, 0.0), (0.0, 0.0, 20.0))
        ),
        sites=tuple(
            domain.StructureSite(domain.new_atom_uid(), element, coordinate)
            for element, coordinate in zip(elements, coordinates, strict=True)
        ),
        origin=domain.StructureOrigin.BUILT,
        periodic=(True, True, True),
    )


def _variant(snapshot: domain.StructureSnapshot, name: str) -> domain.StructureVariant:
    project = domain.Project(name=f"{name} project", slug=f"{name}-project")
    catalyst = domain.Catalyst(
        project_id=project.id,
        name=f"{name} catalyst",
        slug=f"{name}-catalyst",
    )
    return domain.StructureVariant(
        catalyst_id=catalyst.id,
        name=name,
        variant_type=domain.VariantType.SITE_TOPOLOGY,
        current_structure_snapshot_id=snapshot.id,
    )


def _context_for_centers(center_count: int) -> structures.ConformerVisualizationContext:
    if center_count not in (1, 2, 3):
        raise ValueError("test helper supports one to three centers")
    elements = tuple("Pb" for _ in range(center_count)) + ("C", "O")
    coords = tuple(
        (0.2 + 0.2 * index, 0.5, 0.5) for index in range(center_count)
    ) + ((0.5, 0.5, 0.65), (0.55, 0.5, 0.65))
    snapshot = _snapshot(elements, coords)
    centers = tuple(site.atom_uid for site in snapshot.sites[:center_count])
    carbon = snapshot.sites[-2].atom_uid
    oxygen = snapshot.sites[-1].atom_uid
    mode = (
        domain.BindingMode.SINGLE_CENTER
        if center_count == 1
        else domain.BindingMode.MULTICENTER
    )
    edges = (
        (domain.BindingEdge(carbon, centers[0]),)
        if center_count == 1
        else (
            domain.BindingEdge(carbon, centers[0]),
            domain.BindingEdge(oxygen, centers[1]),
        )
    )
    side_labels = tuple(
        domain.SideLabel(atom_uid=center, side=domain.SiteSide.TOP) for center in centers
    )
    return structures.ConformerVisualizationContext(
        snapshot=snapshot,
        state_label="*COOH",
        conformer_name=f"{center_count}-center",
        binding_mode=mode,
        active_center_atom_uids=centers,
        bound_adsorbate_atom_uids=(carbon,) if center_count == 1 else (carbon, oxygen),
        binding_edges=edges,
        side_labels=side_labels,
    )


def test_snapshot_payload_preserves_order_and_uid_index_bijection() -> None:
    snapshot = _snapshot(
        ("C", "N", "Pb"),
        ((0.1, 0.2, 0.5), (0.2, 0.3, 0.5), (0.3, 0.4, 0.6)),
    )

    bundle = visualization.build_matterviz_view(snapshot)

    assert bundle.target_version == "0.6.0"
    assert bundle.contract_version == "ecatvasp-matterviz-v1"
    assert tuple(site.label for site in bundle.structure.sites) == ("C", "N", "Pb")
    expected_uids = tuple(site.atom_uid for site in snapshot.sites)
    assert bundle.atom_index_map.atom_uid_by_viewer_index == expected_uids
    for index, atom_uid in enumerate(expected_uids):
        assert bundle.atom_index_map.viewer_index(atom_uid) == index
        assert bundle.atom_index_map.atom_uid(index) == atom_uid
        assert bundle.structure.sites[index].properties["ecatvasp_atom_uid"] == str(atom_uid)
    assert "atom_uid" in bundle.fallback_extxyz


def test_atom_index_map_rejects_non_bijective_mapping_and_bad_lookup() -> None:
    snapshot = _snapshot(("C", "N"), ((0.1, 0.2, 0.5), (0.2, 0.3, 0.5)))
    first, second = (site.atom_uid for site in snapshot.sites)

    with pytest.raises(visualization.MatterVizAdapterError, match="exact bijection"):
        visualization.MatterVizAtomIndexMap(
            atom_uid_by_viewer_index=(first, second),
            viewer_index_by_atom_uid={first: 1, second: 0},
        )

    mapping = visualization.MatterVizAtomIndexMap.from_snapshot(snapshot)
    with pytest.raises(visualization.MatterVizAdapterError, match="absent"):
        mapping.viewer_index(domain.new_atom_uid())
    with pytest.raises(visualization.MatterVizAdapterError, match="out of range"):
        mapping.atom_uid(-1)


@pytest.mark.parametrize(
    "format",
    (structures.StructureFormat.POSCAR, structures.StructureFormat.CIF),
)
def test_poscar_and_cif_ingestion_use_ecatvasp_parser_before_viewer(
    format: structures.StructureFormat,
) -> None:
    snapshot = _snapshot(("C", "N"), ((0.1, 0.2, 0.5), (0.2, 0.3, 0.5)))
    text = structures.serialize_structure(snapshot, format=format)

    bundle = visualization.matterviz_view_from_text(text, format=format)

    assert len(bundle.structure.sites) == 2
    assert {site.label for site in bundle.structure.sites} == {"C", "N"}
    assert len(bundle.atom_index_map.atom_uid_by_viewer_index) == 2


def test_extxyz_ingestion_preserves_embedded_atom_uids() -> None:
    snapshot = _snapshot(("C", "N"), ((0.1, 0.2, 0.5), (0.2, 0.3, 0.5)))
    text = structures.serialize_structure(snapshot, format=structures.StructureFormat.EXTXYZ)

    bundle = visualization.matterviz_view_from_text(
        text,
        format=structures.StructureFormat.EXTXYZ,
    )

    assert bundle.atom_index_map.atom_uid_by_viewer_index == tuple(
        site.atom_uid for site in snapshot.sites
    )


def test_unavailable_runtime_returns_same_scientific_payload_with_fallback() -> None:
    snapshot = _snapshot(("C",), ((0.1, 0.2, 0.5),))

    available = visualization.build_matterviz_view(snapshot)
    fallback = visualization.build_matterviz_view(
        snapshot,
        matterviz_available=False,
        unavailable_message="MatterViz host not installed",
    )

    assert available.structure == fallback.structure
    assert available.atom_index_map == fallback.atom_index_map
    assert fallback.runtime.interactive_available is False
    assert fallback.runtime.fallback_kind == "extxyz+manifest"
    assert fallback.runtime.message == "MatterViz host not installed"
    assert fallback.fallback_extxyz == available.fallback_extxyz


@pytest.mark.parametrize("center_count", (1, 2, 3))
def test_single_dual_triple_center_overlays_preserve_active_site_order(center_count: int) -> None:
    context = _context_for_centers(center_count)

    bundle = visualization.build_matterviz_view(context.snapshot, context=context)

    expected = tuple(
        bundle.atom_index_map.viewer_index(atom_uid)
        for atom_uid in context.active_center_atom_uids
    )
    assert bundle.overlay.active_center_indices == expected
    assert tuple(label.viewer_index for label in bundle.overlay.side_labels) == expected
    assert bundle.overlay.state_label == "*COOH"
    assert bundle.overlay.binding_mode == context.binding_mode.value


def test_binding_edges_become_transient_segments_not_structure_topology() -> None:
    context = _context_for_centers(2)

    bundle = visualization.build_matterviz_view(context.snapshot, context=context)
    payload = bundle.to_dict()

    assert "bonds" not in bundle.structure.properties
    assert len(bundle.overlay.binding_segments) == 2
    assert bundle.overlay.matterviz_bonds() == (
        {
            "site_idx_1": bundle.overlay.binding_segments[0].adsorbate_index,
            "site_idx_2": bundle.overlay.binding_segments[0].site_index,
            "order": 1,
        },
        {
            "site_idx_1": bundle.overlay.binding_segments[1].adsorbate_index,
            "site_idx_2": bundle.overlay.binding_segments[1].site_index,
            "order": 1,
        },
    )
    assert payload["contract_version"] == "ecatvasp-matterviz-v1"


def test_overlay_rejects_context_from_another_snapshot() -> None:
    context = _context_for_centers(1)
    other = replace(context.snapshot, id=domain.new_structure_snapshot_id())

    with pytest.raises(visualization.MatterVizAdapterError, match="supplied StructureSnapshot"):
        visualization.build_matterviz_view(other, context=context)


def test_v02_graphene_to_pb2_multicenter_cooh_to_matterviz_end_to_end() -> None:
    graphene = structures.build_graphene(
        structures.GrapheneBuildSpec(nx=3, ny=3, vacuum_gap_angstrom=20.0)
    )
    mutation = structures.mutate_structure(
        graphene,
        vacancy_atom_uids=(graphene.sites[0].atom_uid,),
        substitutions=(
            structures.DopantSubstitution(graphene.sites[1].atom_uid, "N"),
        ),
        label="N-vacancy-graphene",
    )
    n_uid = mutation.replacement_pairs[0][1]
    carbon_uid = next(
        site.atom_uid
        for site in mutation.snapshot.sites
        if site.element == "C" and site.atom_uid != n_uid
    )
    pb2 = structures.build_multi_metal_site(
        mutation.snapshot,
        structures.MultiMetalSiteSpec(
            centers=(
                structures.MultiMetalCenterSpec(
                    "Pb",
                    (n_uid, carbon_uid),
                    domain.SiteSide.TOP,
                    1.6,
                ),
                structures.MultiMetalCenterSpec(
                    "Pb",
                    (n_uid, carbon_uid),
                    domain.SiteSide.BOTTOM,
                    1.6,
                ),
            ),
            metal_metal_topology_intent="opposite-side-pair",
        ),
    )
    variant = _variant(pb2.snapshot, "pb2-n-vacancy")
    active_site = structures.active_site_from_multi_metal(variant=variant, result=pb2)
    pb_top, pb_bottom = active_site.center_atom_uids
    cooh = structures.build_adsorbate(
        pb2.snapshot,
        active_site,
        structures.AdsorbatePlacementSpec(
            template_key="COOH",
            target_center_atom_uids=(pb_top, pb_bottom),
            binding_mode=domain.BindingMode.MULTICENTER,
            height_angstrom=2.0,
            contacts=(
                structures.AdsorbateContactSpec("C", pb_top),
                structures.AdsorbateContactSpec("O_carbonyl", pb_bottom),
            ),
            placement_direction_cartesian=(0.0, 0.0, 1.0),
            orientation_vector_cartesian=(1.0, 0.0, 0.0),
        ),
    )
    state = structures.create_adsorption_state(
        variant,
        active_site,
        state_label="*COOH",
        adsorbates=("COOH",),
    )
    conformer = structures.state_conformer_from_adsorbate_build(
        state,
        active_site,
        cooh,
        name="Pb2 opposite-side multicenter COOH",
    )
    context = structures.resolve_conformer_visualization_context(
        state,
        active_site,
        conformer,
        cooh.snapshot,
    )

    bundle = visualization.build_matterviz_view(cooh.snapshot, context=context)

    assert bundle.overlay.active_center_indices == (
        bundle.atom_index_map.viewer_index(pb_top),
        bundle.atom_index_map.viewer_index(pb_bottom),
    )
    carbon_ads_uid = cooh.atom_uid_for_key("C")
    oxygen_ads_uid = cooh.atom_uid_for_key("O_carbonyl")
    assert bundle.overlay.bound_adsorbate_indices == (
        bundle.atom_index_map.viewer_index(carbon_ads_uid),
        bundle.atom_index_map.viewer_index(oxygen_ads_uid),
    )
    assert tuple(
        (segment.adsorbate_index, segment.site_index)
        for segment in bundle.overlay.binding_segments
    ) == (
        (
            bundle.atom_index_map.viewer_index(carbon_ads_uid),
            bundle.atom_index_map.viewer_index(pb_top),
        ),
        (
            bundle.atom_index_map.viewer_index(oxygen_ads_uid),
            bundle.atom_index_map.viewer_index(pb_bottom),
        ),
    )
    assert bundle.overlay.binding_mode == domain.BindingMode.MULTICENTER.value
    assert tuple(site.atom_uid for site in graphene.sites) == tuple(
        event.source_atom_uid for event in mutation.lineage
    )
    poscar = structures.serialize_structure(cooh.snapshot, format=structures.StructureFormat.POSCAR)
    assert "Pb" in poscar
    assert "N" in poscar
    fallback = visualization.matterviz_view_from_text(
        bundle.fallback_extxyz,
        format=structures.StructureFormat.EXTXYZ,
        matterviz_available=False,
    )
    assert fallback.atom_index_map.atom_uid_by_viewer_index == tuple(
        site.atom_uid for site in cooh.snapshot.sites
    )

from __future__ import annotations

from dataclasses import replace

import pytest

from ecatvasp import domain, structures


def _support(
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


def _single_model() -> tuple[
    domain.StructureSnapshot,
    domain.StructureVariant,
    domain.ActiveSite,
]:
    support = _support(
        ("N", "N", "C"),
        ((0.45, 0.50, 0.50), (0.55, 0.50, 0.50), (0.50, 0.60, 0.50)),
    )
    anchors = tuple(site.atom_uid for site in support.sites[:2])
    metal = structures.build_single_metal_site(
        support,
        structures.SingleMetalSiteSpec(
            metal_element="Fe",
            coordination_atom_uids=anchors,
            side=domain.SiteSide.TOP,
            height_angstrom=1.6,
        ),
    )
    variant = _variant(metal.snapshot, "single-fe")
    active_site = structures.active_site_from_single_metal(variant=variant, result=metal)
    return metal.snapshot, variant, active_site


def _dual_top_model() -> tuple[
    domain.StructureSnapshot,
    domain.StructureVariant,
    domain.ActiveSite,
]:
    support = _support(
        ("N", "N", "N", "N"),
        (
            (0.25, 0.25, 0.50),
            (0.35, 0.25, 0.50),
            (0.65, 0.75, 0.50),
            (0.75, 0.75, 0.50),
        ),
    )
    first = tuple(site.atom_uid for site in support.sites[:2])
    second = tuple(site.atom_uid for site in support.sites[2:])
    metals = structures.build_multi_metal_site(
        support,
        structures.MultiMetalSiteSpec(
            centers=(
                structures.MultiMetalCenterSpec(
                    "Fe", first, domain.SiteSide.TOP, 1.6
                ),
                structures.MultiMetalCenterSpec(
                    "Co", second, domain.SiteSide.TOP, 1.6
                ),
            ),
            metal_metal_topology_intent="proximal",
        ),
    )
    variant = _variant(metals.snapshot, "dual-top")
    active_site = structures.active_site_from_multi_metal(variant=variant, result=metals)
    return metals.snapshot, variant, active_site


def _pb2_model() -> tuple[
    domain.StructureSnapshot,
    domain.StructureVariant,
    domain.ActiveSite,
]:
    support = _support(
        ("N", "N", "C", "C"),
        (
            (0.45, 0.50, 0.50),
            (0.55, 0.50, 0.50),
            (0.50, 0.45, 0.50),
            (0.50, 0.55, 0.50),
        ),
    )
    anchors = tuple(site.atom_uid for site in support.sites[:2])
    metals = structures.build_multi_metal_site(
        support,
        structures.MultiMetalSiteSpec(
            centers=(
                structures.MultiMetalCenterSpec(
                    "Pb", anchors, domain.SiteSide.TOP, 1.6
                ),
                structures.MultiMetalCenterSpec(
                    "Pb", anchors, domain.SiteSide.BOTTOM, 1.6
                ),
            ),
            metal_metal_topology_intent="opposite-side-pair",
            label="Pb2-opposite-side",
        ),
    )
    variant = _variant(metals.snapshot, "pb2-opposite")
    active_site = structures.active_site_from_multi_metal(variant=variant, result=metals)
    return metals.snapshot, variant, active_site


def _single_h_build(
    source: domain.StructureSnapshot,
    active_site: domain.ActiveSite,
    *,
    height: float,
) -> structures.AdsorbateBuildResult:
    center = active_site.center_atom_uids[0]
    return structures.build_adsorbate(
        source,
        active_site,
        structures.AdsorbatePlacementSpec(
            template_key="H",
            target_center_atom_uids=(center,),
            binding_mode=domain.BindingMode.SINGLE_CENTER,
            height_angstrom=height,
            contacts=(structures.AdsorbateContactSpec("H", center),),
        ),
    )


def _pb2_cooh_build(
    source: domain.StructureSnapshot,
    active_site: domain.ActiveSite,
) -> structures.AdsorbateBuildResult:
    pb_top, pb_bottom = active_site.center_atom_uids
    return structures.build_adsorbate(
        source,
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


def test_create_adsorption_state_normalizes_identity_and_rejects_duplicate_label() -> None:
    _, variant, active_site = _single_model()

    state = structures.create_adsorption_state(
        variant,
        active_site,
        state_label="  *COOH  ",
        adsorbates=("COOH",),
        reaction_role="  CO2RR   intermediate  ",
    )

    assert state.structure_variant_id == variant.id
    assert state.active_site_id == active_site.id
    assert state.state_label == "*COOH"
    assert state.adsorbates == ("COOH",)
    assert state.reaction_role == "CO2RR intermediate"
    with pytest.raises(structures.StateConformerToolingError, match="same normalized label"):
        structures.create_adsorption_state(
            variant,
            active_site,
            state_label="*cooh",
            adsorbates=("COOH",),
            existing_states=(state,),
        )


def test_single_center_build_materializes_exact_binding_edge() -> None:
    source, variant, active_site = _single_model()
    build = _single_h_build(source, active_site, height=1.5)
    state = structures.create_adsorption_state(
        variant, active_site, state_label="*H", adsorbates=("H",)
    )

    conformer = structures.state_conformer_from_adsorbate_build(
        state,
        active_site,
        build,
        name="  upright   H  ",
        orientation="  atop   seed ",
        rank=1,
    )

    assert conformer.name == "upright H"
    assert conformer.orientation == "atop seed"
    assert conformer.rank == 1
    assert conformer.structure_snapshot_id == build.snapshot.id
    assert conformer.binding_mode is domain.BindingMode.SINGLE_CENTER
    assert len(conformer.binding_edges) == 1
    edge = conformer.binding_edges[0]
    assert edge.adsorbate_atom_uid == build.adsorbate_atom_uids[0]
    assert edge.site_atom_uid == active_site.center_atom_uids[0]
    signature = structures.conformer_signature(conformer)
    assert signature.adsorption_state_id == state.id
    assert signature.structure_snapshot_id == build.snapshot.id


def test_bridge_materialization_preserves_one_atom_to_two_center_semantics() -> None:
    source, variant, active_site = _dual_top_model()
    iron, cobalt = active_site.center_atom_uids
    build = structures.build_adsorbate(
        source,
        active_site,
        structures.AdsorbatePlacementSpec(
            template_key="O",
            target_center_atom_uids=(iron, cobalt),
            binding_mode=domain.BindingMode.BRIDGE,
            height_angstrom=1.5,
            contacts=(
                structures.AdsorbateContactSpec("O", iron),
                structures.AdsorbateContactSpec("O", cobalt),
            ),
        ),
    )
    state = structures.create_adsorption_state(
        variant, active_site, state_label="*O-bridge", adsorbates=("O",)
    )

    conformer = structures.state_conformer_from_adsorbate_build(
        state, active_site, build, name="Fe-Co bridge O"
    )

    assert conformer.binding_mode is domain.BindingMode.BRIDGE
    assert len({edge.adsorbate_atom_uid for edge in conformer.binding_edges}) == 1
    assert {edge.site_atom_uid for edge in conformer.binding_edges} == {iron, cobalt}


def test_same_state_supports_multiple_conformers_without_geometry_guessing() -> None:
    source, variant, active_site = _single_model()
    state = structures.create_adsorption_state(
        variant, active_site, state_label="*H", adsorbates=("H",)
    )
    first_build = _single_h_build(source, active_site, height=1.4)
    second_build = _single_h_build(source, active_site, height=1.8)
    first = structures.state_conformer_from_adsorbate_build(
        state, active_site, first_build, name="H-low"
    )
    second = structures.state_conformer_from_adsorbate_build(
        state,
        active_site,
        second_build,
        name="H-high",
        existing_conformers=(first,),
    )

    structures.validate_conformer_collection(state, (first, second))
    assert first.id != second.id
    assert first.structure_snapshot_id != second.structure_snapshot_id
    assert structures.conformer_signature(first) != structures.conformer_signature(second)


def test_collection_rejects_exact_duplicate_names_ranks_and_parent_cycles() -> None:
    source, variant, active_site = _single_model()
    state = structures.create_adsorption_state(
        variant, active_site, state_label="*H", adsorbates=("H",)
    )
    build = _single_h_build(source, active_site, height=1.5)
    first = structures.state_conformer_from_adsorbate_build(
        state, active_site, build, name="H-a", rank=1
    )
    exact_duplicate = domain.StateConformer(
        adsorption_state_id=state.id,
        structure_snapshot_id=first.structure_snapshot_id,
        name="H-b",
        binding_mode=first.binding_mode,
        binding_edges=first.binding_edges,
        rank=2,
    )
    with pytest.raises(structures.StateConformerToolingError, match="exact duplicate"):
        structures.validate_conformer_collection(state, (first, exact_duplicate))

    second_build = _single_h_build(source, active_site, height=1.8)
    second = structures.state_conformer_from_adsorbate_build(
        state, active_site, second_build, name="H-b", rank=2
    )
    duplicate_name = replace(second, name="  h-A ")
    with pytest.raises(structures.StateConformerToolingError, match="names must be unique"):
        structures.validate_conformer_collection(state, (first, duplicate_name))
    duplicate_rank = replace(second, rank=1)
    with pytest.raises(structures.StateConformerToolingError, match="ranks must be unique"):
        structures.validate_conformer_collection(state, (first, duplicate_rank))

    first_cycle = replace(first, parent_conformer_id=second.id)
    second_cycle = replace(second, parent_conformer_id=first.id)
    with pytest.raises(structures.StateConformerToolingError, match="acyclic"):
        structures.validate_conformer_collection(state, (first_cycle, second_cycle))


def test_parent_conformer_requires_matching_snapshot_lineage() -> None:
    source, variant, active_site = _single_model()
    state = structures.create_adsorption_state(
        variant, active_site, state_label="*H", adsorbates=("H",)
    )
    parent_build = _single_h_build(source, active_site, height=1.4)
    parent = structures.state_conformer_from_adsorbate_build(
        state, active_site, parent_build, name="parent-H"
    )
    child_build = _single_h_build(parent_build.snapshot, active_site, height=1.8)
    child = structures.state_conformer_from_adsorbate_build(
        state,
        active_site,
        child_build,
        name="child-H",
        parent_conformer=parent,
        existing_conformers=(parent,),
    )

    assert child.parent_conformer_id == parent.id
    assert child_build.snapshot.parent_snapshot_id == parent.structure_snapshot_id

    unrelated_build = _single_h_build(source, active_site, height=2.0)
    with pytest.raises(structures.StateConformerToolingError, match="child snapshot lineage"):
        structures.state_conformer_from_adsorbate_build(
            state,
            active_site,
            unrelated_build,
            name="invalid-child",
            parent_conformer=parent,
            existing_conformers=(parent,),
        )


def test_pb2_opposite_side_cooh_runs_block5_through_block8_and_visualization_context() -> None:
    source, variant, active_site = _pb2_model()
    pb_top, pb_bottom = active_site.center_atom_uids
    build = _pb2_cooh_build(source, active_site)
    state = structures.create_adsorption_state(
        variant,
        active_site,
        state_label="*COOH",
        adsorbates=("COOH",),
        reaction_role="CO2RR_CO_intermediate",
    )

    conformer = structures.state_conformer_from_adsorbate_build(
        state,
        active_site,
        build,
        name="Pb2 multicenter COOH",
        orientation="dual-center tilted",
    )
    context = structures.resolve_conformer_visualization_context(
        state, active_site, conformer, build.snapshot
    )

    assert conformer.binding_mode is domain.BindingMode.MULTICENTER
    assert tuple(edge.site_atom_uid for edge in conformer.binding_edges) == (
        pb_top,
        pb_bottom,
    )
    assert tuple(edge.adsorbate_atom_uid for edge in conformer.binding_edges) == (
        build.atom_uid_for_key("C"),
        build.atom_uid_for_key("O_carbonyl"),
    )
    assert context.snapshot.id == build.snapshot.id
    assert context.state_label == "*COOH"
    assert context.conformer_name == "Pb2 multicenter COOH"
    assert context.active_center_atom_uids == (pb_top, pb_bottom)
    assert context.bound_adsorbate_atom_uids == (
        build.atom_uid_for_key("C"),
        build.atom_uid_for_key("O_carbonyl"),
    )
    assert tuple(label.side for label in context.side_labels) == (
        domain.SiteSide.TOP,
        domain.SiteSide.BOTTOM,
    )
    assert context.binding_edges == conformer.binding_edges


def test_same_snapshot_can_retain_distinct_binding_interpretations() -> None:
    source, variant, active_site = _pb2_model()
    build = _pb2_cooh_build(source, active_site)
    state = structures.create_adsorption_state(
        variant, active_site, state_label="*COOH", adsorbates=("COOH",)
    )
    canonical = structures.state_conformer_from_adsorbate_build(
        state, active_site, build, name="canonical"
    )
    pb_top, pb_bottom = active_site.center_atom_uids
    alternative = domain.StateConformer(
        adsorption_state_id=state.id,
        structure_snapshot_id=build.snapshot.id,
        name="alternative-binding",
        binding_mode=domain.BindingMode.MULTICENTER,
        binding_edges=(
            domain.BindingEdge(build.atom_uid_for_key("C"), pb_bottom),
            domain.BindingEdge(build.atom_uid_for_key("O_carbonyl"), pb_top),
        ),
    )

    structures.validate_conformer_collection(state, (canonical, alternative))
    assert structures.conformer_signature(canonical) != structures.conformer_signature(
        alternative
    )


def test_handoff_rejects_state_template_and_active_site_mismatch() -> None:
    source, variant, active_site = _single_model()
    build = _single_h_build(source, active_site, height=1.5)
    wrong_template_state = structures.create_adsorption_state(
        variant, active_site, state_label="*OH", adsorbates=("OH",)
    )
    with pytest.raises(structures.StateConformerToolingError, match="build-result template"):
        structures.state_conformer_from_adsorbate_build(
            wrong_template_state, active_site, build, name="wrong-template"
        )

    other_source, other_variant, other_site = _single_model()
    other_state = structures.create_adsorption_state(
        other_variant, other_site, state_label="*H", adsorbates=("H",)
    )
    other_build = _single_h_build(other_source, other_site, height=1.5)
    with pytest.raises(structures.StateConformerToolingError, match="same StructureVariant"):
        structures.state_conformer_from_adsorbate_build(
            other_state, active_site, other_build, name="wrong-site"
        )

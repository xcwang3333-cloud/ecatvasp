from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

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
from ecatvasp.structures import (
    AdsorbateBuilderError,
    AdsorbateContactSpec,
    AdsorbatePlacementSpec,
    AdsorbateTemplateError,
    build_adsorbate,
    export_structure,
    get_adsorbate_template,
    import_structure,
    list_adsorbate_templates,
)


def _lattice() -> Lattice:
    return Lattice(
        vectors=((10.0, 0.0, 0.0), (0.0, 10.0, 0.0), (0.0, 0.0, 20.0))
    )


def _variant(snapshot: StructureSnapshot) -> StructureVariant:
    project = Project(name="Adsorbate builder", slug="adsorbate-builder")
    catalyst = Catalyst(project_id=project.id, name="model", slug="model")
    return StructureVariant(
        catalyst_id=catalyst.id,
        name="adsorption model",
        variant_type=VariantType.SITE_TOPOLOGY,
        current_structure_snapshot_id=snapshot.id,
    )


def _single_center_model() -> tuple[StructureSnapshot, StructureVariant, ActiveSite]:
    iron = new_atom_uid()
    snapshot = StructureSnapshot(
        lattice=_lattice(),
        sites=(
            StructureSite(new_atom_uid(), "C", (0.30, 0.30, 0.50)),
            StructureSite(new_atom_uid(), "N", (0.40, 0.50, 0.50)),
            StructureSite(iron, "Fe", (0.50, 0.50, 0.50)),
        ),
        label="Fe-NC",
        origin=StructureOrigin.BUILT,
    )
    variant = _variant(snapshot)
    active_site = ActiveSite(
        structure_variant_id=variant.id,
        center_atom_uids=(iron,),
        topology="single-center",
        side_labels=(SideLabel(iron, SiteSide.TOP),),
    )
    return snapshot, variant, active_site


def _dual_top_model() -> tuple[StructureSnapshot, ActiveSite]:
    iron = new_atom_uid()
    cobalt = new_atom_uid()
    snapshot = StructureSnapshot(
        lattice=_lattice(),
        sites=(
            StructureSite(new_atom_uid(), "N", (0.35, 0.50, 0.50)),
            StructureSite(iron, "Fe", (0.45, 0.50, 0.50)),
            StructureSite(cobalt, "Co", (0.55, 0.50, 0.50)),
        ),
        origin=StructureOrigin.BUILT,
    )
    variant = _variant(snapshot)
    active_site = ActiveSite(
        structure_variant_id=variant.id,
        center_atom_uids=(iron, cobalt),
        topology="same-side",
        side_labels=(
            SideLabel(iron, SiteSide.TOP),
            SideLabel(cobalt, SiteSide.TOP),
        ),
    )
    return snapshot, active_site


def _pb2_model() -> tuple[StructureSnapshot, StructureVariant, ActiveSite]:
    pb_top = new_atom_uid()
    pb_bottom = new_atom_uid()
    snapshot = StructureSnapshot(
        lattice=_lattice(),
        sites=(
            StructureSite(new_atom_uid(), "C", (0.50, 0.50, 0.50)),
            StructureSite(pb_top, "Pb", (0.45, 0.50, 0.60)),
            StructureSite(pb_bottom, "Pb", (0.55, 0.50, 0.40)),
        ),
        label="Pb2 opposite-side",
        origin=StructureOrigin.BUILT,
    )
    variant = _variant(snapshot)
    active_site = ActiveSite(
        structure_variant_id=variant.id,
        center_atom_uids=(pb_top, pb_bottom),
        topology="opposite-side",
        side_labels=(
            SideLabel(pb_top, SiteSide.TOP),
            SideLabel(pb_bottom, SiteSide.BOTTOM),
        ),
    )
    return snapshot, variant, active_site


def test_builtin_adsorbate_library_contains_nine_electrocatalysis_templates() -> None:
    templates = list_adsorbate_templates()

    assert tuple(template.key for template in templates) == (
        "H",
        "O",
        "OH",
        "OOH",
        "O2",
        "CO2",
        "COOH",
        "CO",
        "OCHO",
    )
    assert len(templates) == 9
    assert get_adsorbate_template("cooh").key == "COOH"
    assert get_adsorbate_template("COOH").atom_keys == (
        "C",
        "O_carbonyl",
        "O_hydroxyl",
        "H",
    )
    assert all(
        len(template.atom_keys) == len(set(template.atom_keys)) for template in templates
    )
    assert {family for template in templates for family in template.reaction_families} == {
        "HER",
        "ORR",
        "OER",
        "CO2RR",
    }


def test_adsorption_state_star_is_not_part_of_template_key() -> None:
    with pytest.raises(AdsorbateTemplateError, match="must not start"):
        get_adsorbate_template("*COOH")


def test_single_center_h_preserves_source_identity_and_uses_fresh_uid() -> None:
    source, _, active_site = _single_center_model()
    iron = active_site.center_atom_uids[0]
    spec = AdsorbatePlacementSpec(
        template_key="H",
        target_center_atom_uids=(iron,),
        binding_mode=BindingMode.SINGLE_CENTER,
        height_angstrom=1.5,
        contacts=(AdsorbateContactSpec("H", iron),),
    )

    first = build_adsorbate(source, active_site, spec)
    second = build_adsorbate(source, active_site, spec)

    assert first.snapshot.parent_snapshot_id == source.id
    assert first.snapshot.origin is StructureOrigin.EDITED
    assert first.snapshot.sites[: len(source.sites)] == source.sites
    assert first.addition.preserved_atom_uids == tuple(site.atom_uid for site in source.sites)
    assert first.addition.added_atom_uids == first.adsorbate_atom_uids
    assert first.adsorbate_atom_uids[0] not in {site.atom_uid for site in source.sites}
    assert first.adsorbate_atom_uids != second.adsorbate_atom_uids
    assert first.atom_uid_for_key("H") == first.primary_anchor_atom_uid
    assert first.contacts[0].adsorbate_atom_uid == first.primary_anchor_atom_uid
    assert first.contacts[0].site_atom_uid == iron

    fe_z = source.sites[2].fractional_coords[2]
    h_z = first.snapshot.sites[-1].fractional_coords[2]
    assert h_z == pytest.approx(fe_z + 1.5 / 20.0)


def test_oh_default_orientation_aligns_anchor_reference_with_auto_direction() -> None:
    source, _, active_site = _single_center_model()
    iron = active_site.center_atom_uids[0]
    result = build_adsorbate(
        source,
        active_site,
        AdsorbatePlacementSpec(
            template_key="OH",
            target_center_atom_uids=(iron,),
            binding_mode=BindingMode.SINGLE_CENTER,
            height_angstrom=1.8,
            contacts=(AdsorbateContactSpec("O", iron),),
        ),
    )

    added = result.snapshot.sites[len(source.sites) :]
    oxygen, hydrogen = added
    assert oxygen.element == "O"
    assert hydrogen.element == "H"
    assert hydrogen.fractional_coords[2] > oxygen.fractional_coords[2]
    assert result.orientation_vector_cartesian == pytest.approx((0.0, 0.0, 1.0))
    assert result.placement_direction_cartesian == pytest.approx((0.0, 0.0, 1.0))


def test_co_accepts_explicit_orientation_without_changing_placement_direction() -> None:
    source, _, active_site = _single_center_model()
    iron = active_site.center_atom_uids[0]
    result = build_adsorbate(
        source,
        active_site,
        AdsorbatePlacementSpec(
            template_key="CO",
            target_center_atom_uids=(iron,),
            binding_mode=BindingMode.SINGLE_CENTER,
            height_angstrom=1.7,
            contacts=(AdsorbateContactSpec("C", iron),),
            orientation_vector_cartesian=(1.0, 0.0, 0.0),
        ),
    )

    carbon = result.snapshot.sites[-2]
    oxygen = result.snapshot.sites[-1]
    assert oxygen.fractional_coords[0] > carbon.fractional_coords[0]
    assert oxygen.fractional_coords[2] == pytest.approx(carbon.fractional_coords[2])
    assert result.orientation_vector_cartesian == pytest.approx((1.0, 0.0, 0.0))
    assert result.placement_direction_cartesian == pytest.approx((0.0, 0.0, 1.0))


def test_bridge_intent_uses_one_adsorbate_atom_for_two_ordered_centers() -> None:
    source, active_site = _dual_top_model()
    iron, cobalt = active_site.center_atom_uids
    result = build_adsorbate(
        source,
        active_site,
        AdsorbatePlacementSpec(
            template_key="O",
            target_center_atom_uids=(iron, cobalt),
            binding_mode=BindingMode.BRIDGE,
            height_angstrom=1.6,
            contacts=(
                AdsorbateContactSpec("O", iron),
                AdsorbateContactSpec("O", cobalt),
            ),
        ),
    )

    assert result.binding_mode_intent is BindingMode.BRIDGE
    assert result.target_center_atom_uids == (iron, cobalt)
    assert {contact.adsorbate_atom_uid for contact in result.contacts} == {
        result.primary_anchor_atom_uid
    }
    bridge_o = result.snapshot.sites[-1]
    assert bridge_o.fractional_coords[0] == pytest.approx(0.50)


def test_pb2_opposite_side_multicenter_cooh_requires_explicit_direction() -> None:
    source, _, active_site = _pb2_model()
    pb_top, pb_bottom = active_site.center_atom_uids
    common = dict(
        template_key="COOH",
        target_center_atom_uids=(pb_top, pb_bottom),
        binding_mode=BindingMode.MULTICENTER,
        height_angstrom=2.0,
        contacts=(
            AdsorbateContactSpec("C", pb_top),
            AdsorbateContactSpec("O_carbonyl", pb_bottom),
        ),
    )

    with pytest.raises(AdsorbateBuilderError, match="AUTO placement direction is ambiguous"):
        build_adsorbate(source, active_site, AdsorbatePlacementSpec(**common))

    result = build_adsorbate(
        source,
        active_site,
        AdsorbatePlacementSpec(
            **common,
            placement_direction_cartesian=(0.0, 0.0, 2.0),
            orientation_vector_cartesian=(1.0, 0.0, 0.0),
        ),
    )

    assert result.template_key == "COOH"
    assert result.binding_mode_intent is BindingMode.MULTICENTER
    assert result.target_center_atom_uids == (pb_top, pb_bottom)
    assert tuple(atom.atom_key for atom in result.adsorbate_atoms) == (
        "C",
        "O_carbonyl",
        "O_hydroxyl",
        "H",
    )
    assert tuple(contact.adsorbate_atom_key for contact in result.contacts) == (
        "C",
        "O_carbonyl",
    )
    assert tuple(contact.site_atom_uid for contact in result.contacts) == (
        pb_top,
        pb_bottom,
    )
    assert result.placement_direction_cartesian == pytest.approx((0.0, 0.0, 1.0))


def test_pb2_multicenter_cooh_handoff_builds_valid_block8_domain_objects() -> None:
    source, variant, active_site = _pb2_model()
    pb_top, pb_bottom = active_site.center_atom_uids
    result = build_adsorbate(
        source,
        active_site,
        AdsorbatePlacementSpec(
            template_key="COOH",
            target_center_atom_uids=(pb_top, pb_bottom),
            binding_mode=BindingMode.MULTICENTER,
            height_angstrom=2.0,
            contacts=(
                AdsorbateContactSpec("C", pb_top),
                AdsorbateContactSpec("O_carbonyl", pb_bottom),
            ),
            placement_direction_cartesian=(0.0, 0.0, 1.0),
            orientation_vector_cartesian=(1.0, 0.0, 0.0),
        ),
    )

    state = AdsorptionState(
        structure_variant_id=variant.id,
        active_site_id=active_site.id,
        state_label="*COOH",
        adsorbates=(result.template_key,),
        reaction_role="CO2RR_CO_intermediate",
    )
    conformer = StateConformer(
        adsorption_state_id=state.id,
        structure_snapshot_id=result.snapshot.id,
        name="Pb2 multicenter COOH",
        binding_mode=result.binding_mode_intent,
        binding_edges=tuple(
            BindingEdge(
                adsorbate_atom_uid=contact.adsorbate_atom_uid,
                site_atom_uid=contact.site_atom_uid,
                label=f"{contact.adsorbate_atom_key}-site",
            )
            for contact in result.contacts
        ),
    )

    validate_conformer_context(
        active_site=active_site,
        state=state,
        conformer=conformer,
        snapshot=result.snapshot,
    )
    assert {edge.site_atom_uid for edge in conformer.binding_edges} == {
        pb_top,
        pb_bottom,
    }
    assert {edge.adsorbate_atom_uid for edge in conformer.binding_edges}.issubset(
        set(result.adsorbate_atom_uids)
    )


def test_poscar_round_trip_preserves_support_metal_and_adsorbate_uids(tmp_path: Path) -> None:
    source, _, active_site = _single_center_model()
    iron = active_site.center_atom_uids[0]
    result = build_adsorbate(
        source,
        active_site,
        AdsorbatePlacementSpec(
            template_key="COOH",
            target_center_atom_uids=(iron,),
            binding_mode=BindingMode.SINGLE_CENTER,
            height_angstrom=2.0,
            contacts=(AdsorbateContactSpec("C", iron),),
            orientation_vector_cartesian=(1.0, 0.0, 0.0),
        ),
    )
    target = tmp_path / "POSCAR"

    export_structure(result.snapshot, target)
    reimported = import_structure(target)

    expected_uids = {site.atom_uid for site in result.snapshot.sites}
    actual_uids = {site.atom_uid for site in reimported.snapshot.sites}
    assert actual_uids == expected_uids
    assert set(result.adsorbate_atom_uids).issubset(actual_uids)
    assert {site.atom_uid for site in source.sites}.issubset(actual_uids)


def test_targeting_contact_and_orientation_errors_fail_closed() -> None:
    source, active_site = _dual_top_model()
    iron, cobalt = active_site.center_atom_uids

    with pytest.raises(AdsorbateBuilderError, match="preserve ActiveSite"):
        build_adsorbate(
            source,
            active_site,
            AdsorbatePlacementSpec(
                template_key="O",
                target_center_atom_uids=(cobalt, iron),
                binding_mode=BindingMode.BRIDGE,
                height_angstrom=1.5,
                contacts=(
                    AdsorbateContactSpec("O", cobalt),
                    AdsorbateContactSpec("O", iron),
                ),
            ),
        )

    with pytest.raises(AdsorbateBuilderError, match="one adsorbate atom"):
        build_adsorbate(
            source,
            active_site,
            AdsorbatePlacementSpec(
                template_key="O2",
                target_center_atom_uids=(iron, cobalt),
                binding_mode=BindingMode.BRIDGE,
                height_angstrom=1.5,
                contacts=(
                    AdsorbateContactSpec("O1", iron),
                    AdsorbateContactSpec("O2", cobalt),
                ),
            ),
        )

    with pytest.raises(ValueError, match="nonzero magnitude"):
        AdsorbatePlacementSpec(
            template_key="O",
            target_center_atom_uids=(iron,),
            binding_mode=BindingMode.SINGLE_CENTER,
            height_angstrom=1.5,
            contacts=(AdsorbateContactSpec("O", iron),),
            placement_direction_cartesian=(0.0, 0.0, 0.0),
        )


def test_multicenter_requires_distinct_adsorbate_contact_atoms() -> None:
    source, active_site = _dual_top_model()
    iron, cobalt = active_site.center_atom_uids

    with pytest.raises(AdsorbateBuilderError, match="distinct adsorbate contact atoms"):
        build_adsorbate(
            source,
            active_site,
            AdsorbatePlacementSpec(
                template_key="O",
                target_center_atom_uids=(iron, cobalt),
                binding_mode=BindingMode.MULTICENTER,
                height_angstrom=1.5,
                contacts=(
                    AdsorbateContactSpec("O", iron),
                    AdsorbateContactSpec("O", cobalt),
                ),
            ),
        )


def test_single_atom_adsorbate_rejects_orientation_settings() -> None:
    source, _, active_site = _single_center_model()
    iron = active_site.center_atom_uids[0]

    with pytest.raises(AdsorbateBuilderError, match="single-atom adsorbates"):
        build_adsorbate(
            source,
            active_site,
            AdsorbatePlacementSpec(
                template_key="H",
                target_center_atom_uids=(iron,),
                binding_mode=BindingMode.SINGLE_CENTER,
                height_angstrom=1.5,
                contacts=(AdsorbateContactSpec("H", iron),),
                orientation_vector_cartesian=(1.0, 0.0, 0.0),
            ),
        )


def test_adsorbate_result_rejects_corrupt_contact_provenance() -> None:
    source, active_site = _dual_top_model()
    iron, cobalt = active_site.center_atom_uids
    result = build_adsorbate(
        source,
        active_site,
        AdsorbatePlacementSpec(
            template_key="O",
            target_center_atom_uids=(iron, cobalt),
            binding_mode=BindingMode.BRIDGE,
            height_angstrom=1.5,
            contacts=(
                AdsorbateContactSpec("O", iron),
                AdsorbateContactSpec("O", cobalt),
            ),
        ),
    )

    with pytest.raises(ValueError, match="cover exactly"):
        replace(result, contacts=(result.contacts[0],))
    with pytest.raises(ValueError, match="must be unique"):
        replace(result, contacts=result.contacts + (result.contacts[0],))
    with pytest.raises(ValueError, match="preserved source atoms"):
        replace(result, target_center_atom_uids=(result.adsorbate_atom_uids[0],))

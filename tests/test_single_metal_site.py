from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from ecatvasp import domain, structures


def _snapshot(
    elements: tuple[str, ...],
    fractional_coords: tuple[tuple[float, float, float], ...],
    *,
    lattice: domain.Lattice | None = None,
    periodic: tuple[bool, bool, bool] = (True, True, True),
) -> domain.StructureSnapshot:
    if lattice is None:
        lattice = domain.Lattice(
            vectors=(
                (10.0, 0.0, 0.0),
                (0.0, 10.0, 0.0),
                (0.0, 0.0, 20.0),
            )
        )
    sites = tuple(
        domain.StructureSite(
            atom_uid=domain.new_atom_uid(),
            element=element,
            fractional_coords=coords,
        )
        for element, coords in zip(elements, fractional_coords, strict=True)
    )
    return domain.StructureSnapshot(
        lattice=lattice,
        sites=sites,
        origin=domain.StructureOrigin.BUILT,
        periodic=periodic,
    )


def _sites_by_uid(
    snapshot: domain.StructureSnapshot,
) -> dict[domain.AtomUid, domain.StructureSite]:
    return {site.atom_uid: site for site in snapshot.sites}


def test_single_metal_addition_is_immutable_and_uses_fresh_identity() -> None:
    source = structures.build_graphene(structures.GrapheneBuildSpec(nx=2, ny=2))
    original_sites = source.sites
    anchors = (source.sites[0].atom_uid, source.sites[1].atom_uid)
    spec = structures.SingleMetalSiteSpec(
        metal_element="fe",
        coordination_atom_uids=anchors,
        side=domain.SiteSide.TOP,
        height_angstrom=1.8,
        label="Fe-C2",
    )

    result = structures.build_single_metal_site(source, spec)
    child = result.snapshot

    assert source.sites == original_sites
    assert source.origin is domain.StructureOrigin.BUILT
    assert source.parent_snapshot_id is None
    assert child.origin is domain.StructureOrigin.EDITED
    assert child.parent_snapshot_id == source.id
    assert child.label == "Fe-C2"
    assert child.sites[: len(source.sites)] == source.sites
    assert len(child.sites) == len(source.sites) + 1
    assert result.metal_atom_uid not in {site.atom_uid for site in source.sites}
    assert child.sites[-1].atom_uid == result.metal_atom_uid
    assert child.sites[-1].element == "Fe"

    assert result.addition.preserved_atom_uids == tuple(site.atom_uid for site in source.sites)
    assert result.addition.added_atom_uids == (result.metal_atom_uid,)
    assert result.addition.lineage[-1].action is structures.AtomAdditionAction.ADDED
    assert all(
        event.action is structures.AtomAdditionAction.PRESERVED
        for event in result.addition.lineage[:-1]
    )


def test_pbc_aware_centroid_places_top_and_bottom_across_cell_boundary() -> None:
    source = _snapshot(
        ("N", "N"),
        (
            (0.95, 0.50, 0.50),
            (0.05, 0.50, 0.50),
        ),
    )
    anchors = tuple(site.atom_uid for site in source.sites)

    top = structures.build_single_metal_site(
        source,
        structures.SingleMetalSiteSpec(
            metal_element="Co",
            coordination_atom_uids=anchors,
            side=domain.SiteSide.TOP,
            height_angstrom=2.0,
        ),
    )
    bottom = structures.build_single_metal_site(
        source,
        structures.SingleMetalSiteSpec(
            metal_element="Co",
            coordination_atom_uids=anchors,
            side=domain.SiteSide.BOTTOM,
            height_angstrom=2.0,
        ),
    )

    assert top.snapshot.sites[-1].fractional_coords == pytest.approx((0.0, 0.5, 0.6))
    assert bottom.snapshot.sites[-1].fractional_coords == pytest.approx((0.0, 0.5, 0.4))


def test_slab_normal_not_fractional_z_controls_side_for_tilted_cell() -> None:
    lattice = domain.Lattice(
        vectors=(
            (2.0, 0.0, 0.0),
            (0.0, 2.0, 2.0),
            (0.0, 0.0, 10.0),
        )
    )
    source = _snapshot(
        ("N", "N"),
        ((0.25, 0.25, 0.50), (0.75, 0.25, 0.50)),
        lattice=lattice,
    )
    anchors = tuple(site.atom_uid for site in source.sites)
    result = structures.build_single_metal_site(
        source,
        structures.SingleMetalSiteSpec(
            metal_element="Pb",
            coordination_atom_uids=anchors,
            side=domain.SiteSide.TOP,
            height_angstrom=1.0,
        ),
    )

    metal = result.snapshot.sites[-1]
    assert metal.fractional_coords == pytest.approx(
        (0.5, 0.8964466094067263, 0.6414213562373094)
    )


def test_coordination_signature_records_explicit_builder_intent() -> None:
    source = _snapshot(
        ("N", "N", "N", "C"),
        (
            (0.40, 0.40, 0.50),
            (0.60, 0.40, 0.50),
            (0.60, 0.60, 0.50),
            (0.40, 0.60, 0.50),
        ),
    )
    anchors = tuple(site.atom_uid for site in source.sites)
    result = structures.build_single_metal_site(
        source,
        structures.SingleMetalSiteSpec(
            metal_element="Al",
            coordination_atom_uids=anchors,
            side=domain.SiteSide.TOP,
            height_angstrom=1.5,
        ),
    )

    assert result.coordination_atom_uids == anchors
    assert result.coordination_elements == ("N", "N", "N", "C")
    assert result.coordination_signature == "N3C"


def test_single_metal_spec_and_build_fail_closed_for_invalid_requests() -> None:
    source = _snapshot(("N", "N"), ((0.4, 0.5, 0.5), (0.6, 0.5, 0.5)))
    first_uid = source.sites[0].atom_uid

    with pytest.raises(structures.SingleMetalSiteError, match="metal_element"):
        structures.SingleMetalSiteSpec(
            metal_element="Xx",
            coordination_atom_uids=(first_uid,),
            side=domain.SiteSide.TOP,
            height_angstrom=1.0,
        )
    with pytest.raises(structures.SingleMetalSiteError, match="metal_element"):
        structures.SingleMetalSiteSpec(
            metal_element="C",
            coordination_atom_uids=(first_uid,),
            side=domain.SiteSide.TOP,
            height_angstrom=1.0,
        )
    with pytest.raises(structures.SingleMetalSiteError, match="must be unique"):
        structures.SingleMetalSiteSpec(
            metal_element="Fe",
            coordination_atom_uids=(first_uid, first_uid),
            side=domain.SiteSide.TOP,
            height_angstrom=1.0,
        )
    with pytest.raises(structures.SingleMetalSiteError, match="explicit side"):
        structures.SingleMetalSiteSpec(
            metal_element="Fe",
            coordination_atom_uids=(first_uid,),
            side=domain.SiteSide.UNSPECIFIED,
            height_angstrom=1.0,
        )
    with pytest.raises(structures.SingleMetalSiteError, match="positive height"):
        structures.SingleMetalSiteSpec(
            metal_element="Fe",
            coordination_atom_uids=(first_uid,),
            side=domain.SiteSide.TOP,
            height_angstrom=0.0,
        )
    with pytest.raises(structures.SingleMetalSiteError, match="zero height"):
        structures.SingleMetalSiteSpec(
            metal_element="Fe",
            coordination_atom_uids=(first_uid,),
            side=domain.SiteSide.IN_PLANE,
            height_angstrom=1.0,
        )
    with pytest.raises(structures.SingleMetalSiteError, match="finite number"):
        structures.SingleMetalSiteSpec(
            metal_element="Fe",
            coordination_atom_uids=(first_uid,),
            side=domain.SiteSide.TOP,
            height_angstrom=float("nan"),
        )

    missing_uid = domain.new_atom_uid()
    with pytest.raises(structures.SingleMetalSiteError, match="must exist"):
        structures.build_single_metal_site(
            source,
            structures.SingleMetalSiteSpec(
                metal_element="Fe",
                coordination_atom_uids=(missing_uid,),
                side=domain.SiteSide.TOP,
                height_angstrom=1.0,
            ),
        )


def test_in_plane_single_anchor_collision_fails_closed() -> None:
    source = _snapshot(("N",), ((0.5, 0.5, 0.5),))

    with pytest.raises(structures.SingleMetalSiteError, match="overlaps"):
        structures.build_single_metal_site(
            source,
            structures.SingleMetalSiteSpec(
                metal_element="Fe",
                coordination_atom_uids=(source.sites[0].atom_uid,),
                side=domain.SiteSide.IN_PLANE,
                height_angstrom=0.0,
            ),
        )


def test_degenerate_slab_normal_fails_closed() -> None:
    lattice = domain.Lattice(
        vectors=(
            (1.0, 0.0, 0.0),
            (2.0, 0.0, 0.0),
            (0.0, 0.0, 10.0),
        )
    )
    source = _snapshot(("N",), ((0.5, 0.5, 0.5),), lattice=lattice)

    with pytest.raises(structures.SingleMetalSiteError, match="define a plane"):
        structures.build_single_metal_site(
            source,
            structures.SingleMetalSiteSpec(
                metal_element="Fe",
                coordination_atom_uids=(source.sites[0].atom_uid,),
                side=domain.SiteSide.TOP,
                height_angstrom=1.0,
            ),
        )


def test_append_only_lineage_rejects_reused_uid_and_corrupt_target_order() -> None:
    source = structures.build_graphene(structures.GrapheneBuildSpec(nx=1, ny=1))
    reused = domain.StructureSite(
        atom_uid=source.sites[0].atom_uid,
        element="Fe",
        fractional_coords=(0.5, 0.5, 0.6),
    )
    with pytest.raises(structures.StructureAdditionError, match="fresh atom_uids"):
        structures.append_structure_sites(source, (reused,))

    fresh = domain.StructureSite(
        atom_uid=domain.new_atom_uid(),
        element="Fe",
        fractional_coords=(0.5, 0.5, 0.6),
    )
    valid = structures.append_structure_sites(source, (fresh,))
    corrupt_last = replace(valid.lineage[-1], target_index=0)
    with pytest.raises(ValueError, match="target snapshot order"):
        structures.StructureAdditionResult(
            source_snapshot_id=valid.source_snapshot_id,
            source_atom_count=valid.source_atom_count,
            snapshot=valid.snapshot,
            lineage=(*valid.lineage[:-1], corrupt_last),
        )


def test_single_metal_poscar_round_trip_preserves_all_atom_identities(tmp_path: Path) -> None:
    pristine = structures.build_graphene(
        structures.GrapheneBuildSpec(nx=3, ny=2, vacuum_gap_angstrom=20.0)
    )
    doped = structures.substitute_dopants(
        pristine,
        (structures.DopantSubstitution(pristine.sites[2].atom_uid, "N"),),
        label="N-graphene",
    ).snapshot
    nitrogen = next(site for site in doped.sites if site.element == "N")
    carbon = next(site for site in doped.sites if site.element == "C")

    result = structures.build_single_metal_site(
        doped,
        structures.SingleMetalSiteSpec(
            metal_element="Fe",
            coordination_atom_uids=(nitrogen.atom_uid, carbon.atom_uid),
            side=domain.SiteSide.TOP,
            height_angstrom=1.7,
            label="Fe-NC",
        ),
    )
    target = tmp_path / "POSCAR"

    structures.export_structure(result.snapshot, target)
    restored = structures.import_structure(target)

    assert restored.metadata.identity_status is structures.AtomIdentityStatus.PRESERVED_SIDECAR
    expected_by_uid = _sites_by_uid(result.snapshot)
    actual_by_uid = _sites_by_uid(restored.snapshot)
    assert set(actual_by_uid) == set(expected_by_uid)
    assert result.metal_atom_uid in actual_by_uid
    assert actual_by_uid[result.metal_atom_uid].element == "Fe"
    for atom_uid, expected in expected_by_uid.items():
        actual = actual_by_uid[atom_uid]
        assert actual.element == expected.element
        assert actual.fractional_coords == pytest.approx(expected.fractional_coords)

from __future__ import annotations

from pathlib import Path

import pytest

from ecatvasp import domain, structures


def _snapshot(
    elements: tuple[str, ...],
    fractional_coords: tuple[tuple[float, float, float], ...],
) -> domain.StructureSnapshot:
    lattice = domain.Lattice(
        vectors=(
            (10.0, 0.0, 0.0),
            (0.0, 10.0, 0.0),
            (0.0, 0.0, 20.0),
        )
    )
    return domain.StructureSnapshot(
        lattice=lattice,
        sites=tuple(
            domain.StructureSite(
                atom_uid=domain.new_atom_uid(),
                element=element,
                fractional_coords=coords,
            )
            for element, coords in zip(elements, fractional_coords, strict=True)
        ),
        origin=domain.StructureOrigin.BUILT,
        periodic=(True, True, True),
    )


def _sites_by_uid(
    snapshot: domain.StructureSnapshot,
) -> dict[domain.AtomUid, domain.StructureSite]:
    return {site.atom_uid: site for site in snapshot.sites}


def test_pb2_opposite_side_uses_one_revision_and_shared_coordination() -> None:
    source = _snapshot(
        ("N", "N", "C", "C"),
        (
            (0.45, 0.50, 0.50),
            (0.55, 0.50, 0.50),
            (0.50, 0.45, 0.50),
            (0.50, 0.55, 0.50),
        ),
    )
    anchors = tuple(site.atom_uid for site in source.sites[:2])
    original_sites = source.sites
    spec = structures.MultiMetalSiteSpec(
        centers=(
            structures.MultiMetalCenterSpec(
                metal_element="Pb",
                coordination_atom_uids=anchors,
                side=domain.SiteSide.TOP,
                height_angstrom=1.6,
            ),
            structures.MultiMetalCenterSpec(
                metal_element="pb",
                coordination_atom_uids=anchors,
                side=domain.SiteSide.BOTTOM,
                height_angstrom=1.6,
            ),
        ),
        metal_metal_topology_intent="opposite-side-pair",
        label="Pb2-opposite-side",
    )

    result = structures.build_multi_metal_site(source, spec)

    assert source.sites == original_sites
    assert source.parent_snapshot_id is None
    assert result.snapshot.origin is domain.StructureOrigin.EDITED
    assert result.snapshot.parent_snapshot_id == source.id
    assert result.snapshot.label == "Pb2-opposite-side"
    assert result.snapshot.sites[: len(source.sites)] == source.sites
    assert len(result.snapshot.sites) == len(source.sites) + 2
    assert result.addition.added_atom_uids == result.metal_atom_uids
    assert len(set(result.metal_atom_uids)) == 2
    assert not set(result.metal_atom_uids) & {site.atom_uid for site in source.sites}
    assert tuple(center.metal_element for center in result.centers) == ("Pb", "Pb")
    assert result.side_topology is structures.EnsembleSideTopology.OPPOSITE_SIDE
    assert result.shared_coordination_atom_uids == anchors
    assert tuple(center.coordination_signature for center in result.centers) == ("N2", "N2")
    assert result.metal_metal_topology_intent == "opposite-side-pair"
    assert len(result.pair_distances) == 1
    assert result.pair_distances[0].distance_angstrom == pytest.approx(3.2)


def test_dual_same_side_supports_independent_coordination() -> None:
    source = _snapshot(
        ("N", "N", "N", "N"),
        (
            (0.25, 0.25, 0.50),
            (0.35, 0.25, 0.50),
            (0.65, 0.75, 0.50),
            (0.75, 0.75, 0.50),
        ),
    )
    first = tuple(site.atom_uid for site in source.sites[:2])
    second = tuple(site.atom_uid for site in source.sites[2:])

    result = structures.build_multi_metal_site(
        source,
        structures.MultiMetalSiteSpec(
            centers=(
                structures.MultiMetalCenterSpec("Fe", first, domain.SiteSide.TOP, 1.8),
                structures.MultiMetalCenterSpec("Co", second, domain.SiteSide.TOP, 1.7),
            ),
            metal_metal_topology_intent="proximal",
        ),
    )

    assert result.side_topology is structures.EnsembleSideTopology.SAME_SIDE
    assert result.shared_coordination_atom_uids == ()
    assert len(result.pair_distances) == 1
    assert result.pair_distances[0].distance_angstrom > 0.0
    assert tuple(center.coordination_signature for center in result.centers) == ("N2", "N2")


def test_triple_metal_tracks_partial_shared_coordination_and_all_pair_distances() -> None:
    source = _snapshot(
        ("N", "N", "C", "O", "C"),
        (
            (0.25, 0.25, 0.50),
            (0.40, 0.25, 0.50),
            (0.55, 0.45, 0.50),
            (0.70, 0.65, 0.50),
            (0.85, 0.65, 0.50),
        ),
    )
    uid = tuple(site.atom_uid for site in source.sites)

    result = structures.build_multi_metal_site(
        source,
        structures.MultiMetalSiteSpec(
            centers=(
                structures.MultiMetalCenterSpec(
                    "Fe", (uid[0], uid[1]), domain.SiteSide.TOP, 1.6
                ),
                structures.MultiMetalCenterSpec(
                    "Co", (uid[1], uid[2]), domain.SiteSide.BOTTOM, 1.5
                ),
                structures.MultiMetalCenterSpec(
                    "Ni", (uid[3], uid[4]), domain.SiteSide.TOP, 1.7
                ),
            ),
            metal_metal_topology_intent="compact-ensemble",
            label="FeCoNi",
        ),
    )

    assert len(result.centers) == 3
    assert len(set(result.metal_atom_uids)) == 3
    assert result.shared_coordination_atom_uids == (uid[1],)
    assert result.side_topology is structures.EnsembleSideTopology.OPPOSITE_SIDE
    assert tuple(center.coordination_signature for center in result.centers) == (
        "N2",
        "NC",
        "OC",
    )
    assert len(result.pair_distances) == 3
    pair_uid_sets = {
        frozenset((pair.left_atom_uid, pair.right_atom_uid)) for pair in result.pair_distances
    }
    assert len(pair_uid_sets) == 3
    assert all(pair.distance_angstrom > 0.0 for pair in result.pair_distances)


def test_side_topology_is_derived_from_center_sides() -> None:
    derive = structures.derive_side_topology

    assert derive((domain.SiteSide.TOP, domain.SiteSide.TOP)) is (
        structures.EnsembleSideTopology.SAME_SIDE
    )
    assert derive((domain.SiteSide.BOTTOM, domain.SiteSide.BOTTOM)) is (
        structures.EnsembleSideTopology.SAME_SIDE
    )
    assert derive((domain.SiteSide.TOP, domain.SiteSide.BOTTOM)) is (
        structures.EnsembleSideTopology.OPPOSITE_SIDE
    )
    assert derive((domain.SiteSide.IN_PLANE, domain.SiteSide.IN_PLANE)) is (
        structures.EnsembleSideTopology.IN_PLANE
    )
    assert derive((domain.SiteSide.TOP, domain.SiteSide.IN_PLANE)) is (
        structures.EnsembleSideTopology.MIXED
    )


def test_multi_metal_fails_closed_for_invalid_nuclearity_and_requests() -> None:
    source = _snapshot(("N", "N"), ((0.4, 0.5, 0.5), (0.6, 0.5, 0.5)))
    uid = tuple(site.atom_uid for site in source.sites)
    center = structures.MultiMetalCenterSpec("Fe", uid, domain.SiteSide.TOP, 1.5)

    with pytest.raises(structures.MultiMetalSiteError, match="exactly two or three"):
        structures.MultiMetalSiteSpec(centers=(center,))
    with pytest.raises(structures.MultiMetalSiteError, match="exactly two or three"):
        structures.MultiMetalSiteSpec(centers=(center, center, center, center))
    with pytest.raises(structures.MultiMetalSiteError, match="topology_intent"):
        structures.MultiMetalSiteSpec(centers=(center, center), metal_metal_topology_intent="  ")
    with pytest.raises(structures.MultiMetalSiteError, match="recognized metallic"):
        structures.MultiMetalCenterSpec("C", uid, domain.SiteSide.TOP, 1.5)
    with pytest.raises(structures.MultiMetalSiteError, match="explicit side"):
        structures.MultiMetalCenterSpec("Fe", uid, domain.SiteSide.UNSPECIFIED, 1.5)

    missing = domain.new_atom_uid()
    with pytest.raises(structures.MultiMetalSiteError, match="must exist"):
        structures.build_multi_metal_site(
            source,
            structures.MultiMetalSiteSpec(
                centers=(
                    center,
                    structures.MultiMetalCenterSpec(
                        "Co", (missing,), domain.SiteSide.BOTTOM, 1.5
                    ),
                )
            ),
        )


def test_same_requested_position_for_two_metals_fails_closed() -> None:
    source = _snapshot(("N", "N"), ((0.45, 0.5, 0.5), (0.55, 0.5, 0.5)))
    anchors = tuple(site.atom_uid for site in source.sites)

    with pytest.raises(structures.MultiMetalSiteError, match="same position"):
        structures.build_multi_metal_site(
            source,
            structures.MultiMetalSiteSpec(
                centers=(
                    structures.MultiMetalCenterSpec(
                        "Fe", anchors, domain.SiteSide.TOP, 1.5
                    ),
                    structures.MultiMetalCenterSpec(
                        "Co", anchors, domain.SiteSide.TOP, 1.5
                    ),
                )
            ),
        )


def test_pb2_poscar_round_trip_preserves_support_and_both_metal_identities(
    tmp_path: Path,
) -> None:
    pristine = structures.build_graphene(
        structures.GrapheneBuildSpec(nx=3, ny=2, vacuum_gap_angstrom=20.0)
    )
    doped = structures.substitute_dopants(
        pristine,
        (
            structures.DopantSubstitution(pristine.sites[2].atom_uid, "N"),
            structures.DopantSubstitution(pristine.sites[5].atom_uid, "N"),
        ),
        label="N2-graphene",
    ).snapshot
    nitrogens = tuple(site.atom_uid for site in doped.sites if site.element == "N")

    result = structures.build_multi_metal_site(
        doped,
        structures.MultiMetalSiteSpec(
            centers=(
                structures.MultiMetalCenterSpec(
                    "Pb", nitrogens, domain.SiteSide.TOP, 1.6
                ),
                structures.MultiMetalCenterSpec(
                    "Pb", nitrogens, domain.SiteSide.BOTTOM, 1.6
                ),
            ),
            metal_metal_topology_intent="opposite-side-pair",
            label="Pb2-N2C",
        ),
    )
    target = tmp_path / "POSCAR"

    structures.export_structure(result.snapshot, target)
    restored = structures.import_structure(target)

    assert restored.metadata.identity_status is structures.AtomIdentityStatus.PRESERVED_SIDECAR
    expected_by_uid = _sites_by_uid(result.snapshot)
    actual_by_uid = _sites_by_uid(restored.snapshot)
    assert set(actual_by_uid) == set(expected_by_uid)
    assert set(result.metal_atom_uids) <= set(actual_by_uid)
    assert all(actual_by_uid[atom_uid].element == "Pb" for atom_uid in result.metal_atom_uids)
    for atom_uid, expected in expected_by_uid.items():
        actual = actual_by_uid[atom_uid]
        assert actual.element == expected.element
        assert actual.fractional_coords == pytest.approx(expected.fractional_coords)

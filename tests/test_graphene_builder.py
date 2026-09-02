from __future__ import annotations

from itertools import product
from math import isclose, sqrt
from pathlib import Path

import pytest

from ecatvasp import domain, structures


def _cartesian_delta(
    fractional_delta: tuple[float, float, float],
    lattice: domain.Lattice,
) -> tuple[float, float, float]:
    return tuple(
        sum(fractional_delta[axis] * lattice.vectors[axis][component] for axis in range(3))
        for component in range(3)
    )


def _minimum_image_distance(
    first: domain.StructureSite,
    second: domain.StructureSite,
    lattice: domain.Lattice,
) -> float:
    best = float("inf")
    for translation in product((-1, 0, 1), repeat=3):
        delta = tuple(
            second.fractional_coords[axis]
            - first.fractional_coords[axis]
            + translation[axis]
            for axis in range(3)
        )
        cartesian = _cartesian_delta(delta, lattice)
        distance = sqrt(sum(component * component for component in cartesian))
        best = min(best, distance)
    return best


def test_graphene_builds_expected_supercell_geometry() -> None:
    spec = structures.GrapheneBuildSpec(
        nx=3,
        ny=2,
        bond_length_angstrom=1.42,
        vacuum_gap_angstrom=18.0,
        label="pristine-graphene",
    )

    snapshot = structures.build_graphene(spec)

    lattice_constant = sqrt(3.0) * 1.42
    assert snapshot.origin is domain.StructureOrigin.BUILT
    assert snapshot.periodic == (True, True, True)
    assert snapshot.label == "pristine-graphene"
    assert len(snapshot.sites) == 12
    assert spec.atom_count == 12
    assert spec.primitive_lattice_constant_angstrom == pytest.approx(lattice_constant)
    assert snapshot.lattice.vectors[0] == pytest.approx((3 * lattice_constant, 0.0, 0.0))
    assert snapshot.lattice.vectors[1] == pytest.approx(
        (lattice_constant, sqrt(3.0) * lattice_constant, 0.0)
    )
    assert snapshot.lattice.vectors[2] == pytest.approx((0.0, 0.0, 18.0))
    assert all(site.element == "C" for site in snapshot.sites)
    assert all(site.fractional_coords[2] == 0.5 for site in snapshot.sites)
    assert len({site.atom_uid for site in snapshot.sites}) == len(snapshot.sites)


def test_graphene_pristine_atoms_have_three_nearest_neighbors_under_pbc() -> None:
    bond_length = 1.42
    snapshot = structures.build_graphene(
        structures.GrapheneBuildSpec(nx=2, ny=2, bond_length_angstrom=bond_length)
    )

    for index, site in enumerate(snapshot.sites):
        neighbor_count = 0
        for other_index, other in enumerate(snapshot.sites):
            if index == other_index:
                continue
            distance = _minimum_image_distance(site, other, snapshot.lattice)
            if isclose(distance, bond_length, rel_tol=0.0, abs_tol=1e-10):
                neighbor_count += 1
        assert neighbor_count == 3


def test_graphene_builder_order_is_deterministic_but_identity_is_fresh() -> None:
    spec = structures.GrapheneBuildSpec(nx=2, ny=3)

    first = structures.build_graphene(spec)
    second = structures.build_graphene(spec)

    assert first.lattice == second.lattice
    assert tuple(site.element for site in first.sites) == tuple(site.element for site in second.sites)
    assert tuple(site.fractional_coords for site in first.sites) == tuple(
        site.fractional_coords for site in second.sites
    )
    assert tuple(site.atom_uid for site in first.sites) != tuple(
        site.atom_uid for site in second.sites
    )


def test_graphene_poscar_round_trip_preserves_geometry_and_atom_identity(tmp_path: Path) -> None:
    snapshot = structures.build_graphene(
        structures.GrapheneBuildSpec(nx=2, ny=2, vacuum_gap_angstrom=20.0)
    )
    target = tmp_path / "POSCAR"

    structures.export_structure(snapshot, target)
    restored = structures.import_structure(target)

    assert restored.metadata.identity_status is structures.AtomIdentityStatus.PRESERVED_SIDECAR
    assert restored.snapshot.periodic == snapshot.periodic
    for actual, expected in zip(
        restored.snapshot.lattice.vectors,
        snapshot.lattice.vectors,
        strict=True,
    ):
        assert actual == pytest.approx(expected)
    assert tuple(site.atom_uid for site in restored.snapshot.sites) == tuple(
        site.atom_uid for site in snapshot.sites
    )
    for actual, expected in zip(restored.snapshot.sites, snapshot.sites, strict=True):
        assert actual.element == expected.element
        assert actual.fractional_coords == pytest.approx(expected.fractional_coords)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("nx", 0),
        ("nx", -1),
        ("nx", True),
        ("ny", 0),
        ("ny", False),
        ("bond_length_angstrom", 0.0),
        ("bond_length_angstrom", float("nan")),
        ("bond_length_angstrom", float("inf")),
        ("vacuum_gap_angstrom", 0.0),
        ("vacuum_gap_angstrom", float("nan")),
    ],
)
def test_graphene_spec_rejects_invalid_scientific_inputs(field: str, value: object) -> None:
    kwargs: dict[str, object] = {field: value}
    with pytest.raises(ValueError):
        structures.GrapheneBuildSpec(**kwargs)  # type: ignore[arg-type]


def test_graphene_spec_rejects_blank_label() -> None:
    with pytest.raises(ValueError, match="label"):
        structures.GrapheneBuildSpec(label="   ")

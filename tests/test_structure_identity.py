"""Unit tests for stable atom identity and structure-revision mapping."""

import pytest

from ecatvasp.domain import Lattice, StructureSite, StructureSnapshot, new_atom_uid
from ecatvasp.structures import (
    AtomMappingError,
    AtomMappingMethod,
    GeometrySite,
    propagate_atom_uids_by_index,
    reconcile_reordered_sites,
    reorder_snapshot,
    validate_identity_preserving_revision,
)


def _source_snapshot() -> StructureSnapshot:
    return StructureSnapshot(
        lattice=Lattice(((10.0, 0.0, 0.0), (0.0, 10.0, 0.0), (0.0, 0.0, 20.0))),
        sites=(
            StructureSite(new_atom_uid(), "C", (0.10, 0.10, 0.50)),
            StructureSite(new_atom_uid(), "Pb", (0.45, 0.45, 0.58)),
            StructureSite(new_atom_uid(), "N", (0.20, 0.20, 0.50)),
            StructureSite(new_atom_uid(), "Pb", (0.55, 0.55, 0.42)),
        ),
        label="source",
    )


def test_index_preserving_propagation_keeps_identity_for_relaxed_geometry() -> None:
    source = _source_snapshot()
    target_sites = tuple(
        GeometrySite(
            site.element,
            (
                site.fractional_coords[0] + 0.001,
                site.fractional_coords[1],
                site.fractional_coords[2],
            ),
        )
        for site in source.sites
    )

    result = propagate_atom_uids_by_index(source, target_sites, label="relaxed")

    assert result.snapshot.parent_snapshot_id == source.id
    assert result.mapping.method is AtomMappingMethod.INDEX_PRESERVING
    assert tuple(site.atom_uid for site in result.snapshot.sites) == tuple(
        site.atom_uid for site in source.sites
    )
    assert all(entry.displacement_angstrom > 0 for entry in result.mapping.entries)


def test_exact_reorder_recovers_atom_uids_from_geometry() -> None:
    source = _source_snapshot()
    order = (3, 1, 0, 2)
    target_sites = tuple(
        GeometrySite(source.sites[index].element, source.sites[index].fractional_coords)
        for index in order
    )

    result = reconcile_reordered_sites(source, target_sites)

    assert result.mapping.method is AtomMappingMethod.EXACT_REORDER
    assert result.mapping.is_reordered
    assert tuple(site.atom_uid for site in result.snapshot.sites) == tuple(
        source.sites[index].atom_uid for index in order
    )
    validate_identity_preserving_revision(source=source, target=result.snapshot)


def test_reorder_snapshot_requires_a_complete_permutation() -> None:
    source = _source_snapshot()

    with pytest.raises(AtomMappingError, match="permutation"):
        reorder_snapshot(source, (0, 1, 1, 3))


def test_reorder_mapping_fails_closed_when_positions_are_ambiguous() -> None:
    duplicated_position = (0.25, 0.25, 0.50)
    source = StructureSnapshot(
        lattice=Lattice(((10.0, 0.0, 0.0), (0.0, 10.0, 0.0), (0.0, 0.0, 20.0))),
        sites=(
            StructureSite(new_atom_uid(), "H", duplicated_position),
            StructureSite(new_atom_uid(), "H", duplicated_position),
        ),
    )
    targets = (
        GeometrySite("H", duplicated_position),
        GeometrySite("H", duplicated_position),
    )

    with pytest.raises(AtomMappingError, match="ambiguous identity match"):
        reconcile_reordered_sites(source, targets)


def test_periodic_boundary_equivalence_is_used_for_reorder_mapping() -> None:
    atom_uid = new_atom_uid()
    source = StructureSnapshot(
        lattice=Lattice(((10.0, 0.0, 0.0), (0.0, 10.0, 0.0), (0.0, 0.0, 20.0))),
        sites=(StructureSite(atom_uid, "H", (0.9999999, 0.5, 0.5)),),
    )
    target = (GeometrySite("H", (-0.0000001, 0.5, 0.5)),)

    result = reconcile_reordered_sites(source, target, tolerance_angstrom=1e-5)

    assert result.snapshot.sites[0].atom_uid == atom_uid
    assert result.mapping.entries[0].displacement_angstrom == pytest.approx(0.0, abs=1e-12)

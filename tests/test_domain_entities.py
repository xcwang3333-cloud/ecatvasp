"""Unit tests for the schema-v1 domain entities."""

from dataclasses import FrozenInstanceError
from uuid import RFC_4122

import pytest

from ecatvasp.domain import (
    ActiveSite,
    BindingEdge,
    BindingMode,
    Lattice,
    Project,
    StateConformer,
    StructureSite,
    StructureSnapshot,
    new_uuid7,
)


def test_uuid7_is_rfc_4122_and_time_sortable() -> None:
    earlier = new_uuid7(timestamp_ms=1_700_000_000_000)
    later = new_uuid7(timestamp_ms=1_700_000_000_001)

    assert earlier.version == 7
    assert earlier.variant == RFC_4122
    assert earlier.int < later.int


def test_structure_snapshot_is_immutable_and_requires_unique_atom_uids() -> None:
    atom_uid = new_uuid7()
    lattice = Lattice(((10.0, 0.0, 0.0), (0.0, 10.0, 0.0), (0.0, 0.0, 15.0)))
    site = StructureSite(atom_uid=atom_uid, element="Pb", fractional_coords=(0.5, 0.5, 0.5))
    snapshot = StructureSnapshot(lattice=lattice, sites=(site,))

    assert snapshot.contains_atom(atom_uid)
    with pytest.raises(FrozenInstanceError):
        snapshot.label = "edited"  # type: ignore[misc]

    with pytest.raises(ValueError, match="atom_uid values must be unique"):
        StructureSnapshot(lattice=lattice, sites=(site, site))


def test_multicenter_binding_requires_multiple_site_atoms() -> None:
    project = Project(name="Test", slug="test")
    center = new_uuid7()
    active_site = ActiveSite(structure_variant_id=project.id, center_atom_uids=(center,))
    edge = BindingEdge(adsorbate_atom_uid=new_uuid7(), site_atom_uid=center)

    assert active_site.nuclearity == 1
    with pytest.raises(ValueError, match="multicenter binding requires"):
        StateConformer(
            adsorption_state_id=new_uuid7(),
            structure_snapshot_id=new_uuid7(),
            name="invalid",
            binding_mode=BindingMode.MULTICENTER,
            binding_edges=(edge,),
        )

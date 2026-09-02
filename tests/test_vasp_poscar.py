from __future__ import annotations

from dataclasses import replace

import pytest

from ecatvasp import domain, vasp


def _snapshot() -> domain.StructureSnapshot:
    return domain.StructureSnapshot(
        lattice=domain.Lattice(
            vectors=((10.0, 0.0, 0.0), (0.0, 10.0, 0.0), (0.0, 0.0, 20.0))
        ),
        sites=(
            domain.StructureSite(domain.new_atom_uid(), "C", (0.0, 0.0, 0.25)),
            domain.StructureSite(domain.new_atom_uid(), "Pb", (0.25, 0.25, 0.55)),
            domain.StructureSite(domain.new_atom_uid(), "C", (0.5, 0.5, 0.25)),
            domain.StructureSite(domain.new_atom_uid(), "O", (0.25, 0.25, 0.65)),
        ),
        periodic=(True, True, True),
    )


def test_prepare_poscar_is_deterministic_and_groups_species_stably() -> None:
    snapshot = _snapshot()
    first = vasp.prepare_poscar(snapshot)
    second = vasp.prepare_poscar(snapshot)

    assert first == second
    assert first.text == second.text
    assert first.sha256 == second.sha256
    assert first.species_order == ("C", "Pb", "O")
    assert first.species_counts == (2, 1, 1)

    lines = first.text.splitlines()
    assert lines[0] == "ECatVASP"
    assert lines[5].split() == ["C", "Pb", "O"]
    assert lines[6].split() == ["2", "1", "1"]
    assert lines[7] == "Direct"


def test_poscar_index_map_keeps_atom_uid_as_permanent_identity() -> None:
    snapshot = _snapshot()
    carbon_1, lead, carbon_2, oxygen = (site.atom_uid for site in snapshot.sites)

    prepared = vasp.prepare_poscar(snapshot)
    entries = prepared.index_map.entries

    assert tuple(entry.atom_uid for entry in entries) == (
        carbon_1,
        carbon_2,
        lead,
        oxygen,
    )
    assert tuple(entry.snapshot_index for entry in entries) == (0, 2, 1, 3)
    assert tuple(entry.poscar_index for entry in entries) == (0, 1, 2, 3)
    assert prepared.index_map.poscar_index(lead) == 2
    assert prepared.index_map.vasp_ordinal(lead) == 3
    assert prepared.index_map.atom_uid(2) == lead
    assert entries[2].vasp_ordinal == 3


def test_uid_selective_dynamics_resolves_after_poscar_ordering() -> None:
    snapshot = _snapshot()
    lead = snapshot.sites[1].atom_uid
    oxygen = snapshot.sites[3].atom_uid
    policy = vasp.UidSelectiveDynamics(
        default_flags=(False, False, False),
        overrides=(
            vasp.AtomSelectiveFlags(lead, (True, True, True)),
            vasp.AtomSelectiveFlags(oxygen, (True, False, True)),
        ),
    )

    prepared = vasp.prepare_poscar(snapshot, selective_dynamics=policy)

    assert prepared.selective_flags == (
        (False, False, False),
        (False, False, False),
        (True, True, True),
        (True, False, True),
    )
    lines = prepared.text.splitlines()
    assert lines[7] == "Selective dynamics"
    assert lines[8] == "Direct"
    assert lines[9].endswith("F F F")
    assert lines[10].endswith("F F F")
    assert lines[11].endswith("T T T")
    assert lines[12].endswith("T F T")


def test_uid_selective_dynamics_unknown_atom_fails_closed() -> None:
    snapshot = _snapshot()
    policy = vasp.UidSelectiveDynamics(
        overrides=(vasp.AtomSelectiveFlags(domain.new_atom_uid(), (True, True, True)),)
    )

    with pytest.raises(vasp.PoscarPreparationError, match="atom_uid"):
        vasp.prepare_poscar(snapshot, selective_dynamics=policy)


def test_uid_selective_dynamics_rejects_duplicate_atom_uid_overrides() -> None:
    atom_uid = domain.new_atom_uid()

    with pytest.raises(ValueError, match="unique atom_uids"):
        vasp.UidSelectiveDynamics(
            overrides=(
                vasp.AtomSelectiveFlags(atom_uid, (True, True, True)),
                vasp.AtomSelectiveFlags(atom_uid, (False, False, False)),
            )
        )


def test_prepare_poscar_rejects_nonperiodic_or_singular_vasp_cell() -> None:
    snapshot = _snapshot()
    nonperiodic = replace(snapshot, periodic=(True, True, False))
    singular = replace(
        snapshot,
        lattice=domain.Lattice(
            vectors=((10.0, 0.0, 0.0), (20.0, 0.0, 0.0), (0.0, 0.0, 20.0))
        ),
    )

    with pytest.raises(vasp.PoscarPreparationError, match="fully periodic"):
        vasp.prepare_poscar(nonperiodic)
    with pytest.raises(vasp.PoscarPreparationError, match="non-singular"):
        vasp.prepare_poscar(singular)


def test_prepare_poscar_does_not_mutate_snapshot_order() -> None:
    snapshot = _snapshot()
    original_uids = tuple(site.atom_uid for site in snapshot.sites)

    vasp.prepare_poscar(snapshot)

    assert tuple(site.atom_uid for site in snapshot.sites) == original_uids

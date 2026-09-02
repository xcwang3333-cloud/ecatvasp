from __future__ import annotations

import json
from pathlib import Path

import pytest

from ecatvasp import domain
from ecatvasp import structures


POSCAR_SELECTIVE = """Pb2 opposite-side model
1.0
10.0 0.0 0.0
0.0 10.0 0.0
0.0 0.0 20.0
C Pb
2 1
Selective dynamics
Direct
0.0 0.0 0.0 T T F
0.5 0.5 0.0 T T T
0.25 0.25 0.5 F F F
"""

CIF_FRACTIONAL = """data_pb2
_cell_length_a 10.0
_cell_length_b 10.0
_cell_length_c 20.0
_cell_angle_alpha 90
_cell_angle_beta 90
_cell_angle_gamma 90
loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
C1 C 0.0 0.0 0.0
C2 C 0.5 0.5 0.0
Pb1 Pb 0.25 0.25 0.5
"""

CIF_WITH_SYMMETRY = """data_symmetry
_cell_length_a 10.0
_cell_length_b 10.0
_cell_length_c 10.0
_cell_angle_alpha 90
_cell_angle_beta 90
_cell_angle_gamma 90
_space_group_name_H-M_alt 'P -1'
loop_
_space_group_symop_operation_xyz
'x,y,z'
'-x,-y,-z'
loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
_atom_site_occupancy
C1 C 0.1 0.2 0.3 1.0
"""

CIF_DISORDERED = """data_disordered
_cell_length_a 10.0
_cell_length_b 10.0
_cell_length_c 10.0
_cell_angle_alpha 90
_cell_angle_beta 90
_cell_angle_gamma 90
_space_group_name_H-M_alt 'P 1'
loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
_atom_site_occupancy
C1 C 0.0 0.0 0.0 0.5
N1 N 0.0 0.0 0.0 0.5
"""


def _uids(document: structures.StructureDocument) -> tuple[object, ...]:
    return tuple(site.atom_uid for site in document.snapshot.sites)


def _pb2_snapshot(
    *,
    periodic: tuple[bool, bool, bool] = (True, True, True),
) -> domain.StructureSnapshot:
    return domain.StructureSnapshot(
        lattice=domain.Lattice(
            vectors=((10.0, 0.0, 0.0), (0.0, 10.0, 0.0), (0.0, 0.0, 20.0))
        ),
        sites=(
            domain.StructureSite(domain.new_atom_uid(), "C", (0.0, 0.0, 0.0)),
            domain.StructureSite(domain.new_atom_uid(), "Pb", (0.25, 0.25, 0.5)),
            domain.StructureSite(domain.new_atom_uid(), "C", (0.5, 0.5, 0.0)),
        ),
        periodic=periodic,
    )


def test_poscar_import_preserves_selective_dynamics_and_new_identity() -> None:
    document = structures.parse_structure(
        POSCAR_SELECTIVE,
        format=structures.StructureFormat.POSCAR,
    )

    assert document.metadata.format is structures.StructureFormat.POSCAR
    assert document.metadata.coordinate_mode == "direct"
    assert document.metadata.identity_status is structures.AtomIdentityStatus.NEW
    assert document.snapshot.periodic == (True, True, True)
    assert tuple(site.element for site in document.snapshot.sites) == ("C", "C", "Pb")
    assert document.snapshot.sites[2].fractional_coords == pytest.approx((0.25, 0.25, 0.5))
    assert document.metadata.selective_dynamics == structures.SelectiveDynamics(
        flags=((True, True, False), (True, True, True), (False, False, False))
    )
    assert len(set(_uids(document))) == 3


def test_poscar_cartesian_and_negative_volume_scaling() -> None:
    text = """cartesian
-8.0
1 0 0
0 1 0
0 0 1
Pb
1
Cartesian
1 1 1
"""
    document = structures.parse_structure(text, format="poscar")

    expected_lattice = ((2.0, 0.0, 0.0), (0.0, 2.0, 0.0), (0.0, 0.0, 2.0))
    for actual, expected in zip(document.snapshot.lattice.vectors, expected_lattice, strict=True):
        assert actual == pytest.approx(expected)
    assert document.snapshot.sites[0].fractional_coords == pytest.approx((1.0, 1.0, 1.0))
    assert document.metadata.coordinate_mode == "cartesian"


def test_poscar_export_groups_elements_and_sidecar_tracks_export_order(tmp_path: Path) -> None:
    snapshot = _pb2_snapshot()
    carbon_1 = snapshot.sites[0].atom_uid
    lead = snapshot.sites[1].atom_uid
    carbon_2 = snapshot.sites[2].atom_uid
    target = tmp_path / "POSCAR"

    structures.export_structure(snapshot, target)
    lines = target.read_text(encoding="utf-8").splitlines()
    assert lines[5].split() == ["C", "Pb"]
    assert lines[6].split() == ["2", "1"]

    reimported = structures.import_structure(target)
    assert _uids(reimported) == (carbon_1, carbon_2, lead)
    assert reimported.metadata.identity_status is structures.AtomIdentityStatus.PRESERVED_SIDECAR


def test_poscar_round_trip_preserves_atom_uid_and_selective_dynamics(tmp_path: Path) -> None:
    imported = structures.parse_structure(POSCAR_SELECTIVE, format="poscar")
    target = tmp_path / "CONTCAR"

    structures.export_structure(imported, target)
    reimported = structures.import_structure(target)

    assert _uids(reimported) == _uids(imported)
    assert reimported.metadata.identity_status is structures.AtomIdentityStatus.PRESERVED_SIDECAR
    assert reimported.metadata.selective_dynamics == imported.metadata.selective_dynamics


def test_cif_fractional_round_trip_and_sidecar_preserves_constraints(tmp_path: Path) -> None:
    imported = structures.parse_structure(POSCAR_SELECTIVE, format="poscar")
    target = tmp_path / "model.cif"

    structures.export_structure(imported, target)
    reimported = structures.import_structure(target)

    assert reimported.metadata.format is structures.StructureFormat.CIF
    assert _uids(reimported) == _uids(imported)
    assert reimported.metadata.selective_dynamics == imported.metadata.selective_dynamics
    assert tuple(site.element for site in reimported.snapshot.sites) == ("C", "C", "Pb")
    assert reimported.snapshot.sites[2].fractional_coords == pytest.approx((0.25, 0.25, 0.5))


def test_cif_import_accepts_standard_fractional_atom_site_loop() -> None:
    document = structures.parse_structure(CIF_FRACTIONAL, format="cif")

    assert document.snapshot.periodic == (True, True, True)
    assert tuple(site.element for site in document.snapshot.sites) == ("C", "C", "Pb")
    assert document.snapshot.sites[1].fractional_coords == pytest.approx((0.5, 0.5, 0.0))
    exported = structures.serialize_structure(document, format="cif")
    assert "_atom_site_fract_x" in exported
    assert "Pb" in exported


def test_cif_adapter_expands_explicit_symmetry_operations() -> None:
    document = structures.parse_structure(CIF_WITH_SYMMETRY, format="cif")

    assert len(document.snapshot.sites) == 2
    coords = tuple(site.fractional_coords for site in document.snapshot.sites)
    assert any(coord == pytest.approx((0.1, 0.2, 0.3)) for coord in coords)
    assert any(coord == pytest.approx((0.9, 0.8, 0.7)) for coord in coords)


def test_cif_disordered_site_fails_closed() -> None:
    with pytest.raises(structures.StructureIOError, match=r"occupancy|disordered"):
        structures.parse_structure(CIF_DISORDERED, format="cif")


def test_xyz_is_explicitly_nonperiodic_and_cannot_be_silently_exported_to_poscar(
    tmp_path: Path,
) -> None:
    text = """2
CO molecule
C 0.0 0.0 0.0
O 1.2 0.0 0.0
"""
    document = structures.parse_structure(text, format="xyz")

    assert document.snapshot.periodic == (False, False, False)
    assert document.snapshot.sites[1].fractional_coords == pytest.approx((1.2, 0.0, 0.0))

    xyz_path = tmp_path / "molecule.xyz"
    structures.export_structure(document, xyz_path)
    reimported = structures.import_structure(xyz_path)
    assert _uids(reimported) == _uids(document)

    with pytest.raises(structures.StructureIOError, match="fully periodic"):
        structures.export_structure(document, tmp_path / "POSCAR")


def test_xyz_sidecar_restores_periodic_cell_and_domain_coordinates(tmp_path: Path) -> None:
    snapshot = _pb2_snapshot(periodic=(True, True, False))
    target = tmp_path / "state.xyz"

    structures.export_structure(snapshot, target)
    reimported = structures.import_structure(target)

    assert reimported.snapshot.periodic == snapshot.periodic
    assert reimported.snapshot.lattice == snapshot.lattice
    assert _uids(reimported) == tuple(site.atom_uid for site in snapshot.sites)
    for actual, expected in zip(reimported.snapshot.sites, snapshot.sites, strict=True):
        assert actual.fractional_coords == pytest.approx(expected.fractional_coords)


def test_extxyz_embeds_atom_uid_cell_periodicity_and_selective_dynamics(tmp_path: Path) -> None:
    snapshot = _pb2_snapshot(periodic=(True, True, False))
    selective = structures.SelectiveDynamics(
        flags=((True, True, False), (True, True, True), (False, False, False))
    )
    document = structures.StructureDocument(
        snapshot=snapshot,
        metadata=structures.StructureSourceMetadata(
            format=structures.StructureFormat.EXTXYZ,
            selective_dynamics=selective,
        ),
    )
    target = tmp_path / "state.extxyz"

    structures.export_structure(document, target)
    assert not (tmp_path / "state.extxyz.ecatvasp.json").exists()
    reimported = structures.import_structure(target)

    assert _uids(reimported) == _uids(document)
    assert reimported.snapshot.periodic == (True, True, False)
    assert reimported.metadata.identity_status is structures.AtomIdentityStatus.PRESERVED_EMBEDDED
    assert reimported.metadata.selective_dynamics == selective
    assert reimported.snapshot.sites[1].fractional_coords == pytest.approx((0.25, 0.25, 0.5))


def test_sidecar_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    document = structures.parse_structure(POSCAR_SELECTIVE, format="poscar")
    target = tmp_path / "POSCAR"
    structures.export_structure(document, target)

    target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(structures.StructureIOError, match="hash does not match"):
        structures.import_structure(target)


def test_sidecar_atom_identity_mismatch_fails_closed(tmp_path: Path) -> None:
    document = structures.parse_structure(POSCAR_SELECTIVE, format="poscar")
    target = tmp_path / "POSCAR"
    structures.export_structure(document, target)
    sidecar = tmp_path / "POSCAR.ecatvasp.json"
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    payload["elements"] = ["Pb", "C", "C"]
    sidecar.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(structures.StructureIOError, match="atom ordering"):
        structures.import_structure(target)


def test_sidecar_lattice_contradiction_fails_closed(tmp_path: Path) -> None:
    document = structures.parse_structure(POSCAR_SELECTIVE, format="poscar")
    target = tmp_path / "POSCAR"
    structures.export_structure(document, target)
    sidecar = tmp_path / "POSCAR.ecatvasp.json"
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    payload["lattice_angstrom"][0][0] = 11.0
    sidecar.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(structures.StructureIOError, match="lattice contradicts"):
        structures.import_structure(target)


def test_invalid_element_is_rejected() -> None:
    with pytest.raises(structures.StructureIOError):
        structures.parse_structure("1\ninvalid\nXx 0 0 0\n", format="xyz")
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ecatvasp.domain import Lattice, StructureSite, StructureSnapshot, new_atom_uid
from ecatvasp.structures import (
    AtomIdentityStatus,
    SelectiveDynamics,
    StructureDocument,
    StructureFormat,
    StructureIOError,
    StructureSourceMetadata,
    export_structure,
    import_structure,
    parse_structure,
    serialize_structure,
)


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


def _uids(document: StructureDocument) -> tuple[object, ...]:
    return tuple(site.atom_uid for site in document.snapshot.sites)


def test_poscar_import_preserves_selective_dynamics_and_new_identity() -> None:
    document = parse_structure(POSCAR_SELECTIVE, format=StructureFormat.POSCAR)

    assert document.metadata.format is StructureFormat.POSCAR
    assert document.metadata.coordinate_mode == "direct"
    assert document.metadata.identity_status is AtomIdentityStatus.NEW
    assert document.snapshot.periodic == (True, True, True)
    assert tuple(site.element for site in document.snapshot.sites) == ("C", "C", "Pb")
    assert document.snapshot.sites[2].fractional_coords == pytest.approx((0.25, 0.25, 0.5))
    assert document.metadata.selective_dynamics == SelectiveDynamics(
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
    document = parse_structure(text, format="poscar")

    expected_lattice = ((2.0, 0.0, 0.0), (0.0, 2.0, 0.0), (0.0, 0.0, 2.0))
    for actual, expected in zip(document.snapshot.lattice.vectors, expected_lattice, strict=True):
        assert actual == pytest.approx(expected)
    assert document.snapshot.sites[0].fractional_coords == pytest.approx((1.0, 1.0, 1.0))
    assert document.metadata.coordinate_mode == "cartesian"


def test_poscar_export_groups_elements_and_sidecar_tracks_export_order(tmp_path: Path) -> None:
    carbon_1 = new_atom_uid()
    lead = new_atom_uid()
    carbon_2 = new_atom_uid()
    snapshot = StructureSnapshot(
        lattice=Lattice(vectors=((10.0, 0.0, 0.0), (0.0, 10.0, 0.0), (0.0, 0.0, 20.0))),
        sites=(
            StructureSite(carbon_1, "C", (0.0, 0.0, 0.0)),
            StructureSite(lead, "Pb", (0.25, 0.25, 0.5)),
            StructureSite(carbon_2, "C", (0.5, 0.5, 0.0)),
        ),
    )
    target = tmp_path / "POSCAR"

    export_structure(snapshot, target)
    lines = target.read_text(encoding="utf-8").splitlines()
    assert lines[5].split() == ["C", "Pb"]
    assert lines[6].split() == ["2", "1"]

    reimported = import_structure(target)
    assert _uids(reimported) == (carbon_1, carbon_2, lead)
    assert reimported.metadata.identity_status is AtomIdentityStatus.PRESERVED_SIDECAR


def test_poscar_round_trip_preserves_atom_uid_and_selective_dynamics(tmp_path: Path) -> None:
    imported = parse_structure(POSCAR_SELECTIVE, format="poscar")
    target = tmp_path / "CONTCAR"

    export_structure(imported, target)
    reimported = import_structure(target)

    assert _uids(reimported) == _uids(imported)
    assert reimported.metadata.identity_status is AtomIdentityStatus.PRESERVED_SIDECAR
    assert reimported.metadata.selective_dynamics == imported.metadata.selective_dynamics


def test_cif_fractional_round_trip_and_sidecar_preserves_constraints(tmp_path: Path) -> None:
    imported = parse_structure(POSCAR_SELECTIVE, format="poscar")
    target = tmp_path / "model.cif"

    export_structure(imported, target)
    reimported = import_structure(target)

    assert reimported.metadata.format is StructureFormat.CIF
    assert _uids(reimported) == _uids(imported)
    assert reimported.metadata.selective_dynamics == imported.metadata.selective_dynamics
    assert tuple(site.element for site in reimported.snapshot.sites) == ("C", "C", "Pb")
    assert reimported.snapshot.sites[2].fractional_coords == pytest.approx((0.25, 0.25, 0.5))


def test_cif_import_accepts_standard_fractional_atom_site_loop() -> None:
    document = parse_structure(CIF_FRACTIONAL, format="cif")

    assert document.snapshot.periodic == (True, True, True)
    assert tuple(site.element for site in document.snapshot.sites) == ("C", "C", "Pb")
    assert document.snapshot.sites[1].fractional_coords == pytest.approx((0.5, 0.5, 0.0))
    exported = serialize_structure(document, format="cif")
    assert "_atom_site_fract_x" in exported
    assert "Pb1 Pb" in exported


def test_xyz_is_explicitly_nonperiodic_and_cannot_be_silently_exported_to_poscar(
    tmp_path: Path,
) -> None:
    text = """2
CO molecule
C 0.0 0.0 0.0
O 1.2 0.0 0.0
"""
    document = parse_structure(text, format="xyz")

    assert document.snapshot.periodic == (False, False, False)
    assert document.snapshot.sites[1].fractional_coords == pytest.approx((1.2, 0.0, 0.0))

    xyz_path = tmp_path / "molecule.xyz"
    export_structure(document, xyz_path)
    reimported = import_structure(xyz_path)
    assert _uids(reimported) == _uids(document)

    with pytest.raises(StructureIOError, match="fully periodic"):
        export_structure(document, tmp_path / "POSCAR")


def test_extxyz_embeds_atom_uid_lattice_and_periodicity(tmp_path: Path) -> None:
    first = new_atom_uid()
    second = new_atom_uid()
    snapshot = StructureSnapshot(
        lattice=Lattice(vectors=((10.0, 0.0, 0.0), (0.0, 10.0, 0.0), (0.0, 0.0, 20.0))),
        sites=(
            StructureSite(first, "Pb", (0.25, 0.25, 0.25)),
            StructureSite(second, "O", (0.25, 0.25, 0.30)),
        ),
        periodic=(True, True, False),
    )
    document = StructureDocument(
        snapshot=snapshot,
        metadata=StructureSourceMetadata(format=StructureFormat.EXTXYZ),
    )
    target = tmp_path / "state.extxyz"

    export_structure(document, target)
    assert not (tmp_path / "state.extxyz.ecatvasp.json").exists()
    reimported = import_structure(target)

    assert _uids(reimported) == (first, second)
    assert reimported.snapshot.periodic == (True, True, False)
    assert reimported.metadata.identity_status is AtomIdentityStatus.PRESERVED_EMBEDDED
    assert reimported.snapshot.sites[1].fractional_coords == pytest.approx((0.25, 0.25, 0.30))


def test_sidecar_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    document = parse_structure(POSCAR_SELECTIVE, format="poscar")
    target = tmp_path / "POSCAR"
    export_structure(document, target)

    target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(StructureIOError, match="hash does not match"):
        import_structure(target)


def test_sidecar_atom_identity_mismatch_fails_closed(tmp_path: Path) -> None:
    document = parse_structure(POSCAR_SELECTIVE, format="poscar")
    target = tmp_path / "POSCAR"
    export_structure(document, target)
    sidecar = tmp_path / "POSCAR.ecatvasp.json"
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    payload["elements"] = ["Pb", "C", "C"]
    sidecar.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(StructureIOError, match="atom ordering"):
        import_structure(target)


def test_invalid_element_is_rejected() -> None:
    with pytest.raises(StructureIOError, match="chemical element"):
        parse_structure("1\ninvalid\nXx 0 0 0\n", format="xyz")

from __future__ import annotations

import hashlib
import json

import pytest

from ecatvasp.analysis import DoscarParseError, ProjectionScope, SpinChannel, parse_vasp_doscar
from ecatvasp.domain import SpinTreatment
from ecatvasp.domain.ids import AtomUid, new_atom_uid, new_structure_snapshot_id


def _atom_map(snapshot_id: object, atoms: tuple[tuple[AtomUid, str], ...]) -> bytes:
    species_order: list[str] = []
    species_counts: list[int] = []
    for _uid, element in atoms:
        if species_order and species_order[-1] == element:
            species_counts[-1] += 1
        else:
            species_order.append(element)
            species_counts.append(1)
    payload = {
        "format": "ecatvasp-v03-atom-index-map",
        "version": 1,
        "structure_snapshot_id": str(snapshot_id),
        "structure_sha256": "a" * 64,
        "poscar_sha256": "b" * 64,
        "species_order": species_order,
        "species_counts": species_counts,
        "entries": [
            {
                "atom_uid": str(uid),
                "element": element,
                "snapshot_index": index,
                "poscar_index": index,
                "vasp_ordinal": index + 1,
                "selective_dynamics": None,
            }
            for index, (uid, element) in enumerate(atoms)
        ],
    }
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _header(ion_count: int = 1) -> list[str]:
    return [
        f"{ion_count} {ion_count} 1 0",
        "10.0 1.0 1.0 1.0 0.5",
        "300.0",
        "CAR",
        "ECatVASP DOS test",
    ]


def _unpolarized_d_doscar() -> bytes:
    lines = _header()
    lines.extend(
        [
            "2.0 -2.0 3 0.5 1.0",
            "-2.0 1.0 0.0",
            "0.0 2.0 1.0",
            "2.0 1.5 2.0",
            "2.0 -2.0 3 0.5 1.0",
            "-2.0 1 2 3 4 5 6 7 8 9",
            "0.0 11 12 13 14 15 16 17 18 19",
            "2.0 21 22 23 24 25 26 27 28 29",
        ]
    )
    return ("\n".join(lines) + "\n").encode()


def _collinear_s_doscar() -> bytes:
    lines = _header()
    lines.extend(
        [
            "2.0 -2.0 3 -0.25 1.0",
            "-2.0 1.0 0.5 0.0 0.0",
            "0.0 2.0 1.5 1.0 0.5",
            "2.0 1.0 0.4 2.0 1.0",
            "2.0 -2.0 3 -0.25 1.0",
            "-2.0 0.2 0.1",
            "0.0 0.4 0.3",
            "2.0 0.1 0.05",
        ]
    )
    return ("\n".join(lines) + "\n").encode()


def test_unpolarized_lorbit11_doscar_maps_lm_channels_to_atom_uid() -> None:
    snapshot_id = new_structure_snapshot_id()
    atom_uid = new_atom_uid()
    atom_map = _atom_map(snapshot_id, ((atom_uid, "Fe"),))
    doscar = _unpolarized_d_doscar()

    intake = parse_vasp_doscar(
        doscar_bytes=doscar,
        atom_index_map_bytes=atom_map,
        structure_snapshot_id=snapshot_id,
        spin_treatment=SpinTreatment.UNPOLARIZED,
    )

    assert intake.doscar_sha256 == hashlib.sha256(doscar).hexdigest()
    assert intake.result.atom_index_map_sha256 == hashlib.sha256(atom_map).hexdigest()
    assert intake.result.energy_axis.energies_ev == (-2.0, 0.0, 2.0)
    assert intake.result.energy_axis.fermi_energy_ev == 0.5
    projected = tuple(
        item for item in intake.result.series if item.scope is ProjectionScope.ATOM
    )
    assert {item.atom_uid for item in projected} == {atom_uid}
    assert tuple(item.orbital.label for item in projected if item.orbital is not None) == (
        "s",
        "py",
        "pz",
        "px",
        "dxy",
        "dyz",
        "dz2",
        "dxz",
        "dx2-y2",
    )
    dz2 = next(item for item in projected if item.orbital and item.orbital.label == "dz2")
    assert dz2.values == (7.0, 17.0, 27.0)


def test_collinear_lorbit11_doscar_preserves_up_down_columns() -> None:
    snapshot_id = new_structure_snapshot_id()
    atom_uid = new_atom_uid()
    intake = parse_vasp_doscar(
        doscar_bytes=_collinear_s_doscar(),
        atom_index_map_bytes=_atom_map(snapshot_id, ((atom_uid, "H"),)),
        structure_snapshot_id=snapshot_id,
        spin_treatment=SpinTreatment.COLLINEAR,
    )

    system = tuple(item for item in intake.result.series if item.scope is ProjectionScope.SYSTEM)
    assert tuple(item.spin for item in system) == (SpinChannel.UP, SpinChannel.DOWN)
    assert system[0].values == (1.0, 2.0, 1.0)
    assert system[1].values == (0.5, 1.5, 0.4)
    projected = tuple(
        item for item in intake.result.series if item.scope is ProjectionScope.ATOM
    )
    assert tuple(item.spin for item in projected) == (SpinChannel.UP, SpinChannel.DOWN)
    assert projected[0].values == (0.2, 0.4, 0.1)
    assert projected[1].values == (0.1, 0.3, 0.05)


def test_doscar_rejects_noncollinear_and_soc_without_relabeling() -> None:
    snapshot_id = new_structure_snapshot_id()
    atom_map = _atom_map(snapshot_id, ((new_atom_uid(), "Fe"),))

    with pytest.raises(DoscarParseError, match="SOC/noncollinear"):
        parse_vasp_doscar(
            doscar_bytes=_unpolarized_d_doscar(),
            atom_index_map_bytes=atom_map,
            structure_snapshot_id=snapshot_id,
            spin_treatment=SpinTreatment.NONCOLLINEAR,
        )
    with pytest.raises(DoscarParseError, match="SOC/noncollinear"):
        parse_vasp_doscar(
            doscar_bytes=_unpolarized_d_doscar(),
            atom_index_map_bytes=atom_map,
            structure_snapshot_id=snapshot_id,
            spin_treatment=SpinTreatment.UNPOLARIZED,
            soc=True,
        )


def test_doscar_rejects_atom_map_for_another_snapshot() -> None:
    snapshot_id = new_structure_snapshot_id()
    other_snapshot_id = new_structure_snapshot_id()

    with pytest.raises(DoscarParseError, match="another StructureSnapshot"):
        parse_vasp_doscar(
            doscar_bytes=_unpolarized_d_doscar(),
            atom_index_map_bytes=_atom_map(other_snapshot_id, ((new_atom_uid(), "Fe"),)),
            structure_snapshot_id=snapshot_id,
            spin_treatment=SpinTreatment.UNPOLARIZED,
        )


def test_doscar_rejects_site_energy_grid_drift() -> None:
    snapshot_id = new_structure_snapshot_id()
    body = _unpolarized_d_doscar().decode().replace(
        "-2.0 1 2 3 4 5 6 7 8 9",
        "-1.9 1 2 3 4 5 6 7 8 9",
    )

    with pytest.raises(DoscarParseError, match="energy grid differs"):
        parse_vasp_doscar(
            doscar_bytes=body.encode(),
            atom_index_map_bytes=_atom_map(snapshot_id, ((new_atom_uid(), "Fe"),)),
            structure_snapshot_id=snapshot_id,
            spin_treatment=SpinTreatment.UNPOLARIZED,
        )


def test_doscar_rejects_unrecognized_f_or_other_orbital_layout() -> None:
    snapshot_id = new_structure_snapshot_id()
    lines = _header()
    lines.extend(
        [
            "1.0 -1.0 2 0.0 1.0",
            "-1.0 1.0 0.0",
            "1.0 1.0 1.0",
            "1.0 -1.0 2 0.0 1.0",
            "-1.0 " + " ".join(str(value) for value in range(1, 17)),
            "1.0 " + " ".join(str(value) for value in range(17, 33)),
        ]
    )

    with pytest.raises(DoscarParseError, match="orbital layout is unsupported"):
        parse_vasp_doscar(
            doscar_bytes=("\n".join(lines) + "\n").encode(),
            atom_index_map_bytes=_atom_map(snapshot_id, ((new_atom_uid(), "Ce"),)),
            structure_snapshot_id=snapshot_id,
            spin_treatment=SpinTreatment.UNPOLARIZED,
        )

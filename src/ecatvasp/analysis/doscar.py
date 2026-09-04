"""Fail-closed VASP DOSCAR parsing into canonical v0.7 DOS/PDOS facts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from math import isclose, isfinite
from uuid import UUID

from ecatvasp.analysis.electronic import (
    CanonicalDosResult,
    DosSeries,
    ElectronicEnergyAxis,
    OrbitalChannel,
    ProjectionScope,
    SpinChannel,
)
from ecatvasp.domain import SpinTreatment
from ecatvasp.domain.ids import AtomUid, StructureSnapshotId

DOSCAR_PARSER_NAME = "ecatvasp.analysis.doscar"
DOSCAR_PARSER_VERSION = "1"

_ORBITALS: dict[int, tuple[OrbitalChannel, ...]] = {
    1: (OrbitalChannel("s", 0),),
    4: (
        OrbitalChannel("s", 0),
        OrbitalChannel("py", 1),
        OrbitalChannel("pz", 1),
        OrbitalChannel("px", 1),
    ),
    9: (
        OrbitalChannel("s", 0),
        OrbitalChannel("py", 1),
        OrbitalChannel("pz", 1),
        OrbitalChannel("px", 1),
        OrbitalChannel("dxy", 2),
        OrbitalChannel("dyz", 2),
        OrbitalChannel("dz2", 2),
        OrbitalChannel("dxz", 2),
        OrbitalChannel("dx2-y2", 2),
    ),
}


class DoscarParseError(ValueError):
    """Raised when DOSCAR data cannot be normalized without scientific guessing."""


@dataclass(frozen=True, slots=True)
class CanonicalDosIntake:
    """One exact DOSCAR parse receipt plus normalized scientific facts."""

    result: CanonicalDosResult
    doscar_sha256: str
    parser_name: str = DOSCAR_PARSER_NAME
    parser_version: str = DOSCAR_PARSER_VERSION

    def __post_init__(self) -> None:
        _validate_sha256(self.doscar_sha256, "doscar_sha256")
        if not self.parser_name.strip() or not self.parser_version.strip():
            raise ValueError("parser name/version must not be blank")


@dataclass(frozen=True, slots=True)
class _AtomBinding:
    atom_uid: AtomUid
    element: str


@dataclass(frozen=True, slots=True)
class _DosHeader:
    emax_ev: float
    emin_ev: float
    nedos: int
    fermi_energy_ev: float


def parse_vasp_doscar(
    *,
    doscar_bytes: bytes,
    atom_index_map_bytes: bytes,
    structure_snapshot_id: StructureSnapshotId,
    spin_treatment: SpinTreatment,
    soc: bool = False,
) -> CanonicalDosIntake:
    """Parse exact DOSCAR bytes using only the exact frozen POSCAR atom map.

    The parser supports the v0.3 `LORBIT=11` prerequisite for unpolarized and
    collinear calculations with explicit s/p/d lm-resolved projections. SOC,
    noncollinear spin decomposition, absent PDOS, and unrecognized orbital-column
    layouts fail closed rather than being inferred.
    """

    if soc or spin_treatment is SpinTreatment.NONCOLLINEAR:
        raise DoscarParseError("SOC/noncollinear DOS is outside the v0.7 collinear contract")
    if spin_treatment not in {SpinTreatment.UNPOLARIZED, SpinTreatment.COLLINEAR}:
        raise DoscarParseError("unsupported spin treatment")

    atom_map_sha256 = hashlib.sha256(atom_index_map_bytes).hexdigest()
    atoms = _parse_atom_index_map(
        body=atom_index_map_bytes,
        structure_snapshot_id=structure_snapshot_id,
    )
    doscar_sha256 = hashlib.sha256(doscar_bytes).hexdigest()
    try:
        text = doscar_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise DoscarParseError("DOSCAR must be valid UTF-8/ASCII text") from error
    lines = text.splitlines()
    if len(lines) < 6:
        raise DoscarParseError("DOSCAR is truncated before the total-DOS header")

    ion_count, partial_flag = _parse_file_header(lines[0])
    if ion_count != len(atoms):
        raise DoscarParseError("DOSCAR ion count does not match frozen atom-index map")
    if partial_flag != 1:
        raise DoscarParseError("DOSCAR does not declare site-projected DOS")

    total_header = _parse_dos_header(lines[5], "total DOS header")
    cursor = 6
    total_rows, cursor = _read_rows(
        lines=lines,
        cursor=cursor,
        count=total_header.nedos,
        label="total DOS",
    )
    expected_total_columns = 3 if spin_treatment is SpinTreatment.UNPOLARIZED else 5
    _require_uniform_columns(total_rows, expected_total_columns, "total DOS")
    energies = tuple(row[0] for row in total_rows)
    system_series = _system_series(total_rows, spin_treatment)

    projected_series: list[DosSeries] = []
    for ion_index, atom in enumerate(atoms):
        if cursor >= len(lines):
            raise DoscarParseError("DOSCAR is missing one or more site-projected blocks")
        site_header = _parse_dos_header(lines[cursor], f"site {ion_index + 1} DOS header")
        cursor += 1
        _validate_site_header(site_header, total_header, ion_index + 1)
        rows, cursor = _read_rows(
            lines=lines,
            cursor=cursor,
            count=total_header.nedos,
            label=f"site {ion_index + 1} DOS",
        )
        _validate_site_energies(rows, energies, ion_index + 1)
        projected_series.extend(
            _site_series(
                rows=rows,
                atom=atom,
                spin_treatment=spin_treatment,
                ion_index=ion_index + 1,
            )
        )

    if any(line.strip() for line in lines[cursor:]):
        raise DoscarParseError("DOSCAR contains unexpected trailing non-empty data")

    result = CanonicalDosResult(
        structure_snapshot_id=structure_snapshot_id,
        energy_axis=ElectronicEnergyAxis(
            energies_ev=energies,
            fermi_energy_ev=total_header.fermi_energy_ev,
        ),
        series=tuple((*system_series, *projected_series)),
        atom_index_map_sha256=atom_map_sha256,
    )
    return CanonicalDosIntake(result=result, doscar_sha256=doscar_sha256)


def _parse_file_header(line: str) -> tuple[int, int]:
    tokens = line.split()
    if len(tokens) < 4:
        raise DoscarParseError("DOSCAR first header line is malformed")
    try:
        ions_with_spheres = int(tokens[0])
        ions = int(tokens[1])
        partial_flag = int(tokens[2])
    except ValueError as error:
        raise DoscarParseError("DOSCAR ion/projection header fields must be integers") from error
    if ions_with_spheres < 1 or ions < 1:
        raise DoscarParseError("DOSCAR ion count must be positive")
    if ions_with_spheres != ions:
        raise DoscarParseError("DOSCAR empty-sphere ion indexing is unsupported")
    if partial_flag not in {0, 1}:
        raise DoscarParseError("DOSCAR partial-DOS flag must be 0 or 1")
    return ions, partial_flag


def _parse_dos_header(line: str, label: str) -> _DosHeader:
    tokens = line.split()
    if len(tokens) < 4:
        raise DoscarParseError(f"{label} is malformed")
    try:
        emax = _float(tokens[0])
        emin = _float(tokens[1])
        nedos = int(tokens[2])
        fermi = _float(tokens[3])
    except ValueError as error:
        raise DoscarParseError(f"{label} contains invalid numeric fields") from error
    if nedos < 2:
        raise DoscarParseError(f"{label} NEDOS must be at least 2")
    if emax <= emin:
        raise DoscarParseError(f"{label} requires EMAX > EMIN")
    return _DosHeader(emax_ev=emax, emin_ev=emin, nedos=nedos, fermi_energy_ev=fermi)


def _read_rows(
    *,
    lines: list[str],
    cursor: int,
    count: int,
    label: str,
) -> tuple[tuple[tuple[float, ...], ...], int]:
    end = cursor + count
    if end > len(lines):
        raise DoscarParseError(f"{label} is truncated")
    rows: list[tuple[float, ...]] = []
    for index, line in enumerate(lines[cursor:end], start=1):
        tokens = line.split()
        if not tokens:
            raise DoscarParseError(f"{label} row {index} is blank")
        try:
            row = tuple(_float(token) for token in tokens)
        except ValueError as error:
            raise DoscarParseError(f"{label} row {index} contains non-numeric data") from error
        rows.append(row)
    return tuple(rows), end


def _float(token: str) -> float:
    value = float(token.replace("D", "E").replace("d", "e"))
    if not isfinite(value):
        raise ValueError("non-finite numeric token")
    return value


def _require_uniform_columns(
    rows: tuple[tuple[float, ...], ...],
    expected: int,
    label: str,
) -> None:
    if any(len(row) != expected for row in rows):
        raise DoscarParseError(f"{label} has an unsupported or inconsistent column layout")


def _system_series(
    rows: tuple[tuple[float, ...], ...],
    spin_treatment: SpinTreatment,
) -> tuple[DosSeries, ...]:
    if spin_treatment is SpinTreatment.UNPOLARIZED:
        return (
            DosSeries(
                scope=ProjectionScope.SYSTEM,
                spin=SpinChannel.TOTAL,
                values=tuple(row[1] for row in rows),
            ),
        )
    return (
        DosSeries(
            scope=ProjectionScope.SYSTEM,
            spin=SpinChannel.UP,
            values=tuple(row[1] for row in rows),
        ),
        DosSeries(
            scope=ProjectionScope.SYSTEM,
            spin=SpinChannel.DOWN,
            values=tuple(row[2] for row in rows),
        ),
    )


def _validate_site_header(site: _DosHeader, total: _DosHeader, ion_index: int) -> None:
    if site.nedos != total.nedos:
        raise DoscarParseError(f"site {ion_index} NEDOS differs from total DOS")
    comparisons = (
        (site.emax_ev, total.emax_ev, "EMAX"),
        (site.emin_ev, total.emin_ev, "EMIN"),
        (site.fermi_energy_ev, total.fermi_energy_ev, "E_F"),
    )
    for observed, expected, field_name in comparisons:
        if not isclose(observed, expected, rel_tol=0.0, abs_tol=1.0e-8):
            raise DoscarParseError(f"site {ion_index} {field_name} differs from total DOS")


def _validate_site_energies(
    rows: tuple[tuple[float, ...], ...],
    energies: tuple[float, ...],
    ion_index: int,
) -> None:
    if len(rows) != len(energies):
        raise DoscarParseError(f"site {ion_index} energy grid length differs from total DOS")
    if any(
        not isclose(row[0], energy, rel_tol=0.0, abs_tol=1.0e-8)
        for row, energy in zip(rows, energies, strict=True)
    ):
        raise DoscarParseError(f"site {ion_index} energy grid differs from total DOS")


def _site_series(
    *,
    rows: tuple[tuple[float, ...], ...],
    atom: _AtomBinding,
    spin_treatment: SpinTreatment,
    ion_index: int,
) -> tuple[DosSeries, ...]:
    width = len(rows[0])
    if any(len(row) != width for row in rows):
        raise DoscarParseError(f"site {ion_index} DOS columns are inconsistent")
    payload_columns = width - 1
    divisor = 1 if spin_treatment is SpinTreatment.UNPOLARIZED else 2
    if payload_columns < divisor or payload_columns % divisor:
        raise DoscarParseError(f"site {ion_index} DOS spin/orbital columns are malformed")
    orbital_count = payload_columns // divisor
    orbitals = _ORBITALS.get(orbital_count)
    if orbitals is None:
        raise DoscarParseError(
            f"site {ion_index} DOS orbital layout is unsupported; only s/p/d LORBIT=11 is accepted"
        )

    series: list[DosSeries] = []
    for orbital_index, orbital in enumerate(orbitals):
        if spin_treatment is SpinTreatment.UNPOLARIZED:
            column = 1 + orbital_index
            series.append(
                DosSeries(
                    scope=ProjectionScope.ATOM,
                    spin=SpinChannel.TOTAL,
                    values=tuple(row[column] for row in rows),
                    atom_uid=atom.atom_uid,
                    element=atom.element,
                    orbital=orbital,
                )
            )
            continue
        up_column = 1 + 2 * orbital_index
        down_column = up_column + 1
        series.extend(
            (
                DosSeries(
                    scope=ProjectionScope.ATOM,
                    spin=SpinChannel.UP,
                    values=tuple(row[up_column] for row in rows),
                    atom_uid=atom.atom_uid,
                    element=atom.element,
                    orbital=orbital,
                ),
                DosSeries(
                    scope=ProjectionScope.ATOM,
                    spin=SpinChannel.DOWN,
                    values=tuple(row[down_column] for row in rows),
                    atom_uid=atom.atom_uid,
                    element=atom.element,
                    orbital=orbital,
                ),
            )
        )
    return tuple(series)


def _parse_atom_index_map(
    *,
    body: bytes,
    structure_snapshot_id: StructureSnapshotId,
) -> tuple[_AtomBinding, ...]:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DoscarParseError("atom-index-map.json must be valid UTF-8 JSON") from error
    if not isinstance(payload, dict):
        raise DoscarParseError("atom-index-map.json root must be an object")
    if payload.get("format") != "ecatvasp-v03-atom-index-map" or payload.get("version") != 1:
        raise DoscarParseError("unsupported atom-index-map.json format/version")
    if payload.get("structure_snapshot_id") != str(structure_snapshot_id):
        raise DoscarParseError("atom index map belongs to another StructureSnapshot")
    for field_name in ("structure_sha256", "poscar_sha256"):
        raw_hash = payload.get(field_name)
        if not isinstance(raw_hash, str):
            raise DoscarParseError(f"atom index map {field_name} must be a SHA-256 string")
        _validate_sha256(raw_hash, field_name)

    raw_entries = payload.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise DoscarParseError("atom index map requires non-empty entries")
    bindings: list[_AtomBinding] = []
    elements: list[str] = []
    for expected_index, raw in enumerate(raw_entries):
        if not isinstance(raw, dict):
            raise DoscarParseError("atom index map entry must be an object")
        if raw.get("poscar_index") != expected_index or raw.get("vasp_ordinal") != expected_index + 1:
            raise DoscarParseError("atom index map indices/ordinals are not contiguous")
        raw_uid = raw.get("atom_uid")
        element = raw.get("element")
        if not isinstance(raw_uid, str) or not isinstance(element, str) or not element.strip():
            raise DoscarParseError("atom index map entry requires atom_uid and element")
        try:
            atom_uid = AtomUid(UUID(raw_uid))
        except ValueError as error:
            raise DoscarParseError("atom index map atom_uid is not a UUID") from error
        bindings.append(_AtomBinding(atom_uid=atom_uid, element=element))
        elements.append(element)
    atom_uids = tuple(item.atom_uid for item in bindings)
    if len(atom_uids) != len(set(atom_uids)):
        raise DoscarParseError("atom index map atom_uids must be unique")

    species_order = payload.get("species_order")
    species_counts = payload.get("species_counts")
    if not isinstance(species_order, list) or not all(
        isinstance(value, str) and value.strip() for value in species_order
    ):
        raise DoscarParseError("atom index map species_order is invalid")
    if not isinstance(species_counts, list) or any(
        isinstance(value, bool) or not isinstance(value, int) or value < 1
        for value in species_counts
    ):
        raise DoscarParseError("atom index map species_counts are invalid")
    if len(species_order) != len(species_counts):
        raise DoscarParseError("atom index map species metadata lengths differ")
    expanded = tuple(
        element
        for element, count in zip(species_order, species_counts, strict=True)
        for _ in range(count)
    )
    if expanded != tuple(elements):
        raise DoscarParseError("atom index map species metadata does not match entry order")
    return tuple(bindings)


def _validate_sha256(value: str, field_name: str) -> str:
    normalized = value.lower()
    valid_hex = all(character in "0123456789abcdef" for character in normalized)
    if len(normalized) != 64 or not valid_hex:
        raise DoscarParseError(f"{field_name} must be a 64-character hexadecimal SHA-256 digest")
    return normalized

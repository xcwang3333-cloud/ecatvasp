"""Stable structure I/O boundary for the ECatVASP Model Studio."""

from __future__ import annotations

import hashlib
import io
import json
import math
import warnings
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import numpy as np
from ase import Atoms  # type: ignore[import-untyped]
from ase.constraints import FixAtoms, FixScaled  # type: ignore[import-untyped]
from ase.data import atomic_numbers  # type: ignore[import-untyped]
from ase.io import read as ase_read  # type: ignore[import-untyped]
from ase.io import write as ase_write  # type: ignore[import-untyped]

from ecatvasp.domain import (
    AtomUid,
    Lattice,
    StructureOrigin,
    StructureSite,
    StructureSnapshot,
    new_atom_uid,
)

Vector3 = tuple[float, float, float]
SelectiveFlags = tuple[bool, bool, bool]

_IDENTITY_SIDECAR_SCHEMA = "ecatvasp-structure-identity-v1"
_IDENTITY_ARRAY = "atom_uid"
_SELECTIVE_ARRAY = "ecatvasp_selective_dynamics"


class StructureIOError(ValueError):
    """Raised when a structure cannot be imported/exported without guessing."""


class StructureFormat(StrEnum):
    """Structure formats supported by the v0.2 Model Studio foundation."""

    POSCAR = "poscar"
    CIF = "cif"
    XYZ = "xyz"
    EXTXYZ = "extxyz"


class AtomIdentityStatus(StrEnum):
    """How atom identities were assigned while importing a structure."""

    NEW = "new_identity"
    PRESERVED_SIDECAR = "preserved_sidecar"
    PRESERVED_EMBEDDED = "preserved_embedded"


@dataclass(frozen=True, slots=True)
class SelectiveDynamics:
    """VASP selective-dynamics mobility flags kept outside the domain geometry."""

    flags: tuple[SelectiveFlags, ...]

    def __post_init__(self) -> None:
        if not self.flags:
            raise ValueError("selective-dynamics flags must not be empty")


@dataclass(frozen=True, slots=True)
class StructureSourceMetadata:
    """Source-format semantics that are not part of immutable domain geometry."""

    format: StructureFormat
    source_name: str | None = None
    coordinate_mode: str | None = None
    comment: str | None = None
    identity_status: AtomIdentityStatus = AtomIdentityStatus.NEW
    selective_dynamics: SelectiveDynamics | None = None
    validation_warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class StructureDocument:
    """ECatVASP StructureSnapshot plus lossless structure-I/O metadata."""

    snapshot: StructureSnapshot
    metadata: StructureSourceMetadata

    def __post_init__(self) -> None:
        selective = self.metadata.selective_dynamics
        if selective is not None and len(selective.flags) != len(self.snapshot.sites):
            raise ValueError("selective-dynamics flags must match the snapshot atom count")


def import_structure(
    path: Path | str,
    *,
    format: StructureFormat | str | None = None,
) -> StructureDocument:
    """Import a supported structure and restore ECatVASP identity when verifiable."""

    source = Path(path)
    if not source.is_file():
        raise StructureIOError("structure import source must be an existing file")
    text = source.read_text(encoding="utf-8")
    resolved = _resolve_format(source, text=text, explicit=format)
    document = parse_structure(text, format=resolved, source_name=source.name)

    sidecar = _sidecar_path(source)
    if sidecar.is_file() and document.metadata.identity_status is AtomIdentityStatus.NEW:
        document = _restore_sidecar(document, text=text, sidecar_path=sidecar)
    return document


def parse_structure(
    text: str,
    *,
    format: StructureFormat | str,
    source_name: str | None = None,
) -> StructureDocument:
    """Parse text through an ASE adapter and return ECatVASP objects only."""

    resolved = StructureFormat(format)
    atoms, adapter_warnings = _ase_parse(text, resolved)
    _validate_ase_structure(atoms, resolved)

    embedded_uids = _embedded_atom_uids(atoms) if resolved is StructureFormat.EXTXYZ else None
    snapshot = _snapshot_from_ase(
        atoms,
        label=source_name,
        atom_uids=embedded_uids,
    )
    identity_status = (
        AtomIdentityStatus.PRESERVED_EMBEDDED
        if embedded_uids is not None
        else AtomIdentityStatus.NEW
    )

    selective: SelectiveDynamics | None = None
    if resolved is StructureFormat.POSCAR:
        selective = _selective_from_vasp_constraints(atoms)
    elif resolved is StructureFormat.EXTXYZ:
        selective = _embedded_selective_dynamics(atoms)

    metadata = StructureSourceMetadata(
        format=resolved,
        source_name=source_name,
        coordinate_mode=_source_coordinate_mode(text, resolved),
        comment=_source_comment(text, resolved),
        identity_status=identity_status,
        selective_dynamics=selective,
        validation_warnings=adapter_warnings,
    )
    return StructureDocument(snapshot=snapshot, metadata=metadata)


def export_structure(
    document: StructureDocument | StructureSnapshot,
    path: Path | str,
    *,
    format: StructureFormat | str | None = None,
    write_sidecar: bool = True,
) -> Path:
    """Export a structure and persist identity metadata when the format cannot."""

    target = Path(path)
    resolved = _resolve_format(target, explicit=format)
    coerced = _coerce_document(document, format=resolved)
    text, exported_order = _serialize_with_order(coerced, resolved)

    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)

    if write_sidecar and resolved is not StructureFormat.EXTXYZ:
        _write_sidecar(
            path=_sidecar_path(target),
            document=coerced,
            text=text,
            format=resolved,
            exported_order=exported_order,
        )
    return target


def serialize_structure(
    document: StructureDocument | StructureSnapshot,
    *,
    format: StructureFormat | str,
) -> str:
    """Serialize a structure without filesystem side effects."""

    resolved = StructureFormat(format)
    coerced = _coerce_document(document, format=resolved)
    text, _ = _serialize_with_order(coerced, resolved)
    return text


def _ase_parse(text: str, format: StructureFormat) -> tuple[Atoms, tuple[str, ...]]:
    ase_format = {
        StructureFormat.POSCAR: "vasp",
        StructureFormat.CIF: "cif",
        StructureFormat.XYZ: "xyz",
        StructureFormat.EXTXYZ: "extxyz",
    }[format]
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            parsed = ase_read(io.StringIO(text), format=ase_format, index=-1)
    except Exception as error:
        raise StructureIOError(f"failed to parse {format.value} structure") from error
    if not isinstance(parsed, Atoms):
        raise StructureIOError("structure input must contain exactly one atomic structure")
    messages = tuple(str(item.message) for item in caught)
    return parsed, messages


def _validate_ase_structure(atoms: Atoms, format: StructureFormat) -> None:
    if len(atoms) == 0:
        raise StructureIOError("structure must contain at least one atom")
    for symbol in atoms.get_chemical_symbols():
        if symbol not in atomic_numbers:
            raise StructureIOError(f"invalid or unsupported chemical element: {symbol!r}")

    rank = int(atoms.cell.rank)
    periodic = tuple(bool(value) for value in atoms.pbc)
    if rank not in {0, 3}:
        raise StructureIOError(
            "partial-rank cells are ambiguous at the ECatVASP I/O boundary; "
            "provide a full three-vector cell"
        )
    if rank == 0 and any(periodic):
        raise StructureIOError("periodic axes require a full three-vector cell")

    if format is StructureFormat.CIF:
        _validate_cif_occupancy(atoms)


def _validate_cif_occupancy(atoms: Atoms) -> None:
    occupancy = atoms.info.get("occupancy")
    if occupancy is None:
        return
    if not isinstance(occupancy, dict):
        raise StructureIOError("CIF occupancy metadata is malformed")
    for value in occupancy.values():
        if not isinstance(value, dict) or len(value) != 1:
            raise StructureIOError(
                "disordered or mixed-occupancy CIF sites are not supported by StructureSite"
            )
        raw_occupancy = next(iter(value.values()))
        try:
            numeric = float(raw_occupancy)
        except (TypeError, ValueError) as error:
            raise StructureIOError("CIF occupancy value is not numeric") from error
        if not math.isclose(numeric, 1.0, rel_tol=1e-8, abs_tol=1e-8):
            raise StructureIOError(
                "partial-occupancy CIF sites are not supported by StructureSite"
            )


def _snapshot_from_ase(
    atoms: Atoms,
    *,
    label: str | None,
    atom_uids: tuple[AtomUid, ...] | None,
) -> StructureSnapshot:
    symbols = tuple(atoms.get_chemical_symbols())
    identities = atom_uids or tuple(new_atom_uid() for _ in symbols)
    if len(identities) != len(symbols):
        raise StructureIOError("atom_uid count does not match the atom count")

    rank = int(atoms.cell.rank)
    if rank == 3:
        vectors = _matrix_to_lattice_vectors(atoms.cell.array)
        lattice = Lattice(vectors=vectors)
        fractional = _matrix_to_vectors(atoms.get_scaled_positions(wrap=False))
    else:
        lattice = _identity_lattice()
        fractional = _matrix_to_vectors(atoms.get_positions())

    sites = tuple(
        StructureSite(
            atom_uid=atom_uid,
            element=symbol,
            fractional_coords=coords,
        )
        for atom_uid, symbol, coords in zip(
            identities,
            symbols,
            fractional,
            strict=True,
        )
    )
    return StructureSnapshot(
        lattice=lattice,
        sites=sites,
        label=label,
        origin=StructureOrigin.IMPORTED,
        periodic=cast(tuple[bool, bool, bool], tuple(bool(value) for value in atoms.pbc)),
    )


def _embedded_atom_uids(atoms: Atoms) -> tuple[AtomUid, ...] | None:
    raw = atoms.arrays.get(_IDENTITY_ARRAY)
    if raw is None:
        return None
    if raw.ndim != 1 or len(raw) != len(atoms):
        raise StructureIOError("extXYZ atom_uid property must contain one value per atom")
    try:
        result = tuple(AtomUid(UUID(str(value))) for value in raw.tolist())
    except ValueError as error:
        raise StructureIOError("invalid embedded extXYZ atom_uid") from error
    if len(set(result)) != len(result):
        raise StructureIOError("embedded extXYZ atom_uids must be unique")
    return result


def _embedded_selective_dynamics(atoms: Atoms) -> SelectiveDynamics | None:
    raw = atoms.arrays.get(_SELECTIVE_ARRAY)
    if raw is None:
        return None
    if raw.shape != (len(atoms), 3):
        raise StructureIOError("extXYZ selective-dynamics property must have shape N x 3")
    flags = tuple((bool(row[0]), bool(row[1]), bool(row[2])) for row in raw.tolist())
    return SelectiveDynamics(flags=flags)


def _selective_from_vasp_constraints(atoms: Atoms) -> SelectiveDynamics | None:
    if not atoms.constraints:
        return None
    mobility = [[True, True, True] for _ in range(len(atoms))]
    for constraint in atoms.constraints:
        if isinstance(constraint, FixAtoms):
            for index in np.atleast_1d(constraint.index).tolist():
                mobility[int(index)] = [False, False, False]
            continue
        if isinstance(constraint, FixScaled):
            mask = tuple(bool(value) for value in np.asarray(constraint.mask).tolist())
            if len(mask) != 3:
                raise StructureIOError("ASE returned an invalid FixScaled mask")
            for index in np.atleast_1d(constraint.index).tolist():
                mobility[int(index)] = [not mask[0], not mask[1], not mask[2]]
            continue
        raise StructureIOError(
            f"unsupported ASE constraint reconstructed from POSCAR: {type(constraint).__name__}"
        )
    flags = tuple((row[0], row[1], row[2]) for row in mobility)
    return SelectiveDynamics(flags=flags)


def _serialize_with_order(
    document: StructureDocument,
    format: StructureFormat,
) -> tuple[str, tuple[int, ...]]:
    _validate_export_target(document.snapshot, format)
    order = _export_order(document.snapshot, format)
    atoms = _ase_from_snapshot(document, order=order, format=format)
    ase_format = {
        StructureFormat.POSCAR: "vasp",
        StructureFormat.CIF: "cif",
        StructureFormat.XYZ: "xyz",
        StructureFormat.EXTXYZ: "extxyz",
    }[format]

    try:
        if format is StructureFormat.CIF:
            binary_stream = io.BytesIO()
            ase_write(binary_stream, atoms, format=ase_format)
            text = binary_stream.getvalue().decode("latin-1")
        else:
            text_stream = io.StringIO()
            if format is StructureFormat.POSCAR:
                ase_write(
                    text_stream,
                    atoms,
                    format=ase_format,
                    direct=True,
                    sort=False,
                    vasp5=True,
                )
            else:
                ase_write(text_stream, atoms, format=ase_format)
            text = text_stream.getvalue()
    except Exception as error:
        raise StructureIOError(f"failed to serialize {format.value} structure") from error
    if format is StructureFormat.POSCAR:
        text = _replace_poscar_comment(text, document)
    return _normalize_newline_ending(text), order


def _validate_export_target(snapshot: StructureSnapshot, format: StructureFormat) -> None:
    matrix = np.asarray(snapshot.lattice.vectors, dtype=float)
    rank = int(np.linalg.matrix_rank(matrix))
    if format in {StructureFormat.POSCAR, StructureFormat.CIF}:
        if snapshot.periodic != (True, True, True):
            raise StructureIOError(f"{format.value} export requires a fully periodic snapshot")
        if rank != 3:
            raise StructureIOError(f"{format.value} export requires a non-singular lattice")
    if format is StructureFormat.XYZ and rank != 3:
        raise StructureIOError(
            "XYZ export requires a non-singular ECatVASP lattice so its sidecar can "
            "round-trip domain coordinates"
        )


def _export_order(snapshot: StructureSnapshot, format: StructureFormat) -> tuple[int, ...]:
    if format is not StructureFormat.POSCAR:
        return tuple(range(len(snapshot.sites)))
    element_order: list[str] = []
    for site in snapshot.sites:
        if site.element not in element_order:
            element_order.append(site.element)
    return tuple(
        index
        for element in element_order
        for index, site in enumerate(snapshot.sites)
        if site.element == element
    )


def _ase_from_snapshot(
    document: StructureDocument,
    *,
    order: tuple[int, ...],
    format: StructureFormat,
) -> Atoms:
    snapshot = document.snapshot
    cell = np.asarray(snapshot.lattice.vectors, dtype=float)
    fractional = np.asarray(
        [snapshot.sites[index].fractional_coords for index in order],
        dtype=float,
    )
    positions = fractional @ cell
    symbols = [snapshot.sites[index].element for index in order]
    atoms = Atoms(
        symbols=symbols,
        positions=positions,
        cell=cell,
        pbc=snapshot.periodic,
    )

    if format is StructureFormat.POSCAR and document.metadata.selective_dynamics is not None:
        _apply_vasp_constraints(atoms, document.metadata.selective_dynamics, order=order)
    if format is StructureFormat.EXTXYZ:
        uid_values = np.asarray(
            [str(snapshot.sites[index].atom_uid) for index in order],
            dtype=str,
        )
        atoms.new_array(_IDENTITY_ARRAY, uid_values)
        selective = document.metadata.selective_dynamics
        if selective is not None:
            flags = np.asarray([selective.flags[index] for index in order], dtype=bool)
            atoms.new_array(_SELECTIVE_ARRAY, flags)
    return atoms


def _apply_vasp_constraints(
    atoms: Atoms,
    selective: SelectiveDynamics,
    *,
    order: tuple[int, ...],
) -> None:
    fixed_atoms: list[int] = []
    constraints: list[object] = []
    for exported_index, original_index in enumerate(order):
        mobility = selective.flags[original_index]
        fixed = np.asarray([not value for value in mobility], dtype=bool)
        if bool(fixed.all()):
            fixed_atoms.append(exported_index)
        elif bool(fixed.any()):
            constraints.append(FixScaled(exported_index, fixed, atoms.get_cell()))
    if fixed_atoms:
        constraints.append(FixAtoms(indices=fixed_atoms))
    if constraints:
        atoms.set_constraint(constraints)


def _write_sidecar(
    *,
    path: Path,
    document: StructureDocument,
    text: str,
    format: StructureFormat,
    exported_order: tuple[int, ...],
) -> None:
    snapshot = document.snapshot
    selective = document.metadata.selective_dynamics
    payload: dict[str, Any] = {
        "schema": _IDENTITY_SIDECAR_SCHEMA,
        "format": format.value,
        "structure_sha256": _normalized_text_hash(text),
        "atom_uids": [str(snapshot.sites[index].atom_uid) for index in exported_order],
        "elements": [snapshot.sites[index].element for index in exported_order],
        "lattice_angstrom": [list(vector) for vector in snapshot.lattice.vectors],
        "periodic": list(snapshot.periodic),
    }
    if selective is not None:
        payload["selective_dynamics"] = [
            list(selective.flags[index]) for index in exported_order
        ]
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, sort_keys=True, indent=2)
        handle.write("\n")


def _restore_sidecar(
    document: StructureDocument,
    *,
    text: str,
    sidecar_path: Path,
) -> StructureDocument:
    raw = _load_sidecar(sidecar_path)
    if raw.get("format") != document.metadata.format.value:
        raise StructureIOError("structure sidecar format does not match imported structure")
    if raw.get("structure_sha256") != _normalized_text_hash(text):
        raise StructureIOError("structure sidecar hash does not match the structure file")

    raw_uids = raw.get("atom_uids")
    raw_elements = raw.get("elements")
    if not isinstance(raw_uids, list) or not isinstance(raw_elements, list):
        raise StructureIOError("structure sidecar lacks atom identity arrays")
    elements = [site.element for site in document.snapshot.sites]
    if raw_elements != elements or len(raw_uids) != len(elements):
        raise StructureIOError("structure sidecar atom ordering does not match the structure file")
    atom_uids = _parse_sidecar_uids(raw_uids)

    lattice = _parse_sidecar_lattice(raw.get("lattice_angstrom"))
    periodic = _parse_sidecar_periodicity(raw.get("periodic"))
    snapshot = _restore_sidecar_geometry(document, lattice=lattice, periodic=periodic)
    sites = tuple(
        replace(site, atom_uid=atom_uid)
        for site, atom_uid in zip(snapshot.sites, atom_uids, strict=True)
    )
    snapshot = replace(snapshot, sites=sites)

    selective = _parse_sidecar_selective(raw.get("selective_dynamics"), len(sites))
    metadata = document.metadata
    if selective is not None:
        if metadata.selective_dynamics is not None and metadata.selective_dynamics != selective:
            raise StructureIOError(
                "structure sidecar selective dynamics contradict the structure file"
            )
        metadata = replace(metadata, selective_dynamics=selective)
    metadata = replace(metadata, identity_status=AtomIdentityStatus.PRESERVED_SIDECAR)
    return StructureDocument(snapshot=snapshot, metadata=metadata)


def _load_sidecar(path: Path) -> dict[str, object]:
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise StructureIOError("invalid ECatVASP structure sidecar") from error
    if not isinstance(raw, dict) or raw.get("schema") != _IDENTITY_SIDECAR_SCHEMA:
        raise StructureIOError("unsupported ECatVASP structure sidecar schema")
    return cast(dict[str, object], raw)


def _parse_sidecar_uids(values: list[object]) -> tuple[AtomUid, ...]:
    try:
        result = tuple(AtomUid(UUID(str(value))) for value in values)
    except ValueError as error:
        raise StructureIOError("structure sidecar contains an invalid atom_uid") from error
    if len(set(result)) != len(result):
        raise StructureIOError("structure sidecar atom_uids must be unique")
    return result


def _parse_sidecar_lattice(value: object) -> Lattice:
    if not isinstance(value, list) or len(value) != 3:
        raise StructureIOError("structure sidecar lattice must contain three vectors")
    vectors: list[Vector3] = []
    for row in value:
        if not isinstance(row, list) or len(row) != 3:
            raise StructureIOError("structure sidecar lattice vector must have three values")
        try:
            vector = (float(row[0]), float(row[1]), float(row[2]))
        except (TypeError, ValueError) as error:
            raise StructureIOError(
                "structure sidecar lattice contains a non-numeric value"
            ) from error
        if not all(math.isfinite(component) for component in vector):
            raise StructureIOError("structure sidecar lattice values must be finite")
        vectors.append(vector)
    return Lattice(vectors=(vectors[0], vectors[1], vectors[2]))


def _parse_sidecar_periodicity(value: object) -> tuple[bool, bool, bool]:
    if (
        not isinstance(value, list)
        or len(value) != 3
        or any(not isinstance(item, bool) for item in value)
    ):
        raise StructureIOError("structure sidecar periodicity must contain three booleans")
    return (value[0], value[1], value[2])


def _parse_sidecar_selective(value: object, atom_count: int) -> SelectiveDynamics | None:
    if value is None:
        return None
    if not isinstance(value, list) or len(value) != atom_count:
        raise StructureIOError("invalid selective_dynamics in structure sidecar")
    flags: list[SelectiveFlags] = []
    for row in value:
        if (
            not isinstance(row, list)
            or len(row) != 3
            or any(not isinstance(item, bool) for item in row)
        ):
            raise StructureIOError("invalid selective_dynamics in structure sidecar")
        flags.append((row[0], row[1], row[2]))
    return SelectiveDynamics(flags=tuple(flags))


def _restore_sidecar_geometry(
    document: StructureDocument,
    *,
    lattice: Lattice,
    periodic: tuple[bool, bool, bool],
) -> StructureSnapshot:
    snapshot = document.snapshot
    if document.metadata.format is not StructureFormat.XYZ:
        parsed_lattice = np.asarray(snapshot.lattice.vectors, dtype=float)
        stored_lattice = np.asarray(lattice.vectors, dtype=float)
        if not np.allclose(parsed_lattice, stored_lattice, rtol=1e-8, atol=1e-8):
            raise StructureIOError("structure sidecar lattice contradicts the structure file")
        if periodic != snapshot.periodic:
            raise StructureIOError("structure sidecar periodicity contradicts the structure file")
        return snapshot

    stored_lattice = np.asarray(lattice.vectors, dtype=float)
    if int(np.linalg.matrix_rank(stored_lattice)) != 3:
        raise StructureIOError("XYZ sidecar lattice must be non-singular")
    cartesian = np.asarray([site.fractional_coords for site in snapshot.sites], dtype=float)
    fractional = np.linalg.solve(stored_lattice.T, cartesian.T).T
    sites = tuple(
        replace(
            site,
            fractional_coords=(float(coords[0]), float(coords[1]), float(coords[2])),
        )
        for site, coords in zip(snapshot.sites, fractional, strict=True)
    )
    return replace(snapshot, lattice=lattice, periodic=periodic, sites=sites)


def _coerce_document(
    value: StructureDocument | StructureSnapshot,
    *,
    format: StructureFormat,
) -> StructureDocument:
    if isinstance(value, StructureDocument):
        return value
    return StructureDocument(
        snapshot=value,
        metadata=StructureSourceMetadata(format=format),
    )


def _resolve_format(
    path: Path,
    *,
    text: str | None = None,
    explicit: StructureFormat | str | None = None,
) -> StructureFormat:
    if explicit is not None:
        return StructureFormat(explicit)
    name = path.name.casefold()
    suffix = path.suffix.casefold()
    if name in {"poscar", "contcar"} or suffix in {".vasp", ".poscar"}:
        return StructureFormat.POSCAR
    if suffix == ".cif":
        return StructureFormat.CIF
    if suffix == ".extxyz":
        return StructureFormat.EXTXYZ
    if suffix == ".xyz":
        if text is not None and _looks_like_extxyz(text):
            return StructureFormat.EXTXYZ
        return StructureFormat.XYZ
    raise StructureIOError("cannot infer structure format; pass format explicitly")


def _looks_like_extxyz(text: str) -> bool:
    lines = text.splitlines()
    if len(lines) < 2:
        return False
    header = lines[1]
    return "Properties=" in header or "Lattice=" in header or "pbc=" in header


def _source_coordinate_mode(text: str, format: StructureFormat) -> str | None:
    if format in {StructureFormat.XYZ, StructureFormat.EXTXYZ}:
        return "cartesian"
    if format is not StructureFormat.POSCAR:
        return None
    lines = text.splitlines()
    if len(lines) < 7:
        return None
    cursor = 6 if _line_is_integer_counts(lines[5]) else 7
    if cursor >= len(lines):
        return None
    if lines[cursor].strip().casefold().startswith("s"):
        cursor += 1
    if cursor >= len(lines):
        return None
    mode = lines[cursor].strip().casefold()
    if mode.startswith("d"):
        return "direct"
    if mode.startswith("c") or mode.startswith("k"):
        return "cartesian"
    return None


def _line_is_integer_counts(line: str) -> bool:
    tokens = line.split()
    if not tokens:
        return False
    try:
        return all(int(token) > 0 for token in tokens)
    except ValueError:
        return False


def _source_comment(text: str, format: StructureFormat) -> str | None:
    lines = text.splitlines()
    if format is StructureFormat.POSCAR and lines:
        return lines[0].strip() or None
    if format is StructureFormat.XYZ and len(lines) >= 2:
        return lines[1].strip() or None
    return None


def _replace_poscar_comment(text: str, document: StructureDocument) -> str:
    comment = document.metadata.comment or document.snapshot.label
    if not comment:
        return text
    safe_comment = " ".join(comment.splitlines()).strip()
    if not safe_comment:
        return text
    lines = text.splitlines()
    if not lines:
        return text
    lines[0] = safe_comment
    return "\n".join(lines) + "\n"


def _matrix_to_lattice_vectors(matrix: object) -> tuple[Vector3, Vector3, Vector3]:
    rows = np.asarray(matrix, dtype=float)
    if rows.shape != (3, 3) or not np.isfinite(rows).all():
        raise StructureIOError("adapter returned an invalid lattice")
    return (
        (float(rows[0, 0]), float(rows[0, 1]), float(rows[0, 2])),
        (float(rows[1, 0]), float(rows[1, 1]), float(rows[1, 2])),
        (float(rows[2, 0]), float(rows[2, 1]), float(rows[2, 2])),
    )


def _matrix_to_vectors(matrix: object) -> tuple[Vector3, ...]:
    rows = np.asarray(matrix, dtype=float)
    if rows.ndim != 2 or rows.shape[1] != 3 or not np.isfinite(rows).all():
        raise StructureIOError("adapter returned invalid atomic coordinates")
    return tuple((float(row[0]), float(row[1]), float(row[2])) for row in rows)


def _identity_lattice() -> Lattice:
    return Lattice(vectors=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)))


def _sidecar_path(path: Path) -> Path:
    return path.with_name(path.name + ".ecatvasp.json")


def _normalized_text_hash(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _normalize_newline_ending(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return normalized if normalized.endswith("\n") else normalized + "\n"

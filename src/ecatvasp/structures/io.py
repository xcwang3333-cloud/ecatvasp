"""Stable structure import/export boundary for ECatVASP Model Studio."""

from __future__ import annotations

import hashlib
import json
import math
import re
import shlex
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import UUID

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
_ELEMENTS = frozenset(
    """
    H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn
    Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce
    Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn
    Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl
    Mc Lv Ts Og
    """.split()
)


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
    """VASP selective-dynamics flags kept outside the frozen geometry domain."""

    flags: tuple[SelectiveFlags, ...]

    def __post_init__(self) -> None:
        if not self.flags:
            raise ValueError("selective-dynamics flags must not be empty")


@dataclass(frozen=True, slots=True)
class StructureSourceMetadata:
    """Source-format semantics that are not part of immutable scientific geometry."""

    format: StructureFormat
    source_name: str | None = None
    coordinate_mode: str | None = None
    comment: str | None = None
    identity_status: AtomIdentityStatus = AtomIdentityStatus.NEW
    selective_dynamics: SelectiveDynamics | None = None


@dataclass(frozen=True, slots=True)
class StructureDocument:
    """ECatVASP structure snapshot plus lossless I/O metadata."""

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
    """Import one supported structure file and restore atom identity when verifiable."""

    source = Path(path)
    if not source.is_file():
        raise StructureIOError("structure import source must be an existing file")
    text = source.read_text(encoding="utf-8")
    resolved_format = _resolve_format(source, text=text, explicit=format)
    document = parse_structure(text, format=resolved_format, source_name=source.name)

    sidecar_path = _sidecar_path(source)
    if sidecar_path.is_file() and document.metadata.identity_status is AtomIdentityStatus.NEW:
        document = _apply_sidecar(document, text=text, sidecar_path=sidecar_path)
    return document


def parse_structure(
    text: str,
    *,
    format: StructureFormat | str,
    source_name: str | None = None,
) -> StructureDocument:
    """Parse structure text into ECatVASP DTO/domain objects only."""

    resolved = StructureFormat(format)
    if resolved is StructureFormat.POSCAR:
        return _parse_poscar(text, source_name=source_name)
    if resolved is StructureFormat.CIF:
        return _parse_cif(text, source_name=source_name)
    if resolved is StructureFormat.XYZ:
        return _parse_xyz(text, source_name=source_name, extended=False)
    if resolved is StructureFormat.EXTXYZ:
        return _parse_xyz(text, source_name=source_name, extended=True)
    raise AssertionError("unreachable structure format")


def export_structure(
    document: StructureDocument | StructureSnapshot,
    path: Path | str,
    *,
    format: StructureFormat | str | None = None,
    write_sidecar: bool = True,
) -> Path:
    """Export a structure and write identity metadata when the format cannot carry it."""

    target = Path(path)
    resolved_format = _resolve_format(target, explicit=format)
    coerced = _coerce_document(document, format=resolved_format)
    text, exported_order = _serialize_with_order(coerced, resolved_format)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)

    if write_sidecar and resolved_format is not StructureFormat.EXTXYZ:
        _write_sidecar(
            path=_sidecar_path(target),
            document=coerced,
            text=text,
            format=resolved_format,
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


def _validate_element(symbol: str) -> str:
    cleaned = symbol.strip()
    if cleaned not in _ELEMENTS:
        raise StructureIOError(f"invalid or unsupported chemical element: {symbol!r}")
    return cleaned


def _new_snapshot(
    *,
    lattice: Lattice,
    elements: tuple[str, ...],
    fractional_coords: tuple[Vector3, ...],
    periodic: tuple[bool, bool, bool],
    label: str | None,
    atom_uids: tuple[AtomUid, ...] | None = None,
) -> StructureSnapshot:
    if len(elements) != len(fractional_coords):
        raise StructureIOError("element and coordinate counts are inconsistent")
    if atom_uids is not None and len(atom_uids) != len(elements):
        raise StructureIOError("atom_uid count does not match the atom count")
    identities = atom_uids or tuple(new_atom_uid() for _ in elements)
    sites = tuple(
        StructureSite(
            atom_uid=atom_uid,
            element=_validate_element(element),
            fractional_coords=coords,
        )
        for atom_uid, element, coords in zip(
            identities,
            elements,
            fractional_coords,
            strict=True,
        )
    )
    return StructureSnapshot(
        lattice=lattice,
        sites=sites,
        label=label,
        origin=StructureOrigin.IMPORTED,
        periodic=periodic,
    )


def _parse_poscar(text: str, *, source_name: str | None) -> StructureDocument:
    lines = [line.rstrip() for line in text.splitlines()]
    if len(lines) < 8:
        raise StructureIOError("POSCAR/CONTCAR is too short")
    comment = lines[0].strip() or None

    raw_vectors = tuple(_parse_three_floats(lines[index]) for index in range(2, 5))
    raw_lattice = Lattice(vectors=(raw_vectors[0], raw_vectors[1], raw_vectors[2]))
    scale_vector = _parse_poscar_scale(lines[1], raw_lattice)
    lattice = Lattice(
        vectors=tuple(
            (
                vector[0] * scale_vector[0],
                vector[1] * scale_vector[1],
                vector[2] * scale_vector[2],
            )
            for vector in raw_vectors
        )  # type: ignore[arg-type]
    )
    _require_nonsingular_lattice(lattice)

    symbols = tuple(lines[5].split())
    if not symbols or any(_is_number(token) for token in symbols):
        raise StructureIOError("POSCAR import requires VASP5/6 element symbols")
    elements_by_group = tuple(_validate_element(symbol) for symbol in symbols)
    try:
        counts = tuple(int(token) for token in lines[6].split())
    except ValueError as error:
        raise StructureIOError("invalid POSCAR element counts") from error
    if len(counts) != len(elements_by_group) or any(count <= 0 for count in counts):
        raise StructureIOError("POSCAR element symbols/counts are inconsistent")

    cursor = 7
    has_selective = lines[cursor].strip().casefold().startswith("s")
    if has_selective:
        cursor += 1
    if cursor >= len(lines):
        raise StructureIOError("POSCAR is missing its coordinate mode")
    mode_line = lines[cursor].strip()
    mode = mode_line.casefold()
    cursor += 1
    if not (mode.startswith("d") or mode.startswith("c") or mode.startswith("k")):
        raise StructureIOError(f"unsupported POSCAR coordinate mode: {mode_line}")

    elements = tuple(
        element
        for element, count in zip(elements_by_group, counts, strict=True)
        for _ in range(count)
    )
    if len(lines) < cursor + len(elements):
        raise StructureIOError("POSCAR has fewer coordinate rows than declared atoms")

    raw_coordinates: list[Vector3] = []
    selective_flags: list[SelectiveFlags] = []
    for index in range(len(elements)):
        tokens = lines[cursor + index].split()
        if len(tokens) < 3:
            raise StructureIOError("POSCAR coordinate row requires three numeric components")
        raw_coordinates.append(_parse_three_floats(" ".join(tokens[:3])))
        if has_selective:
            if len(tokens) < 6:
                raise StructureIOError("Selective Dynamics row requires three T/F flags")
            selective_flags.append(tuple(_parse_tf(token) for token in tokens[3:6]))  # type: ignore[arg-type]

    if mode.startswith("d"):
        fractional = tuple(raw_coordinates)
        coordinate_mode = "direct"
    else:
        cartesian = tuple(
            (
                coords[0] * scale_vector[0],
                coords[1] * scale_vector[1],
                coords[2] * scale_vector[2],
            )
            for coords in raw_coordinates
        )
        fractional = tuple(_cartesian_to_fractional(coords, lattice) for coords in cartesian)
        coordinate_mode = "cartesian"

    snapshot = _new_snapshot(
        lattice=lattice,
        elements=elements,
        fractional_coords=fractional,
        periodic=(True, True, True),
        label=source_name,
    )
    selective = SelectiveDynamics(flags=tuple(selective_flags)) if has_selective else None
    return StructureDocument(
        snapshot=snapshot,
        metadata=StructureSourceMetadata(
            format=StructureFormat.POSCAR,
            source_name=source_name,
            coordinate_mode=coordinate_mode,
            comment=comment,
            selective_dynamics=selective,
        ),
    )


def _parse_poscar_scale(line: str, raw_lattice: Lattice) -> Vector3:
    tokens = line.split()
    if len(tokens) not in {1, 3}:
        raise StructureIOError("POSCAR scale line must contain one or three values")
    try:
        values = tuple(float(token) for token in tokens)
    except ValueError as error:
        raise StructureIOError("invalid POSCAR scale factor") from error
    if not all(math.isfinite(value) for value in values):
        raise StructureIOError("POSCAR scale factors must be finite")
    if len(values) == 3:
        if any(value <= 0 for value in values):
            raise StructureIOError("three POSCAR scale factors must all be positive")
        return (values[0], values[1], values[2])
    value = values[0]
    if value == 0:
        raise StructureIOError("POSCAR scale factor must not be zero")
    if value > 0:
        return (value, value, value)
    raw_volume = abs(_determinant(raw_lattice))
    if raw_volume < 1e-14:
        raise StructureIOError("cannot apply negative-volume scaling to a singular lattice")
    factor = (abs(value) / raw_volume) ** (1.0 / 3.0)
    return (factor, factor, factor)


def _parse_cif(text: str, *, source_name: str | None) -> StructureDocument:
    lines = text.splitlines()
    scalars: dict[str, str] = {}
    loop_headers: list[str] = []
    loop_rows: list[list[str]] = []
    index = 0
    while index < len(lines):
        stripped = _strip_cif_comment(lines[index]).strip()
        if not stripped:
            index += 1
            continue
        if stripped.casefold() == "loop_":
            index += 1
            headers: list[str] = []
            while index < len(lines):
                candidate = _strip_cif_comment(lines[index]).strip()
                if candidate.startswith("_"):
                    headers.append(candidate.split()[0].casefold())
                    index += 1
                    continue
                break
            rows: list[list[str]] = []
            while index < len(lines):
                candidate = _strip_cif_comment(lines[index]).strip()
                lowered = candidate.casefold()
                if not candidate:
                    index += 1
                    if rows:
                        break
                    continue
                if candidate.startswith("_") or lowered == "loop_" or lowered.startswith("data_"):
                    break
                tokens = shlex.split(candidate, posix=True)
                if tokens:
                    rows.append(tokens)
                index += 1
            if any(header.startswith("_atom_site_") for header in headers):
                loop_headers = headers
                loop_rows = rows
            continue
        if stripped.startswith("_"):
            tokens = shlex.split(stripped, posix=True)
            if len(tokens) < 2:
                raise StructureIOError(f"CIF scalar value missing for {tokens[0]}")
            scalars[tokens[0].casefold()] = tokens[1]
        index += 1

    required_cell = (
        "_cell_length_a",
        "_cell_length_b",
        "_cell_length_c",
        "_cell_angle_alpha",
        "_cell_angle_beta",
        "_cell_angle_gamma",
    )
    missing = tuple(key for key in required_cell if key not in scalars)
    if missing:
        raise StructureIOError("CIF is missing required cell fields: " + ", ".join(missing))
    a, b, c, alpha, beta, gamma = tuple(_parse_cif_number(scalars[key]) for key in required_cell)
    lattice = _lattice_from_lengths_angles(a, b, c, alpha, beta, gamma)

    if not loop_headers or not loop_rows:
        raise StructureIOError("CIF does not contain an atom-site loop")
    header_index = {header: idx for idx, header in enumerate(loop_headers)}
    symbol_key = (
        "_atom_site_type_symbol"
        if "_atom_site_type_symbol" in header_index
        else "_atom_site_label"
    )
    if symbol_key not in header_index:
        raise StructureIOError("CIF atom-site loop lacks type symbol or label")

    fractional_keys = ("_atom_site_fract_x", "_atom_site_fract_y", "_atom_site_fract_z")
    cartesian_keys = ("_atom_site_cartn_x", "_atom_site_cartn_y", "_atom_site_cartn_z")
    has_fractional = all(key in header_index for key in fractional_keys)
    has_cartesian = all(key in header_index for key in cartesian_keys)
    if not has_fractional and not has_cartesian:
        raise StructureIOError("CIF atom-site loop lacks complete fractional/Cartesian coordinates")

    elements: list[str] = []
    coords: list[Vector3] = []
    for row in loop_rows:
        if len(row) < len(loop_headers):
            raise StructureIOError("CIF atom-site row has fewer columns than declared")
        raw_symbol = row[header_index[symbol_key]]
        symbol = _cif_symbol(raw_symbol)
        elements.append(_validate_element(symbol))
        keys = fractional_keys if has_fractional else cartesian_keys
        parsed = tuple(_parse_cif_number(row[header_index[key]]) for key in keys)
        coords.append((parsed[0], parsed[1], parsed[2]))

    if has_fractional:
        fractional = tuple(coords)
        coordinate_mode = "fractional"
    else:
        fractional = tuple(_cartesian_to_fractional(coord, lattice) for coord in coords)
        coordinate_mode = "cartesian"

    snapshot = _new_snapshot(
        lattice=lattice,
        elements=tuple(elements),
        fractional_coords=fractional,
        periodic=(True, True, True),
        label=source_name,
    )
    return StructureDocument(
        snapshot=snapshot,
        metadata=StructureSourceMetadata(
            format=StructureFormat.CIF,
            source_name=source_name,
            coordinate_mode=coordinate_mode,
        ),
    )


def _strip_cif_comment(line: str) -> str:
    quote: str | None = None
    for index, char in enumerate(line):
        if char in {"'", '"'}:
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
        elif char == "#" and quote is None:
            return line[:index]
    return line


def _parse_cif_number(value: str) -> float:
    token = value.strip()
    if token in {"?", "."}:
        raise StructureIOError("CIF contains an unknown numeric value")
    token = re.sub(r"\([0-9]+\)$", "", token)
    if "/" in token and token.count("/") == 1:
        numerator, denominator = token.split("/", 1)
        try:
            result = float(numerator) / float(denominator)
        except (ValueError, ZeroDivisionError) as error:
            raise StructureIOError(f"invalid CIF numeric value: {value}") from error
    else:
        try:
            result = float(token)
        except ValueError as error:
            raise StructureIOError(f"invalid CIF numeric value: {value}") from error
    if not math.isfinite(result):
        raise StructureIOError("CIF numeric values must be finite")
    return result


def _cif_symbol(value: str) -> str:
    match = re.match(r"^([A-Z][a-z]?)", value.strip())
    if match is None:
        raise StructureIOError(f"cannot infer chemical element from CIF token: {value!r}")
    return match.group(1)


def _parse_xyz(
    text: str,
    *,
    source_name: str | None,
    extended: bool,
) -> StructureDocument:
    lines = text.splitlines()
    if len(lines) < 2:
        raise StructureIOError("XYZ/extXYZ is too short")
    try:
        atom_count = int(lines[0].strip())
    except ValueError as error:
        raise StructureIOError("XYZ first line must be an atom count") from error
    if atom_count <= 0:
        raise StructureIOError("XYZ atom count must be positive")
    if len(lines) < atom_count + 2:
        raise StructureIOError("XYZ has fewer atom rows than declared")
    comment = lines[1].strip() or None

    if not extended:
        elements: list[str] = []
        cartesian: list[Vector3] = []
        for line in lines[2 : 2 + atom_count]:
            tokens = line.split()
            if len(tokens) < 4:
                raise StructureIOError("XYZ atom row requires element and x/y/z")
            elements.append(_validate_element(tokens[0]))
            cartesian.append(_parse_three_floats(" ".join(tokens[1:4])))
        lattice = Lattice(vectors=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)))
        snapshot = _new_snapshot(
            lattice=lattice,
            elements=tuple(elements),
            fractional_coords=tuple(cartesian),
            periodic=(False, False, False),
            label=source_name,
        )
        return StructureDocument(
            snapshot=snapshot,
            metadata=StructureSourceMetadata(
                format=StructureFormat.XYZ,
                source_name=source_name,
                coordinate_mode="cartesian",
                comment=comment,
            ),
        )

    header = _parse_extxyz_header(lines[1])
    lattice = _parse_extxyz_lattice(header.get("Lattice"))
    periodic = _parse_extxyz_pbc(header.get("pbc"))
    properties = _parse_extxyz_properties(header.get("Properties"))
    elements = []
    cartesian = []
    embedded_uids: list[AtomUid] = []
    uid_present = properties.get("atom_uid") is not None
    for line in lines[2 : 2 + atom_count]:
        tokens = shlex.split(line, posix=True)
        expected_columns = sum(count for _, count in properties.values())
        if len(tokens) < expected_columns:
            raise StructureIOError("extXYZ atom row has fewer columns than Properties declares")
        element_slice = properties.get("species")
        pos_slice = properties.get("pos")
        if element_slice is None or pos_slice is None or pos_slice[1] != 3:
            raise StructureIOError("extXYZ Properties must define species and pos:R:3")
        elements.append(_validate_element(tokens[element_slice[0]]))
        pos_start = pos_slice[0]
        cartesian.append(_parse_three_floats(" ".join(tokens[pos_start : pos_start + 3])))
        uid_slice = properties.get("atom_uid")
        if uid_slice is not None:
            if uid_slice[1] != 1:
                raise StructureIOError("extXYZ atom_uid property must have width 1")
            try:
                embedded_uids.append(AtomUid(UUID(tokens[uid_slice[0]])))
            except ValueError as error:
                raise StructureIOError("invalid embedded extXYZ atom_uid") from error

    fractional = tuple(_cartesian_to_fractional(coord, lattice) for coord in cartesian)
    atom_uids = tuple(embedded_uids) if uid_present else None
    snapshot = _new_snapshot(
        lattice=lattice,
        elements=tuple(elements),
        fractional_coords=fractional,
        periodic=periodic,
        label=source_name,
        atom_uids=atom_uids,
    )
    identity_status = (
        AtomIdentityStatus.PRESERVED_EMBEDDED if uid_present else AtomIdentityStatus.NEW
    )
    return StructureDocument(
        snapshot=snapshot,
        metadata=StructureSourceMetadata(
            format=StructureFormat.EXTXYZ,
            source_name=source_name,
            coordinate_mode="cartesian",
            comment=comment,
            identity_status=identity_status,
        ),
    )


def _looks_like_extxyz(text: str) -> bool:
    lines = text.splitlines()
    if len(lines) < 2:
        return False
    return "Properties=" in lines[1] or "Lattice=" in lines[1] or "pbc=" in lines[1]


def _parse_extxyz_header(line: str) -> dict[str, str]:
    values: dict[str, str] = {}
    pattern = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=(\"[^\"]*\"|'[^']*'|\S+)")
    for match in pattern.finditer(line):
        raw_value = match.group(2)
        if len(raw_value) >= 2 and raw_value[0] == raw_value[-1] and raw_value[0] in {"'", '"'}:
            raw_value = raw_value[1:-1]
        values[match.group(1)] = raw_value
    return values


def _parse_extxyz_lattice(value: str | None) -> Lattice:
    if value is None:
        raise StructureIOError("extXYZ requires a Lattice header")
    tokens = value.split()
    if len(tokens) != 9:
        raise StructureIOError("extXYZ Lattice must contain 9 numeric values")
    try:
        numbers = tuple(float(token) for token in tokens)
    except ValueError as error:
        raise StructureIOError("invalid extXYZ Lattice value") from error
    if not all(math.isfinite(number) for number in numbers):
        raise StructureIOError("extXYZ Lattice values must be finite")
    lattice = Lattice(
        vectors=(
            (numbers[0], numbers[1], numbers[2]),
            (numbers[3], numbers[4], numbers[5]),
            (numbers[6], numbers[7], numbers[8]),
        )
    )
    _require_nonsingular_lattice(lattice)
    return lattice


def _parse_extxyz_pbc(value: str | None) -> tuple[bool, bool, bool]:
    if value is None:
        raise StructureIOError("extXYZ requires an explicit pbc header")
    tokens = value.split()
    if len(tokens) != 3:
        raise StructureIOError("extXYZ pbc must contain three boolean values")
    return tuple(_parse_tf(token) for token in tokens)  # type: ignore[return-value]


def _parse_extxyz_properties(value: str | None) -> dict[str, tuple[int, int]]:
    if value is None:
        raise StructureIOError("extXYZ requires a Properties header")
    tokens = value.split(":")
    if len(tokens) % 3 != 0:
        raise StructureIOError("invalid extXYZ Properties descriptor")
    result: dict[str, tuple[int, int]] = {}
    offset = 0
    for index in range(0, len(tokens), 3):
        name = tokens[index]
        try:
            count = int(tokens[index + 2])
        except ValueError as error:
            raise StructureIOError("invalid extXYZ Properties width") from error
        if count <= 0:
            raise StructureIOError("extXYZ Properties width must be positive")
        result[name] = (offset, count)
        offset += count
    return result


def _serialize_with_order(
    document: StructureDocument,
    format: StructureFormat,
) -> tuple[str, tuple[int, ...]]:
    if format is StructureFormat.POSCAR:
        return _serialize_poscar(document)
    if format is StructureFormat.CIF:
        return _serialize_cif(document), tuple(range(len(document.snapshot.sites)))
    if format is StructureFormat.XYZ:
        return _serialize_xyz(document, extended=False), tuple(range(len(document.snapshot.sites)))
    if format is StructureFormat.EXTXYZ:
        return _serialize_xyz(document, extended=True), tuple(range(len(document.snapshot.sites)))
    raise AssertionError("unreachable structure format")


def _serialize_poscar(document: StructureDocument) -> tuple[str, tuple[int, ...]]:
    snapshot = document.snapshot
    if snapshot.periodic != (True, True, True):
        raise StructureIOError("POSCAR export requires a fully periodic snapshot")
    _require_nonsingular_lattice(snapshot.lattice)

    element_order: list[str] = []
    for site in snapshot.sites:
        _validate_element(site.element)
        if site.element not in element_order:
            element_order.append(site.element)
    exported_order = tuple(
        index
        for element in element_order
        for index, site in enumerate(snapshot.sites)
        if site.element == element
    )
    counts = tuple(
        sum(1 for site in snapshot.sites if site.element == element)
        for element in element_order
    )

    lines = [document.metadata.comment or snapshot.label or "ECatVASP structure", "1.0"]
    lines.extend("  " + "  ".join(_format_float(value) for value in vector) for vector in snapshot.lattice.vectors)
    lines.append("  " + "  ".join(element_order))
    lines.append("  " + "  ".join(str(count) for count in counts))
    selective = document.metadata.selective_dynamics
    if selective is not None:
        lines.append("Selective dynamics")
    lines.append("Direct")
    for original_index in exported_order:
        site = snapshot.sites[original_index]
        row = "  " + "  ".join(_format_float(value) for value in site.fractional_coords)
        if selective is not None:
            row += "  " + "  ".join("T" if flag else "F" for flag in selective.flags[original_index])
        lines.append(row)
    return "\n".join(lines) + "\n", exported_order


def _serialize_cif(document: StructureDocument) -> str:
    snapshot = document.snapshot
    if snapshot.periodic != (True, True, True):
        raise StructureIOError("CIF export requires a fully periodic snapshot")
    a, b, c, alpha, beta, gamma = _lengths_angles(snapshot.lattice)
    lines = [
        "data_ecatvasp",
        f"_cell_length_a {_format_float(a)}",
        f"_cell_length_b {_format_float(b)}",
        f"_cell_length_c {_format_float(c)}",
        f"_cell_angle_alpha {_format_float(alpha)}",
        f"_cell_angle_beta {_format_float(beta)}",
        f"_cell_angle_gamma {_format_float(gamma)}",
        "_symmetry_space_group_name_H-M 'P 1'",
        "loop_",
        "_atom_site_label",
        "_atom_site_type_symbol",
        "_atom_site_fract_x",
        "_atom_site_fract_y",
        "_atom_site_fract_z",
    ]
    counters: dict[str, int] = {}
    for site in snapshot.sites:
        _validate_element(site.element)
        counters[site.element] = counters.get(site.element, 0) + 1
        label = f"{site.element}{counters[site.element]}"
        x, y, z = site.fractional_coords
        lines.append(
            f"{label} {site.element} {_format_float(x)} {_format_float(y)} {_format_float(z)}"
        )
    return "\n".join(lines) + "\n"


def _serialize_xyz(document: StructureDocument, *, extended: bool) -> str:
    snapshot = document.snapshot
    lines = [str(len(snapshot.sites))]
    if extended:
        lattice_values = " ".join(
            _format_float(component)
            for vector in snapshot.lattice.vectors
            for component in vector
        )
        pbc = " ".join("T" if value else "F" for value in snapshot.periodic)
        lines.append(
            f'Lattice="{lattice_values}" Properties=species:S:1:pos:R:3:atom_uid:S:1 pbc="{pbc}"'
        )
        for site in snapshot.sites:
            _validate_element(site.element)
            x, y, z = _fractional_to_cartesian(site.fractional_coords, snapshot.lattice)
            lines.append(
                f"{site.element} {_format_float(x)} {_format_float(y)} {_format_float(z)} {site.atom_uid}"
            )
    else:
        lines.append(document.metadata.comment or snapshot.label or "ECatVASP structure")
        for site in snapshot.sites:
            _validate_element(site.element)
            x, y, z = _fractional_to_cartesian(site.fractional_coords, snapshot.lattice)
            lines.append(f"{site.element} {_format_float(x)} {_format_float(y)} {_format_float(z)}")
    return "\n".join(lines) + "\n"


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
        "periodic": list(snapshot.periodic),
    }
    if selective is not None:
        payload["selective_dynamics"] = [
            list(selective.flags[index]) for index in exported_order
        ]
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, sort_keys=True, indent=2)
        handle.write("\n")


def _apply_sidecar(
    document: StructureDocument,
    *,
    text: str,
    sidecar_path: Path,
) -> StructureDocument:
    try:
        raw = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise StructureIOError("invalid ECatVASP structure sidecar") from error
    if not isinstance(raw, dict) or raw.get("schema") != _IDENTITY_SIDECAR_SCHEMA:
        raise StructureIOError("unsupported ECatVASP structure sidecar schema")
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
    try:
        atom_uids = tuple(AtomUid(UUID(str(value))) for value in raw_uids)
    except ValueError as error:
        raise StructureIOError("structure sidecar contains an invalid atom_uid") from error
    if len(atom_uids) != len(set(atom_uids)):
        raise StructureIOError("structure sidecar atom_uids must be unique")

    sites = tuple(
        replace(site, atom_uid=atom_uid)
        for site, atom_uid in zip(document.snapshot.sites, atom_uids, strict=True)
    )
    snapshot = replace(document.snapshot, sites=sites)
    metadata = replace(document.metadata, identity_status=AtomIdentityStatus.PRESERVED_SIDECAR)

    raw_selective = raw.get("selective_dynamics")
    if raw_selective is not None:
        if not isinstance(raw_selective, list) or len(raw_selective) != len(sites):
            raise StructureIOError("invalid selective_dynamics in structure sidecar")
        parsed_flags: list[SelectiveFlags] = []
        for row in raw_selective:
            if (
                not isinstance(row, list)
                or len(row) != 3
                or any(not isinstance(value, bool) for value in row)
            ):
                raise StructureIOError("invalid selective_dynamics in structure sidecar")
            parsed_flags.append((row[0], row[1], row[2]))
        sidecar_selective = SelectiveDynamics(flags=tuple(parsed_flags))
        if metadata.selective_dynamics is not None and metadata.selective_dynamics != sidecar_selective:
            raise StructureIOError("structure sidecar selective dynamics contradict file content")
        metadata = replace(metadata, selective_dynamics=sidecar_selective)
    return StructureDocument(snapshot=snapshot, metadata=metadata)


def _sidecar_path(path: Path) -> Path:
    return path.with_name(path.name + ".ecatvasp.json")


def _normalized_text_hash(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _parse_three_floats(line: str) -> Vector3:
    tokens = line.split()
    if len(tokens) != 3:
        raise StructureIOError("expected exactly three numeric components")
    try:
        values = tuple(float(token) for token in tokens)
    except ValueError as error:
        raise StructureIOError("invalid numeric structure coordinate") from error
    if not all(math.isfinite(value) for value in values):
        raise StructureIOError("structure coordinates must be finite")
    return (values[0], values[1], values[2])


def _parse_tf(value: str) -> bool:
    normalized = value.strip().casefold()
    if normalized in {"t", "true", ".true."}:
        return True
    if normalized in {"f", "false", ".false."}:
        return False
    raise StructureIOError(f"invalid boolean flag: {value!r}")


def _is_number(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


def _determinant(lattice: Lattice) -> float:
    a, b, c = lattice.vectors
    return (
        a[0] * (b[1] * c[2] - b[2] * c[1])
        - a[1] * (b[0] * c[2] - b[2] * c[0])
        + a[2] * (b[0] * c[1] - b[1] * c[0])
    )


def _require_nonsingular_lattice(lattice: Lattice) -> None:
    if abs(_determinant(lattice)) < 1e-14:
        raise StructureIOError("structure lattice is singular")


def _cartesian_to_fractional(cartesian: Vector3, lattice: Lattice) -> Vector3:
    _require_nonsingular_lattice(lattice)
    a, b, c = lattice.vectors
    det = _determinant(lattice)
    x, y, z = cartesian
    f1 = (
        x * (b[1] * c[2] - b[2] * c[1])
        - y * (b[0] * c[2] - b[2] * c[0])
        + z * (b[0] * c[1] - b[1] * c[0])
    ) / det
    f2 = (
        -x * (a[1] * c[2] - a[2] * c[1])
        + y * (a[0] * c[2] - a[2] * c[0])
        - z * (a[0] * c[1] - a[1] * c[0])
    ) / det
    f3 = (
        x * (a[1] * b[2] - a[2] * b[1])
        - y * (a[0] * b[2] - a[2] * b[0])
        + z * (a[0] * b[1] - a[1] * b[0])
    ) / det
    return (f1, f2, f3)


def _fractional_to_cartesian(fractional: Vector3, lattice: Lattice) -> Vector3:
    return tuple(
        sum(fractional[basis] * lattice.vectors[basis][axis] for basis in range(3))
        for axis in range(3)
    )  # type: ignore[return-value]


def _lattice_from_lengths_angles(
    a: float,
    b: float,
    c: float,
    alpha_deg: float,
    beta_deg: float,
    gamma_deg: float,
) -> Lattice:
    if any(length <= 0 for length in (a, b, c)):
        raise StructureIOError("CIF cell lengths must be positive")
    if any(not 0 < angle < 180 for angle in (alpha_deg, beta_deg, gamma_deg)):
        raise StructureIOError("CIF cell angles must lie between 0 and 180 degrees")
    alpha = math.radians(alpha_deg)
    beta = math.radians(beta_deg)
    gamma = math.radians(gamma_deg)
    sin_gamma = math.sin(gamma)
    if abs(sin_gamma) < 1e-12:
        raise StructureIOError("CIF gamma angle produces a singular lattice")
    ax = a
    bx = b * math.cos(gamma)
    by = b * sin_gamma
    cx = c * math.cos(beta)
    cy = c * (math.cos(alpha) - math.cos(beta) * math.cos(gamma)) / sin_gamma
    cz_sq = c * c - cx * cx - cy * cy
    if cz_sq < -1e-10:
        raise StructureIOError("CIF cell parameters are geometrically inconsistent")
    cz = math.sqrt(max(0.0, cz_sq))
    lattice = Lattice(vectors=((ax, 0.0, 0.0), (bx, by, 0.0), (cx, cy, cz)))
    _require_nonsingular_lattice(lattice)
    return lattice


def _lengths_angles(lattice: Lattice) -> tuple[float, float, float, float, float, float]:
    _require_nonsingular_lattice(lattice)
    a_vec, b_vec, c_vec = lattice.vectors
    a = _norm(a_vec)
    b = _norm(b_vec)
    c = _norm(c_vec)
    alpha = _angle_degrees(b_vec, c_vec)
    beta = _angle_degrees(a_vec, c_vec)
    gamma = _angle_degrees(a_vec, b_vec)
    return a, b, c, alpha, beta, gamma


def _norm(vector: Vector3) -> float:
    return math.sqrt(sum(component * component for component in vector))


def _angle_degrees(left: Vector3, right: Vector3) -> float:
    denominator = _norm(left) * _norm(right)
    if denominator <= 0:
        raise StructureIOError("cannot calculate angle for zero-length lattice vector")
    cosine = sum(a * b for a, b in zip(left, right, strict=True)) / denominator
    cosine = max(-1.0, min(1.0, cosine))
    return math.degrees(math.acos(cosine))


def _format_float(value: float) -> str:
    if not math.isfinite(value):
        raise StructureIOError("cannot export non-finite structure coordinate")
    return f"{value:.16g}"

"""Deterministic POSCAR preparation with permanent atom_uid identity."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from math import isfinite

from ecatvasp.domain.entities import StructureSnapshot
from ecatvasp.domain.ids import AtomUid, StructureSnapshotId

SelectiveFlags = tuple[bool, bool, bool]


class PoscarPreparationError(ValueError):
    """Raised when a StructureSnapshot cannot be serialized to VASP without guessing."""


@dataclass(frozen=True, slots=True)
class AtomSelectiveFlags:
    """Selective-dynamics override bound to permanent scientific atom identity."""

    atom_uid: AtomUid
    flags: SelectiveFlags

    def __post_init__(self) -> None:
        if len(self.flags) != 3 or any(not isinstance(value, bool) for value in self.flags):
            raise ValueError("selective-dynamics flags must contain exactly three booleans")


@dataclass(frozen=True, slots=True)
class UidSelectiveDynamics:
    """UID-addressed mobility policy resolved only when POSCAR order is materialized."""

    default_flags: SelectiveFlags = (True, True, True)
    overrides: tuple[AtomSelectiveFlags, ...] = ()

    def __post_init__(self) -> None:
        if len(self.default_flags) != 3 or any(
            not isinstance(value, bool) for value in self.default_flags
        ):
            raise ValueError("default_flags must contain exactly three booleans")
        atom_uids = tuple(item.atom_uid for item in self.overrides)
        if len(atom_uids) != len(set(atom_uids)):
            raise ValueError("selective-dynamics overrides must reference unique atom_uids")

    def resolved_flags(self, snapshot: StructureSnapshot) -> tuple[SelectiveFlags, ...]:
        """Resolve mobility flags in immutable snapshot order, failing on missing UIDs."""

        snapshot_uids = {site.atom_uid for site in snapshot.sites}
        missing = tuple(
            item.atom_uid for item in self.overrides if item.atom_uid not in snapshot_uids
        )
        if missing:
            raise PoscarPreparationError(
                "selective-dynamics atom_uid is absent from the supplied StructureSnapshot"
            )
        override_map = {item.atom_uid: item.flags for item in self.overrides}
        return tuple(
            override_map.get(site.atom_uid, self.default_flags) for site in snapshot.sites
        )


@dataclass(frozen=True, slots=True)
class PoscarIndexEntry:
    """One local serialization index mapped back to permanent atom_uid identity."""

    atom_uid: AtomUid
    element: str
    snapshot_index: int
    poscar_index: int

    @property
    def vasp_ordinal(self) -> int:
        """Return the one-based ordinal commonly printed by VASP outputs."""

        return self.poscar_index + 1


@dataclass(frozen=True, slots=True)
class PoscarIndexMap:
    """Bidirectional identity map for one exact POSCAR serialization."""

    structure_snapshot_id: StructureSnapshotId
    entries: tuple[PoscarIndexEntry, ...]

    def __post_init__(self) -> None:
        if not self.entries:
            raise ValueError("POSCAR index map must contain at least one atom")
        atom_uids = tuple(entry.atom_uid for entry in self.entries)
        if len(atom_uids) != len(set(atom_uids)):
            raise ValueError("POSCAR index map atom_uids must be unique")
        indices = tuple(entry.poscar_index for entry in self.entries)
        if indices != tuple(range(len(self.entries))):
            raise ValueError("POSCAR indices must be contiguous and zero-based")

    def poscar_index(self, atom_uid: AtomUid) -> int:
        """Return the zero-based local POSCAR index for a permanent atom_uid."""

        for entry in self.entries:
            if entry.atom_uid == atom_uid:
                return entry.poscar_index
        raise KeyError(atom_uid)

    def atom_uid(self, poscar_index: int) -> AtomUid:
        """Return permanent atom_uid for one zero-based local POSCAR index."""

        if poscar_index < 0 or poscar_index >= len(self.entries):
            raise IndexError("POSCAR index is outside the serialized atom range")
        return self.entries[poscar_index].atom_uid

    def vasp_ordinal(self, atom_uid: AtomUid) -> int:
        """Return the one-based VASP ordinal for a permanent atom_uid."""

        return self.poscar_index(atom_uid) + 1


@dataclass(frozen=True, slots=True)
class PreparedPoscar:
    """Immutable deterministic POSCAR preparation result."""

    structure_snapshot_id: StructureSnapshotId
    text: str
    sha256: str
    species_order: tuple[str, ...]
    species_counts: tuple[int, ...]
    index_map: PoscarIndexMap
    selective_flags: tuple[SelectiveFlags, ...] | None = None

    def __post_init__(self) -> None:
        if self.index_map.structure_snapshot_id != self.structure_snapshot_id:
            raise ValueError("POSCAR index map does not belong to the prepared snapshot")
        if len(self.species_order) != len(self.species_counts):
            raise ValueError("species order and counts must have the same length")
        if sum(self.species_counts) != len(self.index_map.entries):
            raise ValueError("species counts must equal the serialized atom count")
        expected_hash = hashlib.sha256(self.text.encode("utf-8")).hexdigest()
        if self.sha256 != expected_hash:
            raise ValueError("POSCAR sha256 does not match text content")
        if self.selective_flags is not None and len(self.selective_flags) != len(
            self.index_map.entries
        ):
            raise ValueError("selective flags must match the serialized atom count")


def prepare_poscar(
    snapshot: StructureSnapshot,
    *,
    selective_dynamics: UidSelectiveDynamics | None = None,
) -> PreparedPoscar:
    """Prepare deterministic VASP POSCAR text and UID/index mapping.

    POSCAR index is serialization-local and zero-based inside ECatVASP. Permanent
    scientific identity remains atom_uid. The one-based VASP ordinal is derived from
    the local index and is never persisted as scientific identity.
    """

    _validate_vasp_cell(snapshot)
    order, species_order, species_counts = _species_grouped_order(snapshot)
    index_entries = tuple(
        PoscarIndexEntry(
            atom_uid=snapshot.sites[snapshot_index].atom_uid,
            element=snapshot.sites[snapshot_index].element,
            snapshot_index=snapshot_index,
            poscar_index=poscar_index,
        )
        for poscar_index, snapshot_index in enumerate(order)
    )
    index_map = PoscarIndexMap(
        structure_snapshot_id=snapshot.id,
        entries=index_entries,
    )

    snapshot_flags = (
        selective_dynamics.resolved_flags(snapshot)
        if selective_dynamics is not None
        else None
    )
    ordered_flags = (
        tuple(snapshot_flags[index] for index in order)
        if snapshot_flags is not None
        else None
    )
    text = _serialize_poscar(
        snapshot,
        order=order,
        species_order=species_order,
        species_counts=species_counts,
        selective_flags=ordered_flags,
    )
    return PreparedPoscar(
        structure_snapshot_id=snapshot.id,
        text=text,
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        species_order=species_order,
        species_counts=species_counts,
        index_map=index_map,
        selective_flags=ordered_flags,
    )


def _validate_vasp_cell(snapshot: StructureSnapshot) -> None:
    if snapshot.periodic != (True, True, True):
        raise PoscarPreparationError(
            "VASP POSCAR preparation requires a fully periodic cell representation"
        )
    determinant = _cell_determinant(snapshot.lattice.vectors)
    if not isfinite(determinant) or abs(determinant) <= 1e-12:
        raise PoscarPreparationError("VASP POSCAR preparation requires a non-singular cell")


def _cell_determinant(
    vectors: tuple[
        tuple[float, float, float],
        tuple[float, float, float],
        tuple[float, float, float],
    ],
) -> float:
    a, b, c = vectors
    return (
        a[0] * (b[1] * c[2] - b[2] * c[1])
        - a[1] * (b[0] * c[2] - b[2] * c[0])
        + a[2] * (b[0] * c[1] - b[1] * c[0])
    )


def _species_grouped_order(
    snapshot: StructureSnapshot,
) -> tuple[tuple[int, ...], tuple[str, ...], tuple[int, ...]]:
    species_order: list[str] = []
    for site in snapshot.sites:
        if site.element not in species_order:
            species_order.append(site.element)
    order = tuple(
        index
        for element in species_order
        for index, site in enumerate(snapshot.sites)
        if site.element == element
    )
    counts = tuple(
        sum(1 for site in snapshot.sites if site.element == element)
        for element in species_order
    )
    return order, tuple(species_order), counts


def _serialize_poscar(
    snapshot: StructureSnapshot,
    *,
    order: tuple[int, ...],
    species_order: tuple[str, ...],
    species_counts: tuple[int, ...],
    selective_flags: tuple[SelectiveFlags, ...] | None,
) -> str:
    lines = ["ECatVASP", "1.0"]
    lines.extend(
        " ".join(_format_float(value) for value in vector)
        for vector in snapshot.lattice.vectors
    )
    lines.append(" ".join(species_order))
    lines.append(" ".join(str(count) for count in species_counts))
    if selective_flags is not None:
        lines.append("Selective dynamics")
    lines.append("Direct")
    for poscar_index, snapshot_index in enumerate(order):
        site = snapshot.sites[snapshot_index]
        line = " ".join(_format_float(value) for value in site.fractional_coords)
        if selective_flags is not None:
            flags = selective_flags[poscar_index]
            line = f"{line} {' '.join('T' if value else 'F' for value in flags)}"
        lines.append(line)
    return "\n".join(lines) + "\n"


def _format_float(value: float) -> str:
    if abs(value) < 5e-16:
        value = 0.0
    return f"{value:.16f}"

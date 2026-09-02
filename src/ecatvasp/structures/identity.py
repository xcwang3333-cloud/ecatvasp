"""Stable atom identity propagation and structure-revision mapping."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite, sqrt
from typing import Sequence

from ecatvasp.domain import (
    AtomUid,
    Lattice,
    StructureOrigin,
    StructureSite,
    StructureSnapshot,
    StructureSnapshotId,
)

Vector3 = tuple[float, float, float]


class AtomMappingError(ValueError):
    """Raised when atom identities cannot be propagated without ambiguity."""


class AtomMappingMethod(StrEnum):
    """How identities were propagated from a parent structure revision."""

    INDEX_PRESERVING = "index_preserving"
    EXACT_REORDER = "exact_reorder"
    EXPLICIT_REORDER = "explicit_reorder"


@dataclass(frozen=True, slots=True)
class GeometrySite:
    """Transient site geometry before a stable ``atom_uid`` is assigned."""

    element: str
    fractional_coords: Vector3

    def __post_init__(self) -> None:
        if not self.element.strip():
            raise ValueError("element must not be blank")
        if not all(isfinite(component) for component in self.fractional_coords):
            raise ValueError("fractional_coords must contain finite components")


@dataclass(frozen=True, slots=True)
class AtomMappingEntry:
    """One source-to-target atom identity correspondence."""

    source_index: int
    target_index: int
    atom_uid: AtomUid
    element: str
    displacement_angstrom: float

    def __post_init__(self) -> None:
        if self.source_index < 0 or self.target_index < 0:
            raise ValueError("mapping indices must not be negative")
        if not self.element.strip():
            raise ValueError("element must not be blank")
        if self.displacement_angstrom < 0 or not isfinite(self.displacement_angstrom):
            raise ValueError("displacement_angstrom must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class AtomIdentityMapping:
    """Complete atom mapping between two identity-preserving structure revisions."""

    source_snapshot_id: StructureSnapshotId
    target_snapshot_id: StructureSnapshotId
    method: AtomMappingMethod
    entries: tuple[AtomMappingEntry, ...]

    def __post_init__(self) -> None:
        if not self.entries:
            raise ValueError("an AtomIdentityMapping requires at least one entry")
        source_indices = tuple(entry.source_index for entry in self.entries)
        target_indices = tuple(entry.target_index for entry in self.entries)
        atom_uids = tuple(entry.atom_uid for entry in self.entries)
        if len(source_indices) != len(set(source_indices)):
            raise ValueError("source indices must be unique")
        if len(target_indices) != len(set(target_indices)):
            raise ValueError("target indices must be unique")
        if len(atom_uids) != len(set(atom_uids)):
            raise ValueError("atom_uid values must be unique in a complete mapping")

    @property
    def is_reordered(self) -> bool:
        """Return whether at least one atom changed positional index."""

        return any(entry.source_index != entry.target_index for entry in self.entries)


@dataclass(frozen=True, slots=True)
class IdentityPropagationResult:
    """New immutable snapshot plus the atom mapping that created it."""

    snapshot: StructureSnapshot
    mapping: AtomIdentityMapping


def _periodic_fractional_delta(
    source: Vector3,
    target: Vector3,
    periodic: tuple[bool, bool, bool],
) -> Vector3:
    delta = [target[index] - source[index] for index in range(3)]
    for index, is_periodic in enumerate(periodic):
        if is_periodic:
            delta[index] -= round(delta[index])
    return (delta[0], delta[1], delta[2])


def _cartesian_displacement(
    source: Vector3,
    target: Vector3,
    lattice: Lattice,
    periodic: tuple[bool, bool, bool],
) -> float:
    delta = _periodic_fractional_delta(source, target, periodic)
    cartesian = tuple(
        sum(delta[basis] * lattice.vectors[basis][axis] for basis in range(3))
        for axis in range(3)
    )
    return sqrt(sum(component * component for component in cartesian))


def _build_result(
    *,
    source: StructureSnapshot,
    target_sites: Sequence[GeometrySite],
    source_indices_by_target: Sequence[int],
    method: AtomMappingMethod,
    label: str | None,
    origin: StructureOrigin,
) -> IdentityPropagationResult:
    sites: list[StructureSite] = []
    entries: list[AtomMappingEntry] = []
    for target_index, source_index in enumerate(source_indices_by_target):
        source_site = source.sites[source_index]
        target_site = target_sites[target_index]
        displacement = _cartesian_displacement(
            source_site.fractional_coords,
            target_site.fractional_coords,
            source.lattice,
            source.periodic,
        )
        sites.append(
            StructureSite(
                atom_uid=source_site.atom_uid,
                element=target_site.element,
                fractional_coords=target_site.fractional_coords,
            )
        )
        entries.append(
            AtomMappingEntry(
                source_index=source_index,
                target_index=target_index,
                atom_uid=source_site.atom_uid,
                element=source_site.element,
                displacement_angstrom=displacement,
            )
        )

    snapshot = StructureSnapshot(
        lattice=source.lattice,
        sites=tuple(sites),
        label=label,
        origin=origin,
        parent_snapshot_id=source.id,
        periodic=source.periodic,
    )
    mapping = AtomIdentityMapping(
        source_snapshot_id=source.id,
        target_snapshot_id=snapshot.id,
        method=method,
        entries=tuple(entries),
    )
    validate_identity_preserving_revision(source=source, target=snapshot)
    return IdentityPropagationResult(snapshot=snapshot, mapping=mapping)


def propagate_atom_uids_by_index(
    source: StructureSnapshot,
    target_sites: Sequence[GeometrySite],
    *,
    label: str | None = None,
    origin: StructureOrigin = StructureOrigin.RELAXED,
) -> IdentityPropagationResult:
    """Propagate identities when an engine guarantees atom-order preservation.

    This is the intended first-line mapping strategy for VASP POSCAR -> CONTCAR
    workflows. It intentionally rejects element changes at a preserved index.
    """

    if len(target_sites) != len(source.sites):
        raise AtomMappingError("index-preserving propagation requires the same atom count")
    pairs = zip(source.sites, target_sites, strict=True)
    for index, (source_site, target_site) in enumerate(pairs):
        if source_site.element != target_site.element:
            raise AtomMappingError(
                f"element mismatch at preserved index {index}: "
                f"{source_site.element} != {target_site.element}"
            )
    return _build_result(
        source=source,
        target_sites=target_sites,
        source_indices_by_target=tuple(range(len(source.sites))),
        method=AtomMappingMethod.INDEX_PRESERVING,
        label=label,
        origin=origin,
    )


def reconcile_reordered_sites(
    source: StructureSnapshot,
    target_sites: Sequence[GeometrySite],
    *,
    tolerance_angstrom: float = 1e-5,
    label: str | None = None,
    origin: StructureOrigin = StructureOrigin.EDITED,
) -> IdentityPropagationResult:
    """Recover stable identities after a pure atom reorder.

    Matching requires the same element and a unique periodic Cartesian position
    within ``tolerance_angstrom``. This function deliberately does not attempt
    relaxed-geometry nearest-neighbour matching; ambiguous mappings fail closed.
    """

    if tolerance_angstrom <= 0 or not isfinite(tolerance_angstrom):
        raise ValueError("tolerance_angstrom must be finite and positive")
    if len(target_sites) != len(source.sites):
        raise AtomMappingError("reorder reconciliation requires the same atom count")

    unmatched_source_indices = set(range(len(source.sites)))
    source_indices_by_target: list[int] = []
    for target_index, target_site in enumerate(target_sites):
        candidates: list[int] = []
        for source_index in sorted(unmatched_source_indices):
            source_site = source.sites[source_index]
            if source_site.element != target_site.element:
                continue
            displacement = _cartesian_displacement(
                source_site.fractional_coords,
                target_site.fractional_coords,
                source.lattice,
                source.periodic,
            )
            if displacement <= tolerance_angstrom:
                candidates.append(source_index)

        if not candidates:
            raise AtomMappingError(
                f"no identity match for target index {target_index} ({target_site.element})"
            )
        if len(candidates) > 1:
            raise AtomMappingError(
                f"ambiguous identity match for target index {target_index}: {candidates}"
            )
        matched_index = candidates[0]
        unmatched_source_indices.remove(matched_index)
        source_indices_by_target.append(matched_index)

    if unmatched_source_indices:
        raise AtomMappingError("reorder reconciliation left unmatched source atoms")

    return _build_result(
        source=source,
        target_sites=target_sites,
        source_indices_by_target=source_indices_by_target,
        method=AtomMappingMethod.EXACT_REORDER,
        label=label,
        origin=origin,
    )


def reorder_snapshot(
    source: StructureSnapshot,
    order: Sequence[int],
    *,
    label: str | None = None,
    origin: StructureOrigin = StructureOrigin.EDITED,
) -> IdentityPropagationResult:
    """Create an explicit reordered revision while preserving all atom identities."""

    expected = set(range(len(source.sites)))
    if len(order) != len(source.sites) or set(order) != expected:
        raise AtomMappingError("order must be a permutation of all source atom indices")
    target_sites = tuple(
        GeometrySite(
            element=source.sites[source_index].element,
            fractional_coords=source.sites[source_index].fractional_coords,
        )
        for source_index in order
    )
    return _build_result(
        source=source,
        target_sites=target_sites,
        source_indices_by_target=order,
        method=AtomMappingMethod.EXPLICIT_REORDER,
        label=label,
        origin=origin,
    )


def validate_identity_preserving_revision(
    *,
    source: StructureSnapshot,
    target: StructureSnapshot,
) -> None:
    """Validate direct lineage and stable atom identity across a structure revision."""

    if target.parent_snapshot_id != source.id:
        raise AtomMappingError("target snapshot must directly reference source as its parent")
    if len(source.sites) != len(target.sites):
        raise AtomMappingError("identity-preserving revisions require the same atom count")

    source_by_uid = {site.atom_uid: site for site in source.sites}
    target_by_uid = {site.atom_uid: site for site in target.sites}
    if source_by_uid.keys() != target_by_uid.keys():
        raise AtomMappingError("identity-preserving revisions require the same atom_uid set")
    for atom_uid, source_site in source_by_uid.items():
        if target_by_uid[atom_uid].element != source_site.element:
            raise AtomMappingError("an atom_uid cannot change chemical element across revisions")

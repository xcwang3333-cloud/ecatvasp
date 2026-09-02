"""Dual- and triple-metal site construction with explicit ensemble intent."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
from itertools import combinations
from math import isfinite

from ecatvasp.domain import AtomUid, SiteSide, StructureSite, StructureSnapshot, new_atom_uid
from ecatvasp.structures._placement import (
    COLLISION_TOLERANCE_ANGSTROM,
    minimum_image_distance,
    place_at_coordination_centroid,
)
from ecatvasp.structures.addition import StructureAdditionResult, append_structure_sites
from ecatvasp.structures.single_metal import SingleMetalSiteSpec, coordination_signature


class MultiMetalSiteError(ValueError):
    """Raised when a requested dual/triple-metal ensemble is invalid."""


class EnsembleSideTopology(StrEnum):
    """Side relationship derived from the explicitly placed metal centers."""

    SAME_SIDE = "same_side"
    OPPOSITE_SIDE = "opposite_side"
    IN_PLANE = "in_plane"
    MIXED = "mixed"


@dataclass(frozen=True, slots=True)
class MultiMetalCenterSpec:
    """Placement intent for one center within a dual/triple-metal ensemble."""

    metal_element: str
    coordination_atom_uids: tuple[AtomUid, ...]
    side: SiteSide
    height_angstrom: float

    def __post_init__(self) -> None:
        try:
            validated = SingleMetalSiteSpec(
                metal_element=self.metal_element,
                coordination_atom_uids=self.coordination_atom_uids,
                side=self.side,
                height_angstrom=self.height_angstrom,
            )
        except ValueError as exc:
            raise MultiMetalSiteError(str(exc)) from exc
        object.__setattr__(self, "metal_element", validated.metal_element)
        object.__setattr__(self, "coordination_atom_uids", validated.coordination_atom_uids)
        object.__setattr__(self, "side", validated.side)
        object.__setattr__(self, "height_angstrom", validated.height_angstrom)


@dataclass(frozen=True, slots=True)
class MultiMetalSiteSpec:
    """Scientific construction intent for exactly two or three fresh metal centers."""

    centers: tuple[MultiMetalCenterSpec, ...]
    metal_metal_topology_intent: str | None = None
    label: str | None = None

    def __post_init__(self) -> None:
        centers = tuple(self.centers)
        if len(centers) not in (2, 3):
            raise MultiMetalSiteError("multi-metal ensembles require exactly two or three centers")
        object.__setattr__(self, "centers", centers)
        if self.metal_metal_topology_intent is not None:
            intent = self.metal_metal_topology_intent.strip()
            if not intent:
                raise MultiMetalSiteError("metal_metal_topology_intent must not be blank")
            object.__setattr__(self, "metal_metal_topology_intent", intent)
        if self.label is not None and not self.label.strip():
            raise MultiMetalSiteError("label must not be blank when defined")


@dataclass(frozen=True, slots=True)
class MultiMetalCenterResult:
    """Resolved identity, geometry intent, and coordination metadata for one metal center."""

    metal_atom_uid: AtomUid
    metal_element: str
    coordination_atom_uids: tuple[AtomUid, ...]
    coordination_elements: tuple[str, ...]
    side: SiteSide
    height_angstrom: float

    def __post_init__(self) -> None:
        if not self.metal_element.strip():
            raise ValueError("metal_element must not be blank")
        if not self.coordination_atom_uids:
            raise ValueError("multi-metal center requires coordination atoms")
        if len(self.coordination_atom_uids) != len(set(self.coordination_atom_uids)):
            raise ValueError("multi-metal center coordination atom_uids must be unique")
        if len(self.coordination_atom_uids) != len(self.coordination_elements):
            raise ValueError("coordination atom and element metadata must align")
        if self.side is SiteSide.UNSPECIFIED:
            raise ValueError("multi-metal center requires an explicit side")
        if not isfinite(self.height_angstrom):
            raise ValueError("height_angstrom must be finite")
        if self.side is SiteSide.IN_PLANE and self.height_angstrom != 0.0:
            raise ValueError("IN_PLANE center requires zero height")
        if self.side is not SiteSide.IN_PLANE and self.height_angstrom <= 0.0:
            raise ValueError("TOP/BOTTOM center requires positive height")

    @property
    def coordination_signature(self) -> str:
        """Return the explicit coordination-composition intent for this center."""

        return coordination_signature(self.coordination_elements)


@dataclass(frozen=True, slots=True)
class MetalPairDistance:
    """Minimum-image geometric distance between two added metal centers."""

    left_atom_uid: AtomUid
    right_atom_uid: AtomUid
    distance_angstrom: float

    def __post_init__(self) -> None:
        if self.left_atom_uid == self.right_atom_uid:
            raise ValueError("metal pair must reference two distinct atom_uids")
        if not isfinite(self.distance_angstrom) or self.distance_angstrom <= 0.0:
            raise ValueError("metal pair distance must be finite and positive")


@dataclass(frozen=True, slots=True)
class MultiMetalSiteResult:
    """One immutable multi-metal child revision plus explicit ensemble intent."""

    addition: StructureAdditionResult
    centers: tuple[MultiMetalCenterResult, ...]
    side_topology: EnsembleSideTopology
    shared_coordination_atom_uids: tuple[AtomUid, ...]
    pair_distances: tuple[MetalPairDistance, ...]
    metal_metal_topology_intent: str | None = None

    def __post_init__(self) -> None:
        if len(self.centers) not in (2, 3):
            raise ValueError("multi-metal result requires exactly two or three centers")
        center_uids = tuple(center.metal_atom_uid for center in self.centers)
        if len(center_uids) != len(set(center_uids)):
            raise ValueError("multi-metal center atom_uids must be unique")
        if self.addition.added_atom_uids != center_uids:
            raise ValueError("added atom order must match multi-metal center order")
        if self.side_topology is not derive_side_topology(
            tuple(center.side for center in self.centers)
        ):
            raise ValueError("side_topology must match the center side assignments")

        preserved_uids = self.addition.preserved_atom_uids
        preserved_set = set(preserved_uids)
        coordination_counts: Counter[AtomUid] = Counter()
        for center in self.centers:
            if any(atom_uid not in preserved_set for atom_uid in center.coordination_atom_uids):
                raise ValueError(
                    "center coordination atom_uids must reference preserved source atoms"
                )
            coordination_counts.update(center.coordination_atom_uids)
        expected_shared = tuple(
            atom_uid for atom_uid in preserved_uids if coordination_counts[atom_uid] > 1
        )
        if self.shared_coordination_atom_uids != expected_shared:
            raise ValueError("shared coordination atom_uids must match center coordination intent")

        expected_pair_keys = {
            frozenset((left_uid, right_uid)) for left_uid, right_uid in combinations(center_uids, 2)
        }
        actual_pair_keys = {
            frozenset((pair.left_atom_uid, pair.right_atom_uid)) for pair in self.pair_distances
        }
        if len(self.pair_distances) != len(actual_pair_keys):
            raise ValueError("pair distances must not contain duplicate metal pairs")
        if actual_pair_keys != expected_pair_keys:
            raise ValueError("pair distances must cover every unique multi-metal center pair")

        if (
            self.metal_metal_topology_intent is not None
            and not self.metal_metal_topology_intent.strip()
        ):
            raise ValueError("metal_metal_topology_intent must not be blank")

    @property
    def snapshot(self) -> StructureSnapshot:
        """Return the immutable child snapshot containing all metal centers."""

        return self.addition.snapshot

    @property
    def metal_atom_uids(self) -> tuple[AtomUid, ...]:
        """Return ordered fresh identities of all added metal centers."""

        return tuple(center.metal_atom_uid for center in self.centers)


def derive_side_topology(sides: tuple[SiteSide, ...]) -> EnsembleSideTopology:
    """Derive ensemble side topology from center-local side assignments."""

    side_set = set(sides)
    if side_set == {SiteSide.IN_PLANE}:
        return EnsembleSideTopology.IN_PLANE
    if side_set <= {SiteSide.TOP} or side_set <= {SiteSide.BOTTOM}:
        return EnsembleSideTopology.SAME_SIDE
    if side_set <= {SiteSide.TOP, SiteSide.BOTTOM} and side_set == {
        SiteSide.TOP,
        SiteSide.BOTTOM,
    }:
        return EnsembleSideTopology.OPPOSITE_SIDE
    return EnsembleSideTopology.MIXED


def build_multi_metal_site(
    source: StructureSnapshot,
    spec: MultiMetalSiteSpec,
) -> MultiMetalSiteResult:
    """Append two or three independently specified metal centers in one child revision."""

    source_by_uid = {site.atom_uid: site for site in source.sites}
    planned: list[
        tuple[
            MultiMetalCenterSpec,
            tuple[StructureSite, ...],
            tuple[float, float, float],
        ]
    ] = []

    for center in spec.centers:
        missing = set(center.coordination_atom_uids) - set(source_by_uid)
        if missing:
            raise MultiMetalSiteError(
                "all coordination atom_uids for every center must exist in the source snapshot"
            )
        coordination_sites = tuple(
            source_by_uid[atom_uid] for atom_uid in center.coordination_atom_uids
        )
        try:
            fractional_coords = place_at_coordination_centroid(
                source,
                coordination_sites,
                center.side,
                center.height_angstrom,
            )
        except ValueError as exc:
            raise MultiMetalSiteError(str(exc)) from exc

        for existing_site in source.sites:
            distance = minimum_image_distance(
                fractional_coords,
                existing_site.fractional_coords,
                source.lattice,
                source.periodic,
            )
            if distance < COLLISION_TOLERANCE_ANGSTROM:
                raise MultiMetalSiteError("metal placement overlaps an existing source atom")

        for _, _, prior_fractional in planned:
            distance = minimum_image_distance(
                fractional_coords,
                prior_fractional,
                source.lattice,
                source.periodic,
            )
            if distance < COLLISION_TOLERANCE_ANGSTROM:
                raise MultiMetalSiteError("two requested metal centers occupy the same position")

        planned.append((center, coordination_sites, fractional_coords))

    added_sites: list[StructureSite] = []
    center_results: list[MultiMetalCenterResult] = []
    for center, coordination_sites, fractional_coords in planned:
        atom_uid = new_atom_uid()
        added_sites.append(
            StructureSite(
                atom_uid=atom_uid,
                element=center.metal_element,
                fractional_coords=fractional_coords,
            )
        )
        center_results.append(
            MultiMetalCenterResult(
                metal_atom_uid=atom_uid,
                metal_element=center.metal_element,
                coordination_atom_uids=center.coordination_atom_uids,
                coordination_elements=tuple(site.element for site in coordination_sites),
                side=center.side,
                height_angstrom=center.height_angstrom,
            )
        )

    addition = append_structure_sites(source, added_sites, label=spec.label)
    shared = _shared_coordination_atom_uids(source, spec.centers)
    pairs = _pair_distances(source, tuple(added_sites))
    return MultiMetalSiteResult(
        addition=addition,
        centers=tuple(center_results),
        side_topology=derive_side_topology(tuple(center.side for center in spec.centers)),
        shared_coordination_atom_uids=shared,
        pair_distances=pairs,
        metal_metal_topology_intent=spec.metal_metal_topology_intent,
    )


def _shared_coordination_atom_uids(
    source: StructureSnapshot,
    centers: tuple[MultiMetalCenterSpec, ...],
) -> tuple[AtomUid, ...]:
    counts = Counter(atom_uid for center in centers for atom_uid in center.coordination_atom_uids)
    return tuple(site.atom_uid for site in source.sites if counts[site.atom_uid] > 1)


def _pair_distances(
    source: StructureSnapshot,
    added_sites: tuple[StructureSite, ...],
) -> tuple[MetalPairDistance, ...]:
    pairs: list[MetalPairDistance] = []
    for left_index, left in enumerate(added_sites):
        for right in added_sites[left_index + 1 :]:
            distance = minimum_image_distance(
                left.fractional_coords,
                right.fractional_coords,
                source.lattice,
                source.periodic,
            )
            pairs.append(
                MetalPairDistance(
                    left_atom_uid=left.atom_uid,
                    right_atom_uid=right.atom_uid,
                    distance_angstrom=distance,
                )
            )
    return tuple(pairs)

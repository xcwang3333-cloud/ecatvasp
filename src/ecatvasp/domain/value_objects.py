"""Immutable value objects shared by ECatVASP domain entities."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite

from ecatvasp.domain.ids import AtomUid

Vector3 = tuple[float, float, float]


class StructureOrigin(StrEnum):
    """How a structure snapshot entered the scientific lineage."""

    IMPORTED = "imported"
    BUILT = "built"
    EDITED = "edited"
    RELAXED = "relaxed"


class VariantType(StrEnum):
    """Scientific reason a catalyst structure variant differs from its siblings."""

    GEOMETRY = "geometry"
    SPIN = "spin"
    SITE_TOPOLOGY = "site_topology"
    RECONSTRUCTION = "reconstruction"
    SOLVATION = "solvation"
    COVERAGE = "coverage"
    CUSTOM = "custom"


class SiteSide(StrEnum):
    """Relative side of a two-dimensional support occupied by an active center."""

    TOP = "top"
    BOTTOM = "bottom"
    IN_PLANE = "in_plane"
    UNSPECIFIED = "unspecified"


class BindingMode(StrEnum):
    """High-level adsorption geometry classification."""

    NONE = "none"
    SINGLE_CENTER = "single_center"
    BRIDGE = "bridge"
    MULTICENTER = "multicenter"
    CUSTOM = "custom"


@dataclass(frozen=True, slots=True)
class Lattice:
    """Immutable 3x3 lattice vectors in angstrom."""

    vectors: tuple[Vector3, Vector3, Vector3]

    def __post_init__(self) -> None:
        for vector in self.vectors:
            if not all(isfinite(component) for component in vector):
                raise ValueError("lattice vectors must contain finite components")


@dataclass(frozen=True, slots=True)
class StructureSite:
    """One atom in an immutable structure snapshot."""

    atom_uid: AtomUid
    element: str
    fractional_coords: Vector3

    def __post_init__(self) -> None:
        if not self.element.strip():
            raise ValueError("element must not be blank")
        if not all(isfinite(component) for component in self.fractional_coords):
            raise ValueError("fractional_coords must contain finite components")


@dataclass(frozen=True, slots=True)
class BindingEdge:
    """Explicit bond intent between an adsorbate atom and an active-site atom."""

    adsorbate_atom_uid: AtomUid
    site_atom_uid: AtomUid
    label: str | None = None


@dataclass(frozen=True, slots=True)
class SideLabel:
    """Side assignment for one active-center atom."""

    atom_uid: AtomUid
    side: SiteSide

"""Single-metal site construction on immutable electrocatalyst snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from math import isfinite, sqrt

from ecatvasp.domain import (
    AtomUid,
    Lattice,
    SiteSide,
    StructureSite,
    StructureSnapshot,
    new_atom_uid,
)
from ecatvasp.structures.addition import StructureAdditionResult, append_structure_sites

Vector3 = tuple[float, float, float]

_METAL_ELEMENTS = frozenset(
    {
        "Li",
        "Be",
        "Na",
        "Mg",
        "Al",
        "K",
        "Ca",
        "Sc",
        "Ti",
        "V",
        "Cr",
        "Mn",
        "Fe",
        "Co",
        "Ni",
        "Cu",
        "Zn",
        "Ga",
        "Rb",
        "Sr",
        "Y",
        "Zr",
        "Nb",
        "Mo",
        "Tc",
        "Ru",
        "Rh",
        "Pd",
        "Ag",
        "Cd",
        "In",
        "Sn",
        "Cs",
        "Ba",
        "La",
        "Ce",
        "Pr",
        "Nd",
        "Pm",
        "Sm",
        "Eu",
        "Gd",
        "Tb",
        "Dy",
        "Ho",
        "Er",
        "Tm",
        "Yb",
        "Lu",
        "Hf",
        "Ta",
        "W",
        "Re",
        "Os",
        "Ir",
        "Pt",
        "Au",
        "Hg",
        "Tl",
        "Pb",
        "Bi",
        "Fr",
        "Ra",
        "Ac",
        "Th",
        "Pa",
        "U",
        "Np",
        "Pu",
        "Am",
        "Cm",
        "Bk",
        "Cf",
        "Es",
        "Fm",
        "Md",
        "No",
        "Lr",
        "Rf",
        "Db",
        "Sg",
        "Bh",
        "Hs",
        "Mt",
        "Ds",
        "Rg",
        "Cn",
        "Nh",
        "Fl",
        "Mc",
        "Lv",
    }
)
_COLLISION_TOLERANCE_ANGSTROM = 1.0e-6
_GEOMETRY_TOLERANCE = 1.0e-12


class SingleMetalSiteError(ValueError):
    """Raised when a requested single-metal site cannot be constructed safely."""


@dataclass(frozen=True, slots=True)
class SingleMetalSiteSpec:
    """Scientific placement intent for one fresh metal atom."""

    metal_element: str
    coordination_atom_uids: tuple[AtomUid, ...]
    side: SiteSide
    height_angstrom: float
    label: str | None = None

    def __post_init__(self) -> None:
        element = self.metal_element.strip().capitalize()
        if element not in _METAL_ELEMENTS:
            raise SingleMetalSiteError("metal_element must be a recognized metallic element")
        object.__setattr__(self, "metal_element", element)

        coordination_atom_uids = tuple(self.coordination_atom_uids)
        if not coordination_atom_uids:
            raise SingleMetalSiteError("at least one coordination atom_uid is required")
        if len(coordination_atom_uids) != len(set(coordination_atom_uids)):
            raise SingleMetalSiteError("coordination atom_uids must be unique")
        object.__setattr__(self, "coordination_atom_uids", coordination_atom_uids)

        if not isinstance(self.side, SiteSide):
            raise SingleMetalSiteError("side must be a SiteSide value")
        if self.side is SiteSide.UNSPECIFIED:
            raise SingleMetalSiteError("single-metal placement requires an explicit side")

        if isinstance(self.height_angstrom, bool) or not isinstance(
            self.height_angstrom, (int, float)
        ):
            raise SingleMetalSiteError("height_angstrom must be a finite number")
        height = float(self.height_angstrom)
        if not isfinite(height):
            raise SingleMetalSiteError("height_angstrom must be a finite number")
        if self.side is SiteSide.IN_PLANE:
            if height != 0.0:
                raise SingleMetalSiteError("IN_PLANE placement requires zero height")
        elif height <= 0.0:
            raise SingleMetalSiteError("TOP/BOTTOM placement requires positive height")
        object.__setattr__(self, "height_angstrom", height)

        if self.label is not None and not self.label.strip():
            raise SingleMetalSiteError("label must not be blank when defined")


@dataclass(frozen=True, slots=True)
class SingleMetalSiteResult:
    """Single-metal child snapshot plus placement intent and append-only lineage."""

    addition: StructureAdditionResult
    metal_atom_uid: AtomUid
    coordination_atom_uids: tuple[AtomUid, ...]
    coordination_elements: tuple[str, ...]
    side: SiteSide
    height_angstrom: float

    def __post_init__(self) -> None:
        if self.addition.added_atom_uids != (self.metal_atom_uid,):
            raise ValueError("single-metal result must contain exactly one added metal atom")
        if not self.coordination_atom_uids:
            raise ValueError("single-metal result requires coordination atoms")
        if len(self.coordination_atom_uids) != len(self.coordination_elements):
            raise ValueError("coordination atom and element metadata must align")

    @property
    def snapshot(self) -> StructureSnapshot:
        """Return the immutable child snapshot containing the metal atom."""

        return self.addition.snapshot

    @property
    def coordination_signature(self) -> str:
        """Return a stable composition label following the explicit anchor order."""

        order: list[str] = []
        counts: dict[str, int] = {}
        for element in self.coordination_elements:
            if element not in counts:
                order.append(element)
                counts[element] = 0
            counts[element] += 1
        parts: list[str] = []
        for element in order:
            count = counts[element]
            parts.append(element if count == 1 else f"{element}{count}")
        return "".join(parts)


def build_single_metal_site(
    source: StructureSnapshot,
    spec: SingleMetalSiteSpec,
) -> SingleMetalSiteResult:
    """Append one metal at a PBC-aware coordination centroid plus slab-normal offset."""

    source_by_uid = {site.atom_uid: site for site in source.sites}
    missing = set(spec.coordination_atom_uids) - set(source_by_uid)
    if missing:
        raise SingleMetalSiteError("all coordination atom_uids must exist in the source snapshot")

    coordination_sites = tuple(source_by_uid[atom_uid] for atom_uid in spec.coordination_atom_uids)
    centroid_fractional = _pbc_centroid_fractional(source, coordination_sites)
    centroid_cartesian = _fractional_to_cartesian(centroid_fractional, source.lattice)
    normal = _slab_normal(source.lattice)

    if spec.side is SiteSide.TOP:
        displacement = _scale(normal, spec.height_angstrom)
    elif spec.side is SiteSide.BOTTOM:
        displacement = _scale(normal, -spec.height_angstrom)
    else:
        displacement = (0.0, 0.0, 0.0)

    metal_cartesian = _add(centroid_cartesian, displacement)
    metal_fractional = _cartesian_to_fractional(metal_cartesian, source.lattice)
    metal_fractional = _wrap_fractional(metal_fractional, source.periodic)

    for existing_site in source.sites:
        delta = _subtract(metal_fractional, existing_site.fractional_coords)
        minimum_delta = _minimum_image_delta(delta, source.lattice, source.periodic)
        distance = _norm(_fractional_to_cartesian(minimum_delta, source.lattice))
        if distance < _COLLISION_TOLERANCE_ANGSTROM:
            raise SingleMetalSiteError("metal placement overlaps an existing atom")

    metal_atom_uid = new_atom_uid()
    metal_site = StructureSite(
        atom_uid=metal_atom_uid,
        element=spec.metal_element,
        fractional_coords=metal_fractional,
    )
    addition = append_structure_sites(source, (metal_site,), label=spec.label)
    return SingleMetalSiteResult(
        addition=addition,
        metal_atom_uid=metal_atom_uid,
        coordination_atom_uids=spec.coordination_atom_uids,
        coordination_elements=tuple(site.element for site in coordination_sites),
        side=spec.side,
        height_angstrom=spec.height_angstrom,
    )


def _pbc_centroid_fractional(
    source: StructureSnapshot,
    sites: tuple[StructureSite, ...],
) -> Vector3:
    reference = sites[0].fractional_coords
    unwrapped: list[Vector3] = [reference]
    for site in sites[1:]:
        delta = _subtract(site.fractional_coords, reference)
        minimum_delta = _minimum_image_delta(delta, source.lattice, source.periodic)
        unwrapped.append(_add(reference, minimum_delta))

    count = float(len(unwrapped))
    centroid = (
        sum(coords[0] for coords in unwrapped) / count,
        sum(coords[1] for coords in unwrapped) / count,
        sum(coords[2] for coords in unwrapped) / count,
    )
    return _wrap_fractional(centroid, source.periodic)


def _minimum_image_delta(
    delta: Vector3,
    lattice: Lattice,
    periodic: tuple[bool, bool, bool],
) -> Vector3:
    shift_options: list[tuple[int, ...]] = []
    for component, is_periodic in zip(delta, periodic, strict=True):
        if is_periodic:
            center = round(component)
            shift_options.append((center - 1, center, center + 1))
        else:
            shift_options.append((0,))

    best_delta: Vector3 | None = None
    best_norm_squared = float("inf")
    for shift in product(*shift_options):
        candidate = (
            delta[0] - shift[0],
            delta[1] - shift[1],
            delta[2] - shift[2],
        )
        cartesian = _fractional_to_cartesian(candidate, lattice)
        norm_squared = _dot(cartesian, cartesian)
        if norm_squared < best_norm_squared:
            best_norm_squared = norm_squared
            best_delta = candidate

    assert best_delta is not None
    return best_delta


def _slab_normal(lattice: Lattice) -> Vector3:
    normal = _cross(lattice.vectors[0], lattice.vectors[1])
    magnitude = _norm(normal)
    if magnitude <= _GEOMETRY_TOLERANCE:
        raise SingleMetalSiteError("slab lattice vectors a1 and a2 must define a plane")
    return _scale(normal, 1.0 / magnitude)


def _fractional_to_cartesian(coords: Vector3, lattice: Lattice) -> Vector3:
    a1, a2, a3 = lattice.vectors
    return (
        coords[0] * a1[0] + coords[1] * a2[0] + coords[2] * a3[0],
        coords[0] * a1[1] + coords[1] * a2[1] + coords[2] * a3[1],
        coords[0] * a1[2] + coords[1] * a2[2] + coords[2] * a3[2],
    )


def _cartesian_to_fractional(coords: Vector3, lattice: Lattice) -> Vector3:
    a1, a2, a3 = lattice.vectors
    volume = _dot(a1, _cross(a2, a3))
    if abs(volume) <= _GEOMETRY_TOLERANCE:
        raise SingleMetalSiteError("lattice vectors must define a nonzero cell volume")
    return (
        _dot(coords, _cross(a2, a3)) / volume,
        _dot(coords, _cross(a3, a1)) / volume,
        _dot(coords, _cross(a1, a2)) / volume,
    )


def _wrap_fractional(
    coords: Vector3,
    periodic: tuple[bool, bool, bool],
) -> Vector3:
    return tuple(
        component % 1.0 if is_periodic else component
        for component, is_periodic in zip(coords, periodic, strict=True)
    )  # type: ignore[return-value]


def _dot(left: Vector3, right: Vector3) -> float:
    return left[0] * right[0] + left[1] * right[1] + left[2] * right[2]


def _cross(left: Vector3, right: Vector3) -> Vector3:
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def _norm(vector: Vector3) -> float:
    return sqrt(_dot(vector, vector))


def _add(left: Vector3, right: Vector3) -> Vector3:
    return (left[0] + right[0], left[1] + right[1], left[2] + right[2])


def _subtract(left: Vector3, right: Vector3) -> Vector3:
    return (left[0] - right[0], left[1] - right[1], left[2] - right[2])


def _scale(vector: Vector3, factor: float) -> Vector3:
    return (vector[0] * factor, vector[1] * factor, vector[2] * factor)

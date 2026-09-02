"""Shared periodic geometry helpers for metal-site placement."""

from __future__ import annotations

from itertools import product
from math import sqrt

from ecatvasp.domain import Lattice, SiteSide, StructureSite, StructureSnapshot

Vector3 = tuple[float, float, float]

COLLISION_TOLERANCE_ANGSTROM = 1.0e-6
GEOMETRY_TOLERANCE = 1.0e-12


def place_at_coordination_centroid(
    source: StructureSnapshot,
    coordination_sites: tuple[StructureSite, ...],
    side: SiteSide,
    height_angstrom: float,
) -> Vector3:
    """Return wrapped fractional coordinates at a PBC-aware centroid plus side offset."""

    centroid_fractional = pbc_centroid_fractional(source, coordination_sites)
    centroid_cartesian = fractional_to_cartesian(centroid_fractional, source.lattice)
    normal = slab_normal(source.lattice)

    if side is SiteSide.TOP:
        displacement = scale(normal, height_angstrom)
    elif side is SiteSide.BOTTOM:
        displacement = scale(normal, -height_angstrom)
    else:
        displacement = (0.0, 0.0, 0.0)

    placed_cartesian = add(centroid_cartesian, displacement)
    placed_fractional = cartesian_to_fractional(placed_cartesian, source.lattice)
    return wrap_fractional(placed_fractional, source.periodic)


def pbc_centroid_fractional(
    source: StructureSnapshot,
    sites: tuple[StructureSite, ...],
) -> Vector3:
    """Return a centroid after minimum-image unwrapping around the first site."""

    if not sites:
        raise ValueError("at least one site is required to compute a centroid")
    reference = sites[0].fractional_coords
    unwrapped: list[Vector3] = [reference]
    for site in sites[1:]:
        delta = subtract(site.fractional_coords, reference)
        minimum_delta = minimum_image_delta(delta, source.lattice, source.periodic)
        unwrapped.append(add(reference, minimum_delta))

    count = float(len(unwrapped))
    centroid = (
        sum(coords[0] for coords in unwrapped) / count,
        sum(coords[1] for coords in unwrapped) / count,
        sum(coords[2] for coords in unwrapped) / count,
    )
    return wrap_fractional(centroid, source.periodic)


def minimum_image_delta(
    delta: Vector3,
    lattice: Lattice,
    periodic: tuple[bool, bool, bool],
) -> Vector3:
    """Return the shortest Cartesian-image displacement expressed fractionally."""

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
        cartesian = fractional_to_cartesian(candidate, lattice)
        norm_squared = dot(cartesian, cartesian)
        if norm_squared < best_norm_squared:
            best_norm_squared = norm_squared
            best_delta = candidate

    if best_delta is None:
        raise RuntimeError("minimum-image search produced no candidate")
    return best_delta


def minimum_image_distance(
    left_fractional: Vector3,
    right_fractional: Vector3,
    lattice: Lattice,
    periodic: tuple[bool, bool, bool],
) -> float:
    """Return minimum-image Cartesian distance in angstrom."""

    delta = subtract(left_fractional, right_fractional)
    minimum_delta = minimum_image_delta(delta, lattice, periodic)
    return norm(fractional_to_cartesian(minimum_delta, lattice))


def slab_normal(lattice: Lattice) -> Vector3:
    """Return normalized cross product of slab vectors a1 and a2."""

    normal = cross(lattice.vectors[0], lattice.vectors[1])
    magnitude = norm(normal)
    if magnitude <= GEOMETRY_TOLERANCE:
        raise ValueError("slab lattice vectors a1 and a2 must define a plane")
    return scale(normal, 1.0 / magnitude)


def fractional_to_cartesian(coords: Vector3, lattice: Lattice) -> Vector3:
    """Transform fractional coordinates into Cartesian coordinates."""

    a1, a2, a3 = lattice.vectors
    return (
        coords[0] * a1[0] + coords[1] * a2[0] + coords[2] * a3[0],
        coords[0] * a1[1] + coords[1] * a2[1] + coords[2] * a3[1],
        coords[0] * a1[2] + coords[1] * a2[2] + coords[2] * a3[2],
    )


def cartesian_to_fractional(coords: Vector3, lattice: Lattice) -> Vector3:
    """Transform Cartesian coordinates into fractional coordinates."""

    a1, a2, a3 = lattice.vectors
    volume = dot(a1, cross(a2, a3))
    if abs(volume) <= GEOMETRY_TOLERANCE:
        raise ValueError("lattice vectors must define a nonzero cell volume")
    return (
        dot(coords, cross(a2, a3)) / volume,
        dot(coords, cross(a3, a1)) / volume,
        dot(coords, cross(a1, a2)) / volume,
    )


def wrap_fractional(
    coords: Vector3,
    periodic: tuple[bool, bool, bool],
) -> Vector3:
    """Wrap periodic fractional components into [0, 1)."""

    wrapped = tuple(
        component % 1.0 if is_periodic else component
        for component, is_periodic in zip(coords, periodic, strict=True)
    )
    return (wrapped[0], wrapped[1], wrapped[2])


def dot(left: Vector3, right: Vector3) -> float:
    return left[0] * right[0] + left[1] * right[1] + left[2] * right[2]


def cross(left: Vector3, right: Vector3) -> Vector3:
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def norm(vector: Vector3) -> float:
    return sqrt(dot(vector, vector))


def add(left: Vector3, right: Vector3) -> Vector3:
    return (left[0] + right[0], left[1] + right[1], left[2] + right[2])


def subtract(left: Vector3, right: Vector3) -> Vector3:
    return (left[0] - right[0], left[1] - right[1], left[2] - right[2])


def scale(vector: Vector3, factor: float) -> Vector3:
    return (vector[0] * factor, vector[1] * factor, vector[2] * factor)

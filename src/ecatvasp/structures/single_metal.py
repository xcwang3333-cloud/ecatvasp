"""Single-metal site construction on immutable electrocatalyst snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from ecatvasp.domain import AtomUid, SiteSide, StructureSite, StructureSnapshot, new_atom_uid
from ecatvasp.structures._placement import (
    COLLISION_TOLERANCE_ANGSTROM,
    minimum_image_distance,
    place_at_coordination_centroid,
)
from ecatvasp.structures.addition import StructureAdditionResult, append_structure_sites

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

        return coordination_signature(self.coordination_elements)


def coordination_signature(elements: tuple[str, ...]) -> str:
    """Return a compact composition signature preserving first-element order."""

    order: list[str] = []
    counts: dict[str, int] = {}
    for element in elements:
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
    try:
        metal_fractional = place_at_coordination_centroid(
            source,
            coordination_sites,
            spec.side,
            spec.height_angstrom,
        )
    except ValueError as exc:
        raise SingleMetalSiteError(str(exc)) from exc

    for existing_site in source.sites:
        distance = minimum_image_distance(
            metal_fractional,
            existing_site.fractional_coords,
            source.lattice,
            source.periodic,
        )
        if distance < COLLISION_TOLERANCE_ANGSTROM:
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

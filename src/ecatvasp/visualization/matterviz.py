"""Viewer-agnostic MatterViz adapter for Model Studio Block 9."""

from __future__ import annotations

from dataclasses import dataclass
from math import acos, degrees, isfinite, sqrt
from pathlib import Path

from ecatvasp.domain import AtomUid, StructureSnapshot
from ecatvasp.structures.io import (
    StructureDocument,
    StructureFormat,
    import_structure,
    parse_structure,
    serialize_structure,
)
from ecatvasp.structures.state_conformer import ConformerVisualizationContext

Vector3 = tuple[float, float, float]
Matrix3 = tuple[Vector3, Vector3, Vector3]

MATTERVIZ_TARGET_VERSION = "0.6.0"
MATTERVIZ_CONTRACT_VERSION = "ecatvasp-matterviz-v1"


class MatterVizAdapterError(ValueError):
    """Raised when stable scientific identity cannot be mapped to a viewer payload."""


@dataclass(frozen=True, slots=True)
class MatterVizSpecies:
    """Ordered-site species entry matching MatterViz's public ``Species`` contract."""

    element: str
    occu: float = 1.0
    oxidation_state: float = 0.0

    def __post_init__(self) -> None:
        if not self.element.strip():
            raise MatterVizAdapterError("MatterViz species element must not be blank")
        if not isfinite(self.occu) or self.occu <= 0.0:
            raise MatterVizAdapterError("MatterViz species occupancy must be finite and positive")
        if not isfinite(self.oxidation_state):
            raise MatterVizAdapterError("MatterViz oxidation state must be finite")

    def to_dict(self) -> dict[str, object]:
        return {
            "element": self.element,
            "occu": self.occu,
            "oxidation_state": self.oxidation_state,
        }


@dataclass(frozen=True, slots=True)
class MatterVizSite:
    """One site in the exact ECatVASP snapshot order used by the viewer."""

    species: tuple[MatterVizSpecies, ...]
    abc: Vector3
    xyz: Vector3
    label: str
    properties: dict[str, object]

    def __post_init__(self) -> None:
        if not self.species:
            raise MatterVizAdapterError("MatterViz site must contain at least one species")
        if not self.label.strip():
            raise MatterVizAdapterError("MatterViz site label must not be blank")
        if not _finite_vector(self.abc) or not _finite_vector(self.xyz):
            raise MatterVizAdapterError("MatterViz site coordinates must be finite")
        object.__setattr__(self, "properties", dict(self.properties))

    def to_dict(self) -> dict[str, object]:
        return {
            "species": [item.to_dict() for item in self.species],
            "abc": list(self.abc),
            "xyz": list(self.xyz),
            "label": self.label,
            "properties": dict(self.properties),
        }


@dataclass(frozen=True, slots=True)
class MatterVizLattice:
    """Lattice fields required by MatterViz's public ``Crystal`` type."""

    matrix: Matrix3
    pbc: tuple[bool, bool, bool]
    volume: float
    a: float
    b: float
    c: float
    alpha: float
    beta: float
    gamma: float

    def __post_init__(self) -> None:
        if not all(_finite_vector(vector) for vector in self.matrix):
            raise MatterVizAdapterError("MatterViz lattice vectors must be finite")
        numeric = (self.volume, self.a, self.b, self.c, self.alpha, self.beta, self.gamma)
        if any(not isfinite(value) for value in numeric):
            raise MatterVizAdapterError("MatterViz lattice metadata must be finite")
        if self.volume <= 0.0 or min(self.a, self.b, self.c) <= 0.0:
            raise MatterVizAdapterError("MatterViz lattice must be non-singular")

    def to_dict(self) -> dict[str, object]:
        return {
            "matrix": [list(vector) for vector in self.matrix],
            "pbc": list(self.pbc),
            "volume": self.volume,
            "a": self.a,
            "b": self.b,
            "c": self.c,
            "alpha": self.alpha,
            "beta": self.beta,
            "gamma": self.gamma,
        }


@dataclass(frozen=True, slots=True)
class MatterVizStructure:
    """Typed MatterViz ``Crystal`` payload independent of the JavaScript runtime."""

    sites: tuple[MatterVizSite, ...]
    lattice: MatterVizLattice
    properties: dict[str, object]

    def __post_init__(self) -> None:
        if not self.sites:
            raise MatterVizAdapterError("MatterViz structure must contain at least one site")
        object.__setattr__(self, "properties", dict(self.properties))

    def to_dict(self) -> dict[str, object]:
        return {
            "sites": [site.to_dict() for site in self.sites],
            "lattice": self.lattice.to_dict(),
            "properties": dict(self.properties),
        }


@dataclass(frozen=True, slots=True)
class MatterVizAtomIndexMap:
    """Exact bijection between stable ``atom_uid`` and viewer-local site indices."""

    atom_uid_by_viewer_index: tuple[AtomUid, ...]
    viewer_index_by_atom_uid: dict[AtomUid, int]

    def __post_init__(self) -> None:
        ordered = tuple(self.atom_uid_by_viewer_index)
        if not ordered:
            raise MatterVizAdapterError("MatterViz atom-index map must not be empty")
        if len(ordered) != len(set(ordered)):
            raise MatterVizAdapterError("MatterViz atom-index map requires unique atom_uids")
        expected = {atom_uid: index for index, atom_uid in enumerate(ordered)}
        if self.viewer_index_by_atom_uid != expected:
            raise MatterVizAdapterError("MatterViz atom-index map must be an exact bijection")
        object.__setattr__(self, "atom_uid_by_viewer_index", ordered)
        object.__setattr__(self, "viewer_index_by_atom_uid", dict(expected))

    @classmethod
    def from_snapshot(cls, snapshot: StructureSnapshot) -> MatterVizAtomIndexMap:
        ordered = tuple(site.atom_uid for site in snapshot.sites)
        return cls(
            atom_uid_by_viewer_index=ordered,
            viewer_index_by_atom_uid={atom_uid: index for index, atom_uid in enumerate(ordered)},
        )

    def viewer_index(self, atom_uid: AtomUid) -> int:
        try:
            return self.viewer_index_by_atom_uid[atom_uid]
        except KeyError as error:
            raise MatterVizAdapterError("atom_uid is absent from the MatterViz payload") from error

    def atom_uid(self, viewer_index: int) -> AtomUid:
        if viewer_index < 0 or viewer_index >= len(self.atom_uid_by_viewer_index):
            raise MatterVizAdapterError("MatterViz viewer index is out of range")
        return self.atom_uid_by_viewer_index[viewer_index]

    def to_dict(self) -> dict[str, object]:
        return {
            "atom_uid_by_viewer_index": [str(value) for value in self.atom_uid_by_viewer_index],
            "viewer_index_by_atom_uid": {
                str(atom_uid): index for atom_uid, index in self.viewer_index_by_atom_uid.items()
            },
        }


@dataclass(frozen=True, slots=True)
class MatterVizBindingSegment:
    """Transient viewer connection representing scientific binding intent, not topology."""

    adsorbate_index: int
    site_index: int

    def __post_init__(self) -> None:
        if self.adsorbate_index < 0 or self.site_index < 0:
            raise MatterVizAdapterError("MatterViz binding-segment indices must not be negative")
        if self.adsorbate_index == self.site_index:
            raise MatterVizAdapterError("MatterViz binding segment requires two distinct sites")

    def to_structure_bond(self) -> dict[str, object]:
        return {
            "site_idx_1": self.adsorbate_index,
            "site_idx_2": self.site_index,
            "order": 1,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "adsorbate_index": self.adsorbate_index,
            "site_index": self.site_index,
        }


@dataclass(frozen=True, slots=True)
class MatterVizSideLabel:
    """Viewer index plus ActiveSite side semantics for UI overlays."""

    viewer_index: int
    side: str

    def __post_init__(self) -> None:
        if self.viewer_index < 0:
            raise MatterVizAdapterError("MatterViz side-label index must not be negative")
        if not self.side.strip():
            raise MatterVizAdapterError("MatterViz side label must not be blank")

    def to_dict(self) -> dict[str, object]:
        return {"viewer_index": self.viewer_index, "side": self.side}


@dataclass(frozen=True, slots=True)
class MatterVizOverlay:
    """Scientific highlight intent resolved into one immutable viewer index frame."""

    active_center_indices: tuple[int, ...] = ()
    bound_adsorbate_indices: tuple[int, ...] = ()
    binding_segments: tuple[MatterVizBindingSegment, ...] = ()
    side_labels: tuple[MatterVizSideLabel, ...] = ()
    binding_mode: str | None = None
    state_label: str | None = None
    conformer_name: str | None = None

    def __post_init__(self) -> None:
        if len(self.active_center_indices) != len(set(self.active_center_indices)):
            raise MatterVizAdapterError("active-center viewer indices must be unique")
        if len(self.bound_adsorbate_indices) != len(set(self.bound_adsorbate_indices)):
            raise MatterVizAdapterError("bound-adsorbate viewer indices must be unique")
        pairs = tuple((item.adsorbate_index, item.site_index) for item in self.binding_segments)
        if len(pairs) != len(set(pairs)):
            raise MatterVizAdapterError("binding-intent viewer segments must be unique")

    def matterviz_bonds(self) -> tuple[dict[str, object], ...]:
        """Return transient MatterViz ``StructureBond`` payloads for binding-intent display."""

        return tuple(segment.to_structure_bond() for segment in self.binding_segments)

    def to_dict(self) -> dict[str, object]:
        return {
            "active_center_indices": list(self.active_center_indices),
            "bound_adsorbate_indices": list(self.bound_adsorbate_indices),
            "binding_segments": [segment.to_dict() for segment in self.binding_segments],
            "side_labels": [label.to_dict() for label in self.side_labels],
            "binding_mode": self.binding_mode,
            "state_label": self.state_label,
            "conformer_name": self.conformer_name,
        }


@dataclass(frozen=True, slots=True)
class MatterVizRuntimeCapability:
    """Presentation capability state; scientific payload generation never depends on it."""

    interactive_available: bool
    fallback_kind: str = "extxyz+manifest"
    message: str | None = None

    def __post_init__(self) -> None:
        if not self.fallback_kind.strip():
            raise MatterVizAdapterError("MatterViz fallback kind must not be blank")
        if self.interactive_available and self.message is not None:
            raise MatterVizAdapterError(
                "available MatterViz runtime must not carry an error message"
            )
        if not self.interactive_available and not self.message:
            raise MatterVizAdapterError("unavailable MatterViz runtime requires a fallback message")

    @classmethod
    def available(cls) -> MatterVizRuntimeCapability:
        return cls(interactive_available=True)

    @classmethod
    def unavailable(
        cls,
        message: str = "interactive MatterViz runtime unavailable",
    ) -> MatterVizRuntimeCapability:
        normalized = " ".join(message.split())
        if not normalized:
            raise MatterVizAdapterError("MatterViz fallback message must not be blank")
        return cls(interactive_available=False, message=normalized)

    def to_dict(self) -> dict[str, object]:
        return {
            "interactive_available": self.interactive_available,
            "fallback_kind": self.fallback_kind,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class MatterVizViewBundle:
    """Complete ECatVASP-to-MatterViz contract plus an identity-preserving fallback."""

    structure: MatterVizStructure
    atom_index_map: MatterVizAtomIndexMap
    overlay: MatterVizOverlay
    runtime: MatterVizRuntimeCapability
    fallback_extxyz: str
    target_version: str = MATTERVIZ_TARGET_VERSION
    contract_version: str = MATTERVIZ_CONTRACT_VERSION

    def __post_init__(self) -> None:
        if len(self.structure.sites) != len(self.atom_index_map.atom_uid_by_viewer_index):
            raise MatterVizAdapterError("MatterViz structure and atom-index map lengths must match")
        if not self.fallback_extxyz.strip():
            raise MatterVizAdapterError("MatterViz bundle requires a non-empty extXYZ fallback")
        if self.target_version != MATTERVIZ_TARGET_VERSION:
            raise MatterVizAdapterError("MatterViz target version must match the locked adapter")
        if self.contract_version != MATTERVIZ_CONTRACT_VERSION:
            raise MatterVizAdapterError("unsupported ECatVASP MatterViz contract version")
        _validate_overlay_indices(self.overlay, len(self.structure.sites))

    def to_dict(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "target_version": self.target_version,
            "structure": self.structure.to_dict(),
            "atom_index_map": self.atom_index_map.to_dict(),
            "overlay": self.overlay.to_dict(),
            "runtime": self.runtime.to_dict(),
            "fallback_extxyz": self.fallback_extxyz,
        }


def build_matterviz_view(
    snapshot: StructureSnapshot,
    *,
    context: ConformerVisualizationContext | None = None,
    matterviz_available: bool = True,
    unavailable_message: str = "interactive MatterViz runtime unavailable",
) -> MatterVizViewBundle:
    """Build a stable typed MatterViz payload without importing a JavaScript runtime."""

    index_map = MatterVizAtomIndexMap.from_snapshot(snapshot)
    structure = _structure_from_snapshot(snapshot)
    overlay = _overlay_from_context(snapshot, index_map, context)
    runtime = (
        MatterVizRuntimeCapability.available()
        if matterviz_available
        else MatterVizRuntimeCapability.unavailable(unavailable_message)
    )
    fallback_extxyz = serialize_structure(snapshot, format=StructureFormat.EXTXYZ)
    return MatterVizViewBundle(
        structure=structure,
        atom_index_map=index_map,
        overlay=overlay,
        runtime=runtime,
        fallback_extxyz=fallback_extxyz,
    )


def matterviz_view_from_document(
    document: StructureDocument,
    *,
    matterviz_available: bool = True,
) -> MatterVizViewBundle:
    """Adapt an already imported ECatVASP document into the unified viewer contract."""

    return build_matterviz_view(document.snapshot, matterviz_available=matterviz_available)


def matterviz_view_from_text(
    text: str,
    *,
    format: StructureFormat | str,
    source_name: str | None = None,
    matterviz_available: bool = True,
) -> MatterVizViewBundle:
    """Parse POSCAR/CIF/XYZ/extXYZ through ECatVASP before building the viewer payload."""

    document = parse_structure(text, format=format, source_name=source_name)
    return matterviz_view_from_document(document, matterviz_available=matterviz_available)


def matterviz_view_from_file(
    path: Path | str,
    *,
    format: StructureFormat | str | None = None,
    matterviz_available: bool = True,
) -> MatterVizViewBundle:
    """Import a structure file, including ECatVASP identity sidecars, before visualization."""

    document = import_structure(path, format=format)
    return matterviz_view_from_document(document, matterviz_available=matterviz_available)


def _structure_from_snapshot(snapshot: StructureSnapshot) -> MatterVizStructure:
    matrix = snapshot.lattice.vectors
    lattice = _matterviz_lattice(matrix, snapshot.periodic)
    sites = tuple(
        MatterVizSite(
            species=(MatterVizSpecies(site.element),),
            abc=site.fractional_coords,
            xyz=_fractional_to_cartesian(site.fractional_coords, matrix),
            label=site.element,
            properties={"ecatvasp_atom_uid": str(site.atom_uid)},
        )
        for site in snapshot.sites
    )
    return MatterVizStructure(
        sites=sites,
        lattice=lattice,
        properties={
            "ecatvasp_contract_version": MATTERVIZ_CONTRACT_VERSION,
            "ecatvasp_snapshot_id": str(snapshot.id),
        },
    )


def _overlay_from_context(
    snapshot: StructureSnapshot,
    index_map: MatterVizAtomIndexMap,
    context: ConformerVisualizationContext | None,
) -> MatterVizOverlay:
    if context is None:
        return MatterVizOverlay()
    if context.snapshot.id != snapshot.id:
        raise MatterVizAdapterError(
            "ConformerVisualizationContext must refer to the supplied StructureSnapshot"
        )
    if len(context.active_center_atom_uids) not in (1, 2, 3):
        raise MatterVizAdapterError("MatterViz ActiveSite overlay supports one to three centers")

    active_center_indices = tuple(
        index_map.viewer_index(atom_uid) for atom_uid in context.active_center_atom_uids
    )
    bound_adsorbate_indices = tuple(
        index_map.viewer_index(atom_uid) for atom_uid in context.bound_adsorbate_atom_uids
    )
    binding_segments = tuple(
        MatterVizBindingSegment(
            adsorbate_index=index_map.viewer_index(edge.adsorbate_atom_uid),
            site_index=index_map.viewer_index(edge.site_atom_uid),
        )
        for edge in context.binding_edges
    )
    side_labels = tuple(
        MatterVizSideLabel(
            viewer_index=index_map.viewer_index(label.atom_uid),
            side=label.side.value,
        )
        for label in context.side_labels
    )
    return MatterVizOverlay(
        active_center_indices=active_center_indices,
        bound_adsorbate_indices=bound_adsorbate_indices,
        binding_segments=binding_segments,
        side_labels=side_labels,
        binding_mode=context.binding_mode.value,
        state_label=context.state_label,
        conformer_name=context.conformer_name,
    )


def _matterviz_lattice(
    matrix: Matrix3,
    pbc: tuple[bool, bool, bool],
) -> MatterVizLattice:
    a_vec, b_vec, c_vec = matrix
    a = _norm(a_vec)
    b = _norm(b_vec)
    c = _norm(c_vec)
    volume = abs(_dot(a_vec, _cross(b_vec, c_vec)))
    if min(a, b, c, volume) <= 0.0:
        raise MatterVizAdapterError("MatterViz adapter requires a non-singular lattice")
    return MatterVizLattice(
        matrix=matrix,
        pbc=pbc,
        volume=volume,
        a=a,
        b=b,
        c=c,
        alpha=_angle_degrees(b_vec, c_vec),
        beta=_angle_degrees(a_vec, c_vec),
        gamma=_angle_degrees(a_vec, b_vec),
    )


def _validate_overlay_indices(overlay: MatterVizOverlay, atom_count: int) -> None:
    indices = [*overlay.active_center_indices, *overlay.bound_adsorbate_indices]
    for segment in overlay.binding_segments:
        indices.extend((segment.adsorbate_index, segment.site_index))
    indices.extend(label.viewer_index for label in overlay.side_labels)
    if any(index < 0 or index >= atom_count for index in indices):
        raise MatterVizAdapterError("MatterViz overlay index is outside the structure payload")


def _fractional_to_cartesian(fractional: Vector3, matrix: Matrix3) -> Vector3:
    return (
        fractional[0] * matrix[0][0]
        + fractional[1] * matrix[1][0]
        + fractional[2] * matrix[2][0],
        fractional[0] * matrix[0][1]
        + fractional[1] * matrix[1][1]
        + fractional[2] * matrix[2][1],
        fractional[0] * matrix[0][2]
        + fractional[1] * matrix[1][2]
        + fractional[2] * matrix[2][2],
    )


def _finite_vector(vector: Vector3) -> bool:
    return all(isfinite(value) for value in vector)


def _norm(vector: Vector3) -> float:
    return sqrt(_dot(vector, vector))


def _dot(left: Vector3, right: Vector3) -> float:
    return left[0] * right[0] + left[1] * right[1] + left[2] * right[2]


def _cross(left: Vector3, right: Vector3) -> Vector3:
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def _angle_degrees(left: Vector3, right: Vector3) -> float:
    denominator = _norm(left) * _norm(right)
    if denominator <= 0.0:
        raise MatterVizAdapterError("MatterViz lattice angle requires non-zero vectors")
    cosine = max(-1.0, min(1.0, _dot(left, right) / denominator))
    return degrees(acos(cosine))

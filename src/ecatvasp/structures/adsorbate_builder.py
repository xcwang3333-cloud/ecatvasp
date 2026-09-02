"""ActiveSite-aware adsorbate placement with explicit binding intent."""

from __future__ import annotations

from dataclasses import dataclass
from math import cos, isfinite, radians, sin

from ecatvasp.domain import (
    ActiveSite,
    AtomUid,
    BindingMode,
    SiteSide,
    StructureSite,
    StructureSnapshot,
    new_atom_uid,
)
from ecatvasp.structures._placement import (
    COLLISION_TOLERANCE_ANGSTROM,
    Vector3,
    add,
    cartesian_to_fractional,
    cross,
    dot,
    fractional_to_cartesian,
    minimum_image_distance,
    norm,
    pbc_centroid_fractional,
    scale,
    slab_normal,
    subtract,
    wrap_fractional,
)
from ecatvasp.structures.active_site import (
    resolve_active_site_centers,
    validate_active_site_snapshot_compatibility,
)
from ecatvasp.structures.addition import StructureAdditionResult, append_structure_sites
from ecatvasp.structures.adsorbates import AdsorbateTemplate, get_adsorbate_template

_ALLOWED_BINDING_MODES = frozenset(
    {BindingMode.SINGLE_CENTER, BindingMode.BRIDGE, BindingMode.MULTICENTER}
)
_VECTOR_TOLERANCE = 1.0e-12


class AdsorbateBuilderError(ValueError):
    """Raised when an adsorbate placement violates Block 7 invariants."""


@dataclass(frozen=True, slots=True)
class AdsorbateContactSpec:
    """Template-local binding intent from one adsorbate atom to one ActiveSite center."""

    adsorbate_atom_key: str
    site_atom_uid: AtomUid

    def __post_init__(self) -> None:
        if not self.adsorbate_atom_key.strip():
            raise ValueError("adsorbate_atom_key must not be blank")


@dataclass(frozen=True, slots=True)
class AdsorbatePlacementSpec:
    """Explicit instructions for one adsorbate geometry seed."""

    template_key: str
    target_center_atom_uids: tuple[AtomUid, ...]
    binding_mode: BindingMode
    height_angstrom: float
    contacts: tuple[AdsorbateContactSpec, ...]
    primary_anchor_atom_key: str | None = None
    placement_direction_cartesian: Vector3 | None = None
    orientation_vector_cartesian: Vector3 | None = None
    roll_degrees: float = 0.0
    label: str | None = None

    def __post_init__(self) -> None:
        if not self.template_key.strip():
            raise ValueError("template_key must not be blank")
        if not self.target_center_atom_uids:
            raise ValueError("at least one target ActiveSite center is required")
        if len(self.target_center_atom_uids) != len(set(self.target_center_atom_uids)):
            raise ValueError("target_center_atom_uids must be unique")
        if self.binding_mode not in _ALLOWED_BINDING_MODES:
            raise ValueError(
                "binding_mode must be SINGLE_CENTER, BRIDGE, or MULTICENTER"
            )
        if isinstance(self.height_angstrom, bool) or not isinstance(
            self.height_angstrom, (int, float)
        ):
            raise ValueError("height_angstrom must be a finite positive number")
        if not isfinite(self.height_angstrom) or self.height_angstrom <= 0:
            raise ValueError("height_angstrom must be a finite positive number")
        if not self.contacts:
            raise ValueError("at least one adsorbate contact intent is required")
        if len(self.contacts) != len(set(self.contacts)):
            raise ValueError("adsorbate contact intents must be unique")
        if self.primary_anchor_atom_key is not None and not self.primary_anchor_atom_key.strip():
            raise ValueError("primary_anchor_atom_key must not be blank when defined")
        if not isfinite(self.roll_degrees):
            raise ValueError("roll_degrees must be finite")
        if self.placement_direction_cartesian is not None:
            _validate_vector(self.placement_direction_cartesian, "placement_direction_cartesian")
        if self.orientation_vector_cartesian is not None:
            _validate_vector(self.orientation_vector_cartesian, "orientation_vector_cartesian")
        if self.label is not None and not self.label.strip():
            raise ValueError("label must not be blank when defined")


@dataclass(frozen=True, slots=True)
class AdsorbateAtomResult:
    """Mapping from one template-local atom key to its fresh scientific identity."""

    atom_key: str
    atom_uid: AtomUid
    element: str

    def __post_init__(self) -> None:
        if not self.atom_key.strip():
            raise ValueError("adsorbate result atom_key must not be blank")
        if not self.element.strip():
            raise ValueError("adsorbate result element must not be blank")


@dataclass(frozen=True, slots=True)
class AdsorbateContactIntent:
    """Resolved Block 7 contact intent ready for Block 8 BindingEdge construction."""

    adsorbate_atom_key: str
    adsorbate_atom_uid: AtomUid
    site_atom_uid: AtomUid

    def __post_init__(self) -> None:
        if not self.adsorbate_atom_key.strip():
            raise ValueError("adsorbate contact atom key must not be blank")


@dataclass(frozen=True, slots=True)
class AdsorbateBuildResult:
    """Immutable adsorbate child structure plus explicit Block 8 handoff metadata."""

    addition: StructureAdditionResult
    template_key: str
    adsorbate_atoms: tuple[AdsorbateAtomResult, ...]
    primary_anchor_atom_key: str
    target_center_atom_uids: tuple[AtomUid, ...]
    binding_mode_intent: BindingMode
    contacts: tuple[AdsorbateContactIntent, ...]
    placement_direction_cartesian: Vector3
    height_angstrom: float
    orientation_vector_cartesian: Vector3 | None
    roll_degrees: float

    def __post_init__(self) -> None:
        if not self.template_key.strip():
            raise ValueError("template_key must not be blank")
        if not self.adsorbate_atoms:
            raise ValueError("adsorbate build result requires added atoms")
        atom_keys = tuple(atom.atom_key for atom in self.adsorbate_atoms)
        atom_uids = tuple(atom.atom_uid for atom in self.adsorbate_atoms)
        if len(atom_keys) != len(set(atom_keys)):
            raise ValueError("adsorbate result atom keys must be unique")
        if len(atom_uids) != len(set(atom_uids)):
            raise ValueError("adsorbate result atom_uids must be unique")
        if self.addition.added_atom_uids != atom_uids:
            raise ValueError("adsorbate atom identities must match append-only addition order")
        if self.primary_anchor_atom_key not in atom_keys:
            raise ValueError("primary anchor atom key must reference an added adsorbate atom")
        if not self.target_center_atom_uids:
            raise ValueError("adsorbate result requires target ActiveSite centers")
        if len(self.target_center_atom_uids) != len(set(self.target_center_atom_uids)):
            raise ValueError("target ActiveSite center identities must be unique")
        preserved_uids = set(self.addition.preserved_atom_uids)
        if any(atom_uid not in preserved_uids for atom_uid in self.target_center_atom_uids):
            raise ValueError("target ActiveSite centers must be preserved source atoms")
        if self.binding_mode_intent not in _ALLOWED_BINDING_MODES:
            raise ValueError("adsorbate result contains unsupported binding mode intent")
        if not self.contacts:
            raise ValueError("adsorbate result requires resolved contact intents")
        if len(self.contacts) != len(set(self.contacts)):
            raise ValueError("resolved adsorbate contact intents must be unique")

        uid_by_key = {atom.atom_key: atom.atom_uid for atom in self.adsorbate_atoms}
        target_set = set(self.target_center_atom_uids)
        for contact in self.contacts:
            if contact.adsorbate_atom_key not in uid_by_key:
                raise ValueError("contact intent references an unknown adsorbate atom key")
            if uid_by_key[contact.adsorbate_atom_key] != contact.adsorbate_atom_uid:
                raise ValueError("contact intent atom_uid must match the template-key mapping")
            if contact.site_atom_uid not in target_set:
                raise ValueError("contact intent site_atom_uid must be a target ActiveSite center")
        _validate_result_contact_intent(
            self.binding_mode_intent,
            self.target_center_atom_uids,
            self.contacts,
        )

        if not isfinite(self.height_angstrom) or self.height_angstrom <= 0:
            raise ValueError("height_angstrom must remain finite and positive")
        _validate_unit_vector(
            self.placement_direction_cartesian,
            "placement_direction_cartesian",
        )
        if self.orientation_vector_cartesian is not None:
            _validate_unit_vector(
                self.orientation_vector_cartesian,
                "orientation_vector_cartesian",
            )
        if not isfinite(self.roll_degrees):
            raise ValueError("roll_degrees must be finite")

    @property
    def snapshot(self) -> StructureSnapshot:
        return self.addition.snapshot

    @property
    def adsorbate_atom_uids(self) -> tuple[AtomUid, ...]:
        return tuple(atom.atom_uid for atom in self.adsorbate_atoms)

    @property
    def primary_anchor_atom_uid(self) -> AtomUid:
        return self.atom_uid_for_key(self.primary_anchor_atom_key)

    def atom_uid_for_key(self, atom_key: str) -> AtomUid:
        """Resolve a fresh adsorbate atom_uid by its template-local key."""

        for atom in self.adsorbate_atoms:
            if atom.atom_key == atom_key:
                return atom.atom_uid
        raise KeyError(atom_key)


def build_adsorbate(
    source: StructureSnapshot,
    active_site: ActiveSite,
    spec: AdsorbatePlacementSpec,
) -> AdsorbateBuildResult:
    """Append one rigid adsorbate seed while preserving all source atom identities."""

    validate_active_site_snapshot_compatibility(active_site, source)
    template = get_adsorbate_template(spec.template_key)
    all_centers = resolve_active_site_centers(active_site, source)
    target_centers = _resolve_target_centers(active_site, all_centers, spec)
    primary_anchor_key = spec.primary_anchor_atom_key or template.primary_anchor_atom_key
    if primary_anchor_key not in template.anchor_atom_keys:
        raise AdsorbateBuilderError(
            f"{primary_anchor_key!r} is not an eligible anchor for template {template.key!r}"
        )

    _validate_contact_intent(template, spec)
    placement_direction = _resolve_placement_direction(source, active_site, spec)
    target_fractional = pbc_centroid_fractional(source, target_centers)
    target_cartesian = fractional_to_cartesian(target_fractional, source.lattice)
    anchor_cartesian = add(
        target_cartesian,
        scale(placement_direction, spec.height_angstrom),
    )

    orientation_axis = _resolve_orientation_axis(template, spec, placement_direction)
    added_sites, atom_results = _place_template_atoms(
        source=source,
        template=template,
        primary_anchor_key=primary_anchor_key,
        anchor_cartesian=anchor_cartesian,
        placement_direction=placement_direction,
        orientation_axis=orientation_axis,
        roll_degrees=spec.roll_degrees,
    )
    _validate_new_atom_collisions(source, added_sites)

    addition = append_structure_sites(source, added_sites, label=spec.label)
    uid_by_key = {atom.atom_key: atom.atom_uid for atom in atom_results}
    contacts = tuple(
        AdsorbateContactIntent(
            adsorbate_atom_key=contact.adsorbate_atom_key,
            adsorbate_atom_uid=uid_by_key[contact.adsorbate_atom_key],
            site_atom_uid=contact.site_atom_uid,
        )
        for contact in spec.contacts
    )

    return AdsorbateBuildResult(
        addition=addition,
        template_key=template.key,
        adsorbate_atoms=atom_results,
        primary_anchor_atom_key=primary_anchor_key,
        target_center_atom_uids=spec.target_center_atom_uids,
        binding_mode_intent=spec.binding_mode,
        contacts=contacts,
        placement_direction_cartesian=placement_direction,
        height_angstrom=float(spec.height_angstrom),
        orientation_vector_cartesian=orientation_axis,
        roll_degrees=float(spec.roll_degrees),
    )


def _resolve_target_centers(
    active_site: ActiveSite,
    all_centers: tuple[StructureSite, ...],
    spec: AdsorbatePlacementSpec,
) -> tuple[StructureSite, ...]:
    active_uids = active_site.center_atom_uids
    active_set = set(active_uids)
    if any(atom_uid not in active_set for atom_uid in spec.target_center_atom_uids):
        raise AdsorbateBuilderError("target centers must belong to the supplied ActiveSite")

    indices = tuple(active_uids.index(atom_uid) for atom_uid in spec.target_center_atom_uids)
    if indices != tuple(sorted(indices)):
        raise AdsorbateBuilderError(
            "target centers must preserve ActiveSite.center_atom_uids ordering"
        )
    by_uid = {site.atom_uid: site for site in all_centers}
    return tuple(by_uid[atom_uid] for atom_uid in spec.target_center_atom_uids)


def _validate_contact_intent(
    template: AdsorbateTemplate,
    spec: AdsorbatePlacementSpec,
) -> None:
    atom_keys = set(template.atom_keys)
    if any(contact.adsorbate_atom_key not in atom_keys for contact in spec.contacts):
        raise AdsorbateBuilderError("contact intent references an unknown template atom key")

    target_set = set(spec.target_center_atom_uids)
    contacted_sites = {contact.site_atom_uid for contact in spec.contacts}
    if contacted_sites != target_set:
        raise AdsorbateBuilderError(
            "contact intents must cover exactly the ordered target ActiveSite centers"
        )

    if spec.binding_mode is BindingMode.SINGLE_CENTER:
        if len(spec.target_center_atom_uids) != 1:
            raise AdsorbateBuilderError("SINGLE_CENTER placement requires exactly one target")
        return

    if spec.binding_mode is BindingMode.BRIDGE:
        if len(spec.target_center_atom_uids) != 2:
            raise AdsorbateBuilderError("BRIDGE placement requires exactly two targets")
        contacts_by_atom: dict[str, set[AtomUid]] = {}
        for contact in spec.contacts:
            contacts_by_atom.setdefault(contact.adsorbate_atom_key, set()).add(
                contact.site_atom_uid
            )
        if not any(sites == target_set for sites in contacts_by_atom.values()):
            raise AdsorbateBuilderError(
                "BRIDGE placement requires one adsorbate atom to contact both target centers"
            )
        return

    if len(spec.target_center_atom_uids) < 2:
        raise AdsorbateBuilderError("MULTICENTER placement requires at least two targets")
    contacted_atoms = {contact.adsorbate_atom_key for contact in spec.contacts}
    if len(contacted_atoms) < 2:
        raise AdsorbateBuilderError(
            "MULTICENTER placement requires at least two distinct adsorbate contact atoms"
        )


def _validate_result_contact_intent(
    binding_mode: BindingMode,
    target_center_atom_uids: tuple[AtomUid, ...],
    contacts: tuple[AdsorbateContactIntent, ...],
) -> None:
    target_set = set(target_center_atom_uids)
    contacted_sites = {contact.site_atom_uid for contact in contacts}
    if contacted_sites != target_set:
        raise ValueError("resolved contacts must cover exactly the target ActiveSite centers")

    if binding_mode is BindingMode.SINGLE_CENTER:
        if len(target_center_atom_uids) != 1:
            raise ValueError("SINGLE_CENTER result requires exactly one target")
        return

    if binding_mode is BindingMode.BRIDGE:
        if len(target_center_atom_uids) != 2:
            raise ValueError("BRIDGE result requires exactly two targets")
        contacts_by_atom: dict[str, set[AtomUid]] = {}
        for contact in contacts:
            contacts_by_atom.setdefault(contact.adsorbate_atom_key, set()).add(
                contact.site_atom_uid
            )
        if not any(sites == target_set for sites in contacts_by_atom.values()):
            raise ValueError(
                "BRIDGE result requires one adsorbate atom to contact both target centers"
            )
        return

    if len(target_center_atom_uids) < 2:
        raise ValueError("MULTICENTER result requires at least two targets")
    if len({contact.adsorbate_atom_key for contact in contacts}) < 2:
        raise ValueError(
            "MULTICENTER result requires at least two distinct adsorbate contact atoms"
        )


def _resolve_placement_direction(
    source: StructureSnapshot,
    active_site: ActiveSite,
    spec: AdsorbatePlacementSpec,
) -> Vector3:
    if spec.placement_direction_cartesian is not None:
        return _normalized(spec.placement_direction_cartesian, "placement_direction_cartesian")

    side_by_uid = {label.atom_uid: label.side for label in active_site.side_labels}
    try:
        sides = tuple(side_by_uid[atom_uid] for atom_uid in spec.target_center_atom_uids)
    except KeyError as exc:
        raise AdsorbateBuilderError(
            "AUTO placement direction requires side labels for every target center"
        ) from exc

    if all(side is SiteSide.TOP for side in sides):
        return slab_normal(source.lattice)
    if all(side is SiteSide.BOTTOM for side in sides):
        return scale(slab_normal(source.lattice), -1.0)
    raise AdsorbateBuilderError(
        "AUTO placement direction is ambiguous for in-plane, unspecified, or mixed-side targets; "
        "provide placement_direction_cartesian explicitly"
    )


def _resolve_orientation_axis(
    template: AdsorbateTemplate,
    spec: AdsorbatePlacementSpec,
    placement_direction: Vector3,
) -> Vector3 | None:
    if len(template.atoms) == 1:
        if spec.orientation_vector_cartesian is not None or abs(spec.roll_degrees) > 0:
            raise AdsorbateBuilderError(
                "single-atom adsorbates do not accept orientation or roll settings"
            )
        return None

    if spec.orientation_vector_cartesian is not None:
        return _normalized(spec.orientation_vector_cartesian, "orientation_vector_cartesian")
    return placement_direction


def _place_template_atoms(
    *,
    source: StructureSnapshot,
    template: AdsorbateTemplate,
    primary_anchor_key: str,
    anchor_cartesian: Vector3,
    placement_direction: Vector3,
    orientation_axis: Vector3 | None,
    roll_degrees: float,
) -> tuple[tuple[StructureSite, ...], tuple[AdsorbateAtomResult, ...]]:
    anchor_local = template.atom(primary_anchor_key).cartesian_coords
    reference_key = _orientation_reference_for_anchor(template, primary_anchor_key)
    source_axis: Vector3 | None = None
    target_axis: Vector3 | None = None
    if reference_key is not None:
        reference_local = template.atom(reference_key).cartesian_coords
        source_axis = _normalized(
            subtract(reference_local, anchor_local),
            "template orientation axis",
        )
        target_axis = orientation_axis if orientation_axis is not None else placement_direction

    sites: list[StructureSite] = []
    results: list[AdsorbateAtomResult] = []
    for atom in template.atoms:
        relative = subtract(atom.cartesian_coords, anchor_local)
        transformed = relative
        if source_axis is not None and target_axis is not None:
            transformed = _rotate_from_to(transformed, source_axis, target_axis)
            if abs(roll_degrees) > 0:
                transformed = _rotate_around_axis(
                    transformed,
                    target_axis,
                    radians(roll_degrees),
                )
        placed_cartesian = add(anchor_cartesian, transformed)
        try:
            fractional = wrap_fractional(
                cartesian_to_fractional(placed_cartesian, source.lattice),
                source.periodic,
            )
        except ValueError as exc:
            raise AdsorbateBuilderError(str(exc)) from exc
        atom_uid = new_atom_uid()
        sites.append(
            StructureSite(
                atom_uid=atom_uid,
                element=atom.element,
                fractional_coords=fractional,
            )
        )
        results.append(
            AdsorbateAtomResult(
                atom_key=atom.key,
                atom_uid=atom_uid,
                element=atom.element,
            )
        )

    return tuple(sites), tuple(results)


def _orientation_reference_for_anchor(
    template: AdsorbateTemplate,
    primary_anchor_key: str,
) -> str | None:
    if len(template.atoms) == 1:
        return None
    reference = template.orientation_reference_atom_key
    if reference is not None and reference != primary_anchor_key:
        return reference
    for atom in template.atoms:
        if atom.key != primary_anchor_key:
            return atom.key
    return None


def _validate_new_atom_collisions(
    source: StructureSnapshot,
    added_sites: tuple[StructureSite, ...],
) -> None:
    for added in added_sites:
        for existing in source.sites:
            distance = minimum_image_distance(
                added.fractional_coords,
                existing.fractional_coords,
                source.lattice,
                source.periodic,
            )
            if distance <= COLLISION_TOLERANCE_ANGSTROM:
                raise AdsorbateBuilderError(
                    "adsorbate placement numerically overlaps an existing structure atom"
                )

    for left_index, left in enumerate(added_sites):
        for right in added_sites[left_index + 1 :]:
            distance = minimum_image_distance(
                left.fractional_coords,
                right.fractional_coords,
                source.lattice,
                source.periodic,
            )
            if distance <= COLLISION_TOLERANCE_ANGSTROM:
                raise AdsorbateBuilderError(
                    "adsorbate placement produces overlapping periodic images"
                )


def _rotate_from_to(vector: Vector3, source_axis: Vector3, target_axis: Vector3) -> Vector3:
    source = _normalized(source_axis, "source orientation axis")
    target = _normalized(target_axis, "target orientation axis")
    cosine = max(-1.0, min(1.0, dot(source, target)))
    axis = cross(source, target)
    sine = norm(axis)

    if sine <= _VECTOR_TOLERANCE:
        if cosine > 0:
            return vector
        helper: Vector3 = (1.0, 0.0, 0.0)
        if abs(dot(source, helper)) > 0.9:
            helper = (0.0, 1.0, 0.0)
        perpendicular = _normalized(cross(source, helper), "antiparallel rotation axis")
        return _rotate_around_axis(vector, perpendicular, radians(180.0))

    unit_axis = scale(axis, 1.0 / sine)
    return add(
        add(scale(vector, cosine), scale(cross(unit_axis, vector), sine)),
        scale(unit_axis, dot(unit_axis, vector) * (1.0 - cosine)),
    )


def _rotate_around_axis(vector: Vector3, axis: Vector3, angle_radians: float) -> Vector3:
    unit_axis = _normalized(axis, "roll axis")
    cosine = cos(angle_radians)
    sine = sin(angle_radians)
    return add(
        add(scale(vector, cosine), scale(cross(unit_axis, vector), sine)),
        scale(unit_axis, dot(unit_axis, vector) * (1.0 - cosine)),
    )


def _normalized(vector: Vector3, name: str) -> Vector3:
    _validate_vector(vector, name)
    magnitude = norm(vector)
    if magnitude <= _VECTOR_TOLERANCE:
        raise AdsorbateBuilderError(f"{name} must have nonzero magnitude")
    return scale(vector, 1.0 / magnitude)


def _validate_vector(vector: Vector3, name: str) -> None:
    if len(vector) != 3 or not all(isfinite(component) for component in vector):
        raise ValueError(f"{name} must contain three finite components")
    if norm(vector) <= _VECTOR_TOLERANCE:
        raise ValueError(f"{name} must have nonzero magnitude")


def _validate_unit_vector(vector: Vector3, name: str) -> None:
    _validate_vector(vector, name)
    if abs(norm(vector) - 1.0) > 1.0e-9:
        raise ValueError(f"{name} must be normalized")

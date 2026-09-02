"""Active-site tooling bridging immutable structure snapshots and the frozen domain model."""

from __future__ import annotations

from re import sub

from ecatvasp.domain import (
    ActiveSite,
    AtomUid,
    SideLabel,
    SiteSide,
    StructureSite,
    StructureSnapshot,
    StructureVariant,
)
from ecatvasp.structures.multi_metal import EnsembleSideTopology, MultiMetalSiteResult
from ecatvasp.structures.single_metal import SingleMetalSiteResult


class ActiveSiteToolingError(ValueError):
    """Raised when an active-site operation violates structural context invariants."""


def normalize_topology(value: str | None) -> str | None:
    """Normalize optional topology text to stable lowercase kebab-case."""

    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        raise ActiveSiteToolingError("topology must not be blank when defined")
    normalized = sub(r"[\s_]+", "-", stripped.lower())
    normalized = sub(r"-+", "-", normalized).strip("-")
    if not normalized:
        raise ActiveSiteToolingError("topology must contain meaningful text")
    return normalized


def normalize_coordination_environment(value: str | None) -> str | None:
    """Normalize optional coordination text without inventing chemical semantics."""

    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        raise ActiveSiteToolingError(
            "coordination_environment must not be blank when defined"
        )
    parts = tuple(part.strip() for part in stripped.split("|"))
    if any(not part for part in parts):
        raise ActiveSiteToolingError(
            "coordination_environment must not contain empty center segments"
        )
    return "|".join(" ".join(part.split()) for part in parts)


def validate_active_site_snapshot_compatibility(
    active_site: ActiveSite,
    snapshot: StructureSnapshot,
) -> None:
    """Require every stable active-center identity to exist in a snapshot."""

    missing = tuple(
        atom_uid
        for atom_uid in active_site.center_atom_uids
        if not snapshot.contains_atom(atom_uid)
    )
    if missing:
        raise ActiveSiteToolingError(
            "an ActiveSite center atom_uid is absent from the supplied StructureSnapshot"
        )


def validate_active_site_current_context(
    active_site: ActiveSite,
    variant: StructureVariant,
    snapshot: StructureSnapshot,
) -> None:
    """Validate an ActiveSite against a StructureVariant's authoritative current snapshot."""

    if active_site.structure_variant_id != variant.id:
        raise ActiveSiteToolingError(
            "ActiveSite and StructureVariant must reference the same structure variant"
        )
    _validate_current_snapshot(variant, snapshot)
    validate_active_site_snapshot_compatibility(active_site, snapshot)


def resolve_active_site_centers(
    active_site: ActiveSite,
    snapshot: StructureSnapshot,
) -> tuple[StructureSite, ...]:
    """Resolve center atoms in stable ActiveSite center_atom_uids order."""

    validate_active_site_snapshot_compatibility(active_site, snapshot)
    sites_by_uid = {site.atom_uid: site for site in snapshot.sites}
    return tuple(sites_by_uid[atom_uid] for atom_uid in active_site.center_atom_uids)


def create_active_site(
    *,
    variant: StructureVariant,
    snapshot: StructureSnapshot,
    center_atom_uids: tuple[AtomUid, ...],
    side_labels: tuple[SideLabel, ...] = (),
    topology: str | None = None,
    coordination_environment: str | None = None,
) -> ActiveSite:
    """Create a one-, two-, or three-center ActiveSite on a variant's current snapshot."""

    _validate_current_snapshot(variant, snapshot)
    centers = tuple(center_atom_uids)
    if len(centers) not in (1, 2, 3):
        raise ActiveSiteToolingError(
            "ActiveSite tooling supports exactly one, two, or three center atom_uids"
        )
    if len(centers) != len(set(centers)):
        raise ActiveSiteToolingError("center_atom_uids must be unique")
    if any(not snapshot.contains_atom(atom_uid) for atom_uid in centers):
        raise ActiveSiteToolingError(
            "all center atom_uids must exist in the current StructureSnapshot"
        )

    labels = tuple(side_labels)
    if labels:
        if len(labels) != len(centers):
            raise ActiveSiteToolingError(
                "side_labels must either be omitted or cover every active center"
            )
        if tuple(label.atom_uid for label in labels) != centers:
            raise ActiveSiteToolingError(
                "side_labels must follow center_atom_uids order and cover the same atoms"
            )
        if any(not isinstance(label.side, SiteSide) for label in labels):
            raise ActiveSiteToolingError("every side label must contain a SiteSide value")

    active_site = ActiveSite(
        structure_variant_id=variant.id,
        center_atom_uids=centers,
        topology=normalize_topology(topology),
        coordination_environment=normalize_coordination_environment(
            coordination_environment
        ),
        side_labels=labels,
    )
    validate_active_site_current_context(active_site, variant, snapshot)
    return active_site


def active_site_from_single_metal(
    *,
    variant: StructureVariant,
    result: SingleMetalSiteResult,
) -> ActiveSite:
    """Create a canonical single-center ActiveSite from Block 4 builder output."""

    return create_active_site(
        variant=variant,
        snapshot=result.snapshot,
        center_atom_uids=(result.metal_atom_uid,),
        side_labels=(SideLabel(result.metal_atom_uid, result.side),),
        topology="single-center",
        coordination_environment=result.coordination_signature,
    )


def active_site_from_multi_metal(
    *,
    variant: StructureVariant,
    result: MultiMetalSiteResult,
) -> ActiveSite:
    """Create a canonical dual/triple ActiveSite from Block 5 builder output."""

    topology = result.metal_metal_topology_intent
    if topology is None:
        topology = _topology_from_side_topology(result.side_topology)
    coordination_environment = "|".join(
        f"{center.metal_element}({center.coordination_signature})"
        for center in result.centers
    )
    return create_active_site(
        variant=variant,
        snapshot=result.snapshot,
        center_atom_uids=result.metal_atom_uids,
        side_labels=tuple(
            SideLabel(center.metal_atom_uid, center.side) for center in result.centers
        ),
        topology=topology,
        coordination_environment=coordination_environment,
    )


def _validate_current_snapshot(
    variant: StructureVariant,
    snapshot: StructureSnapshot,
) -> None:
    current = variant.current_structure_snapshot_id
    if current is None:
        raise ActiveSiteToolingError(
            "StructureVariant requires a current_structure_snapshot_id before creating or "
            "validating a current ActiveSite context"
        )
    if current != snapshot.id:
        raise ActiveSiteToolingError(
            "the supplied StructureSnapshot is not the StructureVariant current snapshot"
        )


def _topology_from_side_topology(side_topology: EnsembleSideTopology) -> str:
    mapping = {
        EnsembleSideTopology.SAME_SIDE: "same-side",
        EnsembleSideTopology.OPPOSITE_SIDE: "opposite-side",
        EnsembleSideTopology.IN_PLANE: "in-plane",
        EnsembleSideTopology.MIXED: "mixed-side",
    }
    return mapping[side_topology]

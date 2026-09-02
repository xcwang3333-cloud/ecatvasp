"""Adsorption-state and conformer materialization for Model Studio Block 8."""

from __future__ import annotations

from dataclasses import dataclass

from ecatvasp.domain import (
    ActiveSite,
    AdsorptionState,
    AdsorptionStateId,
    AtomUid,
    BindingEdge,
    BindingMode,
    SideLabel,
    StateConformer,
    StructureSnapshot,
    StructureSnapshotId,
    StructureVariant,
    validate_conformer_context,
)
from ecatvasp.structures.active_site import validate_active_site_snapshot_compatibility
from ecatvasp.structures.adsorbate_builder import AdsorbateBuildResult

_ALLOWED_MATERIALIZED_BINDING_MODES = frozenset(
    {BindingMode.SINGLE_CENTER, BindingMode.BRIDGE, BindingMode.MULTICENTER}
)


class StateConformerToolingError(ValueError):
    """Raised when Block 8 state/conformer materialization violates invariants."""


@dataclass(frozen=True, slots=True)
class ConformerSignature:
    """Exact scientific signature used for fail-closed conformer deduplication."""

    adsorption_state_id: AdsorptionStateId
    structure_snapshot_id: StructureSnapshotId
    binding_mode: BindingMode
    binding_edges: tuple[tuple[AtomUid, AtomUid], ...]


@dataclass(frozen=True, slots=True)
class ConformerVisualizationContext:
    """Viewer-agnostic context consumed later by the MatterViz integration layer."""

    snapshot: StructureSnapshot
    state_label: str
    conformer_name: str
    binding_mode: BindingMode
    active_center_atom_uids: tuple[AtomUid, ...]
    bound_adsorbate_atom_uids: tuple[AtomUid, ...]
    binding_edges: tuple[BindingEdge, ...]
    side_labels: tuple[SideLabel, ...]


def create_adsorption_state(
    structure_variant: StructureVariant,
    active_site: ActiveSite,
    *,
    state_label: str,
    adsorbates: tuple[str, ...] = (),
    coverage: float | None = None,
    reaction_role: str | None = None,
    existing_states: tuple[AdsorptionState, ...] = (),
) -> AdsorptionState:
    """Create one chemical adsorption-state identity independent of conformer geometry."""

    if active_site.structure_variant_id != structure_variant.id:
        raise StateConformerToolingError(
            "ActiveSite and StructureVariant must refer to the same structure hypothesis"
        )

    normalized_label = _normalize_text(state_label, "state_label")
    normalized_adsorbates = tuple(
        _normalize_text(adsorbate, "adsorbate") for adsorbate in adsorbates
    )
    if len(normalized_adsorbates) != len(set(normalized_adsorbates)):
        raise StateConformerToolingError("adsorbates must not contain duplicate labels")
    normalized_role = (
        None if reaction_role is None else _normalize_text(reaction_role, "reaction_role")
    )

    duplicate_key = normalized_label.casefold()
    for state in existing_states:
        if (
            state.structure_variant_id == structure_variant.id
            and state.active_site_id == active_site.id
            and _normalize_text(state.state_label, "existing state_label").casefold()
            == duplicate_key
        ):
            raise StateConformerToolingError(
                "an AdsorptionState with the same normalized label already exists for this ActiveSite"
            )

    return AdsorptionState(
        structure_variant_id=structure_variant.id,
        active_site_id=active_site.id,
        state_label=normalized_label,
        adsorbates=normalized_adsorbates,
        coverage=coverage,
        reaction_role=normalized_role,
    )


def state_conformer_from_adsorbate_build(
    state: AdsorptionState,
    active_site: ActiveSite,
    build_result: AdsorbateBuildResult,
    *,
    name: str,
    orientation: str | None = None,
    rank: int | None = None,
    parent_conformer: StateConformer | None = None,
    existing_conformers: tuple[StateConformer, ...] = (),
) -> StateConformer:
    """Materialize one Block 7 result into a frozen StateConformer and BindingEdges."""

    _validate_build_result_handoff(state, active_site, build_result)
    normalized_name = _normalize_text(name, "conformer name")
    normalized_orientation = (
        None if orientation is None else _normalize_text(orientation, "orientation")
    )

    if parent_conformer is not None:
        _validate_parent_lineage(state, build_result, parent_conformer)

    binding_edges = tuple(
        BindingEdge(
            adsorbate_atom_uid=contact.adsorbate_atom_uid,
            site_atom_uid=contact.site_atom_uid,
        )
        for contact in build_result.contacts
    )
    conformer = StateConformer(
        adsorption_state_id=state.id,
        structure_snapshot_id=build_result.snapshot.id,
        name=normalized_name,
        binding_mode=build_result.binding_mode_intent,
        binding_edges=binding_edges,
        orientation=normalized_orientation,
        parent_conformer_id=(None if parent_conformer is None else parent_conformer.id),
        rank=rank,
    )

    validate_conformer_context(
        active_site=active_site,
        state=state,
        conformer=conformer,
        snapshot=build_result.snapshot,
    )
    _validate_materialized_edges(build_result, conformer)

    collection = existing_conformers
    if parent_conformer is not None and all(
        item.id != parent_conformer.id for item in collection
    ):
        collection = (*collection, parent_conformer)
    validate_conformer_collection(state, (*collection, conformer))
    return conformer


def conformer_signature(conformer: StateConformer) -> ConformerSignature:
    """Return exact identity-free metadata used to reject duplicate conformers."""

    edge_pairs = tuple(
        sorted(
            (
                (edge.adsorbate_atom_uid, edge.site_atom_uid)
                for edge in conformer.binding_edges
            ),
            key=lambda pair: (pair[0].int, pair[1].int),
        )
    )
    return ConformerSignature(
        adsorption_state_id=conformer.adsorption_state_id,
        structure_snapshot_id=conformer.structure_snapshot_id,
        binding_mode=conformer.binding_mode,
        binding_edges=edge_pairs,
    )


def validate_conformer_collection(
    state: AdsorptionState,
    conformers: tuple[StateConformer, ...],
) -> None:
    """Validate one state's conformer names, ranks, exact signatures, and parent graph."""

    if any(conformer.adsorption_state_id != state.id for conformer in conformers):
        raise StateConformerToolingError(
            "all conformers in a collection must belong to the supplied AdsorptionState"
        )

    conformer_ids = tuple(conformer.id for conformer in conformers)
    if len(conformer_ids) != len(set(conformer_ids)):
        raise StateConformerToolingError("conformer IDs must be unique within a collection")

    normalized_names = tuple(
        _normalize_text(conformer.name, "conformer name").casefold()
        for conformer in conformers
    )
    if len(normalized_names) != len(set(normalized_names)):
        raise StateConformerToolingError(
            "conformer names must be unique within an AdsorptionState"
        )

    defined_ranks = tuple(
        conformer.rank for conformer in conformers if conformer.rank is not None
    )
    if len(defined_ranks) != len(set(defined_ranks)):
        raise StateConformerToolingError(
            "defined conformer ranks must be unique within an AdsorptionState"
        )

    signatures = tuple(conformer_signature(conformer) for conformer in conformers)
    if len(signatures) != len(set(signatures)):
        raise StateConformerToolingError(
            "exact duplicate StateConformer scientific signatures are not allowed"
        )

    by_id = {conformer.id: conformer for conformer in conformers}
    for conformer in conformers:
        parent_id = conformer.parent_conformer_id
        if parent_id is None:
            continue
        if parent_id == conformer.id:
            raise StateConformerToolingError("a StateConformer cannot be its own parent")
        if parent_id not in by_id:
            raise StateConformerToolingError(
                "StateConformer parent must be present in the same state collection"
            )

    _validate_parent_cycles(conformers, by_id)


def resolve_conformer_visualization_context(
    state: AdsorptionState,
    active_site: ActiveSite,
    conformer: StateConformer,
    snapshot: StructureSnapshot,
) -> ConformerVisualizationContext:
    """Resolve stable scientific UIDs for a viewer without exposing viewer-local indices."""

    validate_conformer_context(
        active_site=active_site,
        state=state,
        conformer=conformer,
        snapshot=snapshot,
    )
    bound_adsorbate_uids = _unique_in_order(
        tuple(edge.adsorbate_atom_uid for edge in conformer.binding_edges)
    )
    side_by_uid = {label.atom_uid: label for label in active_site.side_labels}
    ordered_side_labels = tuple(
        side_by_uid[atom_uid]
        for atom_uid in active_site.center_atom_uids
        if atom_uid in side_by_uid
    )
    return ConformerVisualizationContext(
        snapshot=snapshot,
        state_label=state.state_label,
        conformer_name=conformer.name,
        binding_mode=conformer.binding_mode,
        active_center_atom_uids=active_site.center_atom_uids,
        bound_adsorbate_atom_uids=bound_adsorbate_uids,
        binding_edges=conformer.binding_edges,
        side_labels=ordered_side_labels,
    )


def _validate_build_result_handoff(
    state: AdsorptionState,
    active_site: ActiveSite,
    build_result: AdsorbateBuildResult,
) -> None:
    if state.structure_variant_id != active_site.structure_variant_id:
        raise StateConformerToolingError(
            "AdsorptionState and ActiveSite must belong to the same StructureVariant"
        )
    if state.active_site_id != active_site.id:
        raise StateConformerToolingError(
            "AdsorptionState must reference the supplied ActiveSite"
        )
    if state.adsorbates != (build_result.template_key,):
        raise StateConformerToolingError(
            "Block 7 handoff requires exactly the build-result template in state.adsorbates"
        )
    if build_result.binding_mode_intent not in _ALLOWED_MATERIALIZED_BINDING_MODES:
        raise StateConformerToolingError("unsupported Block 7 binding mode intent")

    validate_active_site_snapshot_compatibility(active_site, build_result.snapshot)
    active_centers = set(active_site.center_atom_uids)
    if any(
        atom_uid not in active_centers
        for atom_uid in build_result.target_center_atom_uids
    ):
        raise StateConformerToolingError(
            "build-result target centers must belong to the supplied ActiveSite"
        )

    added_uids = set(build_result.addition.added_atom_uids)
    target_uids = set(build_result.target_center_atom_uids)
    for contact in build_result.contacts:
        if contact.adsorbate_atom_uid not in added_uids:
            raise StateConformerToolingError(
                "binding adsorbate atom must be newly added by the supplied Block 7 result"
            )
        if contact.site_atom_uid not in target_uids:
            raise StateConformerToolingError(
                "binding site atom must be one of the Block 7 target centers"
            )
        if not build_result.snapshot.contains_atom(contact.adsorbate_atom_uid):
            raise StateConformerToolingError(
                "binding adsorbate atom is absent from the Block 7 result snapshot"
            )
        if not build_result.snapshot.contains_atom(contact.site_atom_uid):
            raise StateConformerToolingError(
                "binding site atom is absent from the Block 7 result snapshot"
            )

    _validate_binding_semantics(build_result)


def _validate_binding_semantics(build_result: AdsorbateBuildResult) -> None:
    targets = build_result.target_center_atom_uids
    target_set = set(targets)
    contacts = build_result.contacts
    contacted_sites = {contact.site_atom_uid for contact in contacts}
    if contacted_sites != target_set:
        raise StateConformerToolingError(
            "Block 7 contacts must cover exactly the target ActiveSite centers"
        )

    mode = build_result.binding_mode_intent
    if mode is BindingMode.SINGLE_CENTER:
        if len(targets) != 1:
            raise StateConformerToolingError(
                "SINGLE_CENTER materialization requires exactly one target center"
            )
        return

    if mode is BindingMode.BRIDGE:
        if len(targets) != 2:
            raise StateConformerToolingError(
                "BRIDGE materialization requires exactly two target centers"
            )
        contacts_by_atom: dict[AtomUid, set[AtomUid]] = {}
        for contact in contacts:
            contacts_by_atom.setdefault(contact.adsorbate_atom_uid, set()).add(
                contact.site_atom_uid
            )
        if not any(sites == target_set for sites in contacts_by_atom.values()):
            raise StateConformerToolingError(
                "BRIDGE materialization requires one adsorbate atom to contact both centers"
            )
        return

    if len(targets) < 2:
        raise StateConformerToolingError(
            "MULTICENTER materialization requires at least two target centers"
        )
    if len({contact.adsorbate_atom_uid for contact in contacts}) < 2:
        raise StateConformerToolingError(
            "MULTICENTER materialization requires at least two adsorbate contact atoms"
        )


def _validate_materialized_edges(
    build_result: AdsorbateBuildResult,
    conformer: StateConformer,
) -> None:
    expected = tuple(
        (contact.adsorbate_atom_uid, contact.site_atom_uid)
        for contact in build_result.contacts
    )
    actual = tuple(
        (edge.adsorbate_atom_uid, edge.site_atom_uid)
        for edge in conformer.binding_edges
    )
    if actual != expected:
        raise StateConformerToolingError(
            "StateConformer BindingEdges must exactly preserve Block 7 contact ordering"
        )


def _validate_parent_lineage(
    state: AdsorptionState,
    build_result: AdsorbateBuildResult,
    parent_conformer: StateConformer,
) -> None:
    if parent_conformer.adsorption_state_id != state.id:
        raise StateConformerToolingError(
            "parent StateConformer must belong to the same AdsorptionState"
        )
    if build_result.snapshot.parent_snapshot_id != parent_conformer.structure_snapshot_id:
        raise StateConformerToolingError(
            "child snapshot lineage must point to the parent conformer's StructureSnapshot"
        )


def _validate_parent_cycles(
    conformers: tuple[StateConformer, ...],
    by_id: dict[object, StateConformer],
) -> None:
    for conformer in conformers:
        seen = {conformer.id}
        parent_id = conformer.parent_conformer_id
        while parent_id is not None:
            if parent_id in seen:
                raise StateConformerToolingError(
                    "StateConformer parent relationships must be acyclic"
                )
            seen.add(parent_id)
            parent = by_id.get(parent_id)
            if parent is None:
                break
            parent_id = parent.parent_conformer_id


def _normalize_text(value: str, field_name: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise StateConformerToolingError(f"{field_name} must not be blank")
    return normalized


def _unique_in_order(values: tuple[AtomUid, ...]) -> tuple[AtomUid, ...]:
    seen: set[AtomUid] = set()
    result: list[AtomUid] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return tuple(result)

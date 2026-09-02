"""Cross-entity integrity checks for electrocatalysis domain objects."""

from __future__ import annotations

from ecatvasp.domain.entities import ActiveSite, AdsorptionState, StateConformer, StructureSnapshot


class DomainIntegrityError(ValueError):
    """Raised when individually valid entities form an invalid scientific relationship."""


def validate_conformer_context(
    *,
    active_site: ActiveSite,
    state: AdsorptionState,
    conformer: StateConformer,
    snapshot: StructureSnapshot,
) -> None:
    """Validate that a conformer, state, active site, and snapshot belong together."""

    if state.structure_variant_id != active_site.structure_variant_id:
        raise DomainIntegrityError("state and active site must belong to the same StructureVariant")
    if state.active_site_id != active_site.id:
        raise DomainIntegrityError(
            "state.active_site_id does not reference the supplied ActiveSite"
        )
    if conformer.adsorption_state_id != state.id:
        raise DomainIntegrityError("conformer does not reference the supplied AdsorptionState")
    if conformer.structure_snapshot_id != snapshot.id:
        raise DomainIntegrityError("conformer does not reference the supplied StructureSnapshot")

    active_centers = set(active_site.center_atom_uids)
    if any(not snapshot.contains_atom(atom_uid) for atom_uid in active_centers):
        raise DomainIntegrityError("an ActiveSite center is absent from the StructureSnapshot")

    for edge in conformer.binding_edges:
        if edge.site_atom_uid not in active_centers:
            raise DomainIntegrityError("binding edge references an atom outside the ActiveSite")
        if not snapshot.contains_atom(edge.site_atom_uid):
            raise DomainIntegrityError(
                "active-site binding atom is absent from the StructureSnapshot"
            )
        if not snapshot.contains_atom(edge.adsorbate_atom_uid):
            raise DomainIntegrityError(
                "adsorbate binding atom is absent from the StructureSnapshot"
            )

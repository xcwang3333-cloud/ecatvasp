"""Structure import, identity, and construction boundary."""

from ecatvasp.structures.identity import (
    AtomIdentityMapping,
    AtomMappingEntry,
    AtomMappingError,
    AtomMappingMethod,
    GeometrySite,
    IdentityPropagationResult,
    propagate_atom_uids_by_index,
    reconcile_reordered_sites,
    reorder_snapshot,
    validate_identity_preserving_revision,
)

__all__ = [
    "AtomIdentityMapping",
    "AtomMappingEntry",
    "AtomMappingError",
    "AtomMappingMethod",
    "GeometrySite",
    "IdentityPropagationResult",
    "propagate_atom_uids_by_index",
    "reconcile_reordered_sites",
    "reorder_snapshot",
    "validate_identity_preserving_revision",
]

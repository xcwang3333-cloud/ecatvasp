"""Structure import, identity, and construction boundary."""

from ecatvasp.structures.graphene import GrapheneBuildSpec, build_graphene
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
from ecatvasp.structures.io import (
    AtomIdentityStatus,
    SelectiveDynamics,
    StructureDocument,
    StructureFormat,
    StructureIOError,
    StructureSourceMetadata,
    export_structure,
    import_structure,
    parse_structure,
    serialize_structure,
)

__all__ = [
    "AtomIdentityMapping",
    "AtomIdentityStatus",
    "AtomMappingEntry",
    "AtomMappingError",
    "AtomMappingMethod",
    "GeometrySite",
    "GrapheneBuildSpec",
    "IdentityPropagationResult",
    "SelectiveDynamics",
    "StructureDocument",
    "StructureFormat",
    "StructureIOError",
    "StructureSourceMetadata",
    "build_graphene",
    "export_structure",
    "import_structure",
    "parse_structure",
    "propagate_atom_uids_by_index",
    "reconcile_reordered_sites",
    "reorder_snapshot",
    "serialize_structure",
    "validate_identity_preserving_revision",
]

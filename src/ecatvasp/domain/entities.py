"""Core electrocatalysis domain entities frozen for ECatVASP schema version 1."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from ecatvasp.domain.ids import new_uuid7
from ecatvasp.domain.value_objects import (
    BindingEdge,
    BindingMode,
    Lattice,
    SideLabel,
    StructureOrigin,
    StructureSite,
    VariantType,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _require_text(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank")


@dataclass(frozen=True, slots=True)
class Project:
    """Top-level scientific research project."""

    name: str
    slug: str
    id: UUID = field(default_factory=new_uuid7)
    schema_version: int = 1
    description: str | None = None
    created_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        _require_text(self.name, "name")
        _require_text(self.slug, "slug")
        if self.schema_version < 1:
            raise ValueError("schema_version must be positive")


@dataclass(frozen=True, slots=True)
class Catalyst:
    """Scientifically meaningful catalyst identity within a project."""

    project_id: UUID
    name: str
    slug: str
    id: UUID = field(default_factory=new_uuid7)
    formula_label: str | None = None
    support_type: str | None = None
    series_key: str | None = None
    series_value: str | int | float | None = None
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.name, "name")
        _require_text(self.slug, "slug")
        if self.series_value is not None and self.series_key is None:
            raise ValueError("series_key is required when series_value is defined")


@dataclass(frozen=True, slots=True)
class StructureVariant:
    """One scientific structure hypothesis for a catalyst."""

    catalyst_id: UUID
    name: str
    variant_type: VariantType
    id: UUID = field(default_factory=new_uuid7)
    parent_variant_id: UUID | None = None
    topology_tags: tuple[str, ...] = ()
    current_structure_snapshot_id: UUID | None = None

    def __post_init__(self) -> None:
        _require_text(self.name, "name")


@dataclass(frozen=True, slots=True)
class StructureSnapshot:
    """Immutable, fully specified atomic geometry with stable atom identities."""

    lattice: Lattice
    sites: tuple[StructureSite, ...]
    id: UUID = field(default_factory=new_uuid7)
    label: str | None = None
    origin: StructureOrigin = StructureOrigin.IMPORTED
    parent_snapshot_id: UUID | None = None
    periodic: tuple[bool, bool, bool] = (True, True, True)

    def __post_init__(self) -> None:
        if not self.sites:
            raise ValueError("a StructureSnapshot must contain at least one site")
        atom_uids = tuple(site.atom_uid for site in self.sites)
        if len(atom_uids) != len(set(atom_uids)):
            raise ValueError("atom_uid values must be unique within a StructureSnapshot")

    def contains_atom(self, atom_uid: UUID) -> bool:
        """Return whether the immutable snapshot contains the requested atom identity."""

        return any(site.atom_uid == atom_uid for site in self.sites)


@dataclass(frozen=True, slots=True)
class ActiveSite:
    """Chemically meaningful one- or multi-center active site."""

    structure_variant_id: UUID
    center_atom_uids: tuple[UUID, ...]
    id: UUID = field(default_factory=new_uuid7)
    topology: str | None = None
    coordination_environment: str | None = None
    side_labels: tuple[SideLabel, ...] = ()

    def __post_init__(self) -> None:
        if not self.center_atom_uids:
            raise ValueError("an ActiveSite requires at least one center atom")
        if len(self.center_atom_uids) != len(set(self.center_atom_uids)):
            raise ValueError("center_atom_uids must be unique")
        center_set = set(self.center_atom_uids)
        labeled_atoms = tuple(label.atom_uid for label in self.side_labels)
        if len(labeled_atoms) != len(set(labeled_atoms)):
            raise ValueError("an active-center atom can have at most one side label")
        if any(atom_uid not in center_set for atom_uid in labeled_atoms):
            raise ValueError("side labels may only reference active-center atoms")

    @property
    def nuclearity(self) -> int:
        """Return the number of explicitly identified active centers."""

        return len(self.center_atom_uids)


@dataclass(frozen=True, slots=True)
class AdsorptionState:
    """Chemical adsorption state independent of any one geometric conformer."""

    structure_variant_id: UUID
    state_label: str
    id: UUID = field(default_factory=new_uuid7)
    active_site_id: UUID | None = None
    adsorbates: tuple[str, ...] = ()
    coverage: float | None = None
    reaction_role: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.state_label, "state_label")
        if self.coverage is not None and self.coverage < 0:
            raise ValueError("coverage must not be negative")


@dataclass(frozen=True, slots=True)
class StateConformer:
    """Concrete adsorption geometry for one AdsorptionState."""

    adsorption_state_id: UUID
    structure_snapshot_id: UUID
    name: str
    id: UUID = field(default_factory=new_uuid7)
    binding_mode: BindingMode = BindingMode.NONE
    binding_edges: tuple[BindingEdge, ...] = ()
    orientation: str | None = None
    parent_conformer_id: UUID | None = None
    rank: int | None = None

    def __post_init__(self) -> None:
        _require_text(self.name, "name")
        if self.rank is not None and self.rank < 1:
            raise ValueError("rank must be positive when defined")
        if self.binding_mode is BindingMode.NONE and self.binding_edges:
            raise ValueError("binding edges require a non-NONE binding mode")
        if self.binding_mode is BindingMode.MULTICENTER:
            centers = {edge.site_atom_uid for edge in self.binding_edges}
            if len(centers) < 2:
                raise ValueError("multicenter binding requires at least two site atoms")

"""Append-only atom addition lineage for immutable structure revisions."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from ecatvasp.domain import (
    AtomUid,
    StructureOrigin,
    StructureSite,
    StructureSnapshot,
    StructureSnapshotId,
)


class StructureAdditionError(ValueError):
    """Raised when an append-only structure addition is invalid."""


class AtomAdditionAction(StrEnum):
    """How one target atom participates in an append-only revision."""

    PRESERVED = "preserved"
    ADDED = "added"


@dataclass(frozen=True, slots=True)
class AtomAdditionLineageEvent:
    """Explicit source-to-target lineage for preserved and newly added atoms."""

    action: AtomAdditionAction
    target_atom_uid: AtomUid
    target_index: int
    target_element: str
    source_atom_uid: AtomUid | None = None
    source_index: int | None = None
    source_element: str | None = None

    def __post_init__(self) -> None:
        if self.target_index < 0:
            raise ValueError("target_index must not be negative")
        if not self.target_element.strip():
            raise ValueError("target_element must not be blank")

        if self.action is AtomAdditionAction.ADDED:
            if (
                self.source_atom_uid is not None
                or self.source_index is not None
                or self.source_element is not None
            ):
                raise ValueError("added atoms must not have source identity fields")
            return

        if (
            self.source_atom_uid is None
            or self.source_index is None
            or self.source_element is None
        ):
            raise ValueError("preserved atoms require complete source identity fields")
        if self.source_index < 0:
            raise ValueError("source_index must not be negative")
        if not self.source_element.strip():
            raise ValueError("source_element must not be blank")
        if self.target_atom_uid != self.source_atom_uid:
            raise ValueError("preserved atoms must keep atom_uid")
        if self.target_element != self.source_element:
            raise ValueError("preserved atoms must keep chemical element")


@dataclass(frozen=True, slots=True)
class StructureAdditionResult:
    """Immutable child snapshot plus complete append-only atom lineage."""

    source_snapshot_id: StructureSnapshotId
    source_atom_count: int
    snapshot: StructureSnapshot
    lineage: tuple[AtomAdditionLineageEvent, ...]

    def __post_init__(self) -> None:
        if self.source_atom_count < 1:
            raise ValueError("source_atom_count must be positive")
        if self.snapshot.parent_snapshot_id != self.source_snapshot_id:
            raise ValueError("addition snapshot must directly reference the source snapshot")
        if self.snapshot.origin is not StructureOrigin.EDITED:
            raise ValueError("addition snapshot origin must be EDITED")
        if len(self.snapshot.sites) <= self.source_atom_count:
            raise ValueError("append-only addition must increase the atom count")
        if len(self.lineage) != len(self.snapshot.sites):
            raise ValueError("addition lineage must cover every target atom exactly once")

        target_indices = tuple(event.target_index for event in self.lineage)
        if target_indices != tuple(range(len(self.snapshot.sites))):
            raise ValueError("addition lineage must follow target snapshot order")

        target_uids = tuple(event.target_atom_uid for event in self.lineage)
        if len(target_uids) != len(set(target_uids)):
            raise ValueError("addition lineage target atom_uids must be unique")

        for event, target_site in zip(self.lineage, self.snapshot.sites, strict=True):
            if event.target_atom_uid != target_site.atom_uid:
                raise ValueError("addition lineage target atom_uid must match the snapshot")
            if event.target_element != target_site.element:
                raise ValueError("addition lineage target element must match the snapshot")

        preserved = self.lineage[: self.source_atom_count]
        added = self.lineage[self.source_atom_count :]
        if any(event.action is not AtomAdditionAction.PRESERVED for event in preserved):
            raise ValueError("source atoms must be preserved at the start of the child snapshot")
        if any(event.action is not AtomAdditionAction.ADDED for event in added):
            raise ValueError("new atoms must be appended after all preserved source atoms")
        for expected_index, event in enumerate(preserved):
            if event.source_index != expected_index:
                raise ValueError("preserved lineage must retain source atom order")

    @property
    def preserved_atom_uids(self) -> tuple[AtomUid, ...]:
        """Return permanent atom identities preserved from the source snapshot."""

        return tuple(
            event.target_atom_uid
            for event in self.lineage
            if event.action is AtomAdditionAction.PRESERVED
        )

    @property
    def added_atom_uids(self) -> tuple[AtomUid, ...]:
        """Return fresh atom identities introduced by the addition revision."""

        return tuple(
            event.target_atom_uid
            for event in self.lineage
            if event.action is AtomAdditionAction.ADDED
        )


def append_structure_sites(
    source: StructureSnapshot,
    added_sites: Sequence[StructureSite],
    *,
    label: str | None = None,
) -> StructureAdditionResult:
    """Append fresh atoms while preserving the source snapshot and atom identities exactly."""

    additions = tuple(added_sites)
    if not additions:
        raise StructureAdditionError("at least one added site is required")
    if label is not None and not label.strip():
        raise StructureAdditionError("label must not be blank when defined")

    source_uids = {site.atom_uid for site in source.sites}
    added_uids = tuple(site.atom_uid for site in additions)
    if len(added_uids) != len(set(added_uids)):
        raise StructureAdditionError("added atom_uids must be unique")
    if any(atom_uid in source_uids for atom_uid in added_uids):
        raise StructureAdditionError("added atoms must use fresh atom_uids")

    target_sites = source.sites + additions
    snapshot = StructureSnapshot(
        lattice=source.lattice,
        sites=target_sites,
        label=label if label is not None else source.label,
        origin=StructureOrigin.EDITED,
        parent_snapshot_id=source.id,
        periodic=source.periodic,
    )

    lineage: list[AtomAdditionLineageEvent] = []
    for index, site in enumerate(source.sites):
        lineage.append(
            AtomAdditionLineageEvent(
                action=AtomAdditionAction.PRESERVED,
                source_atom_uid=site.atom_uid,
                source_index=index,
                source_element=site.element,
                target_atom_uid=site.atom_uid,
                target_index=index,
                target_element=site.element,
            )
        )
    for offset, site in enumerate(additions, start=len(source.sites)):
        lineage.append(
            AtomAdditionLineageEvent(
                action=AtomAdditionAction.ADDED,
                target_atom_uid=site.atom_uid,
                target_index=offset,
                target_element=site.element,
            )
        )

    return StructureAdditionResult(
        source_snapshot_id=source.id,
        source_atom_count=len(source.sites),
        snapshot=snapshot,
        lineage=tuple(lineage),
    )

"""Immutable vacancy and substitutional-dopant mutations for Model Studio."""

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
    new_atom_uid,
)

_ALLOWED_DOPANTS = frozenset({"N", "S", "P"})


class StructureMutationError(ValueError):
    """Raised when a requested structure mutation is scientifically invalid."""


class AtomLineageAction(StrEnum):
    """How one source atom participates in an immutable structure mutation."""

    PRESERVED = "preserved"
    REMOVED = "removed"
    REPLACED = "replaced"


@dataclass(frozen=True, slots=True)
class DopantSubstitution:
    """Replace one carbon atom with a substitutional N, S, or P atom."""

    atom_uid: AtomUid
    dopant: str

    def __post_init__(self) -> None:
        dopant = self.dopant.strip().capitalize()
        if dopant not in _ALLOWED_DOPANTS:
            raise StructureMutationError("dopant must be one of N, S, or P")
        object.__setattr__(self, "dopant", dopant)


@dataclass(frozen=True, slots=True)
class AtomLineageEvent:
    """Explicit source-to-target atom lineage for one structure mutation."""

    action: AtomLineageAction
    source_atom_uid: AtomUid
    source_index: int
    source_element: str
    target_atom_uid: AtomUid | None = None
    target_index: int | None = None
    target_element: str | None = None

    def __post_init__(self) -> None:
        if self.source_index < 0:
            raise ValueError("source_index must not be negative")
        if not self.source_element.strip():
            raise ValueError("source_element must not be blank")
        if self.action is AtomLineageAction.REMOVED:
            if (
                self.target_atom_uid is not None
                or self.target_index is not None
                or self.target_element is not None
            ):
                raise ValueError("removed atoms must not have target identity fields")
            return
        if self.target_atom_uid is None or self.target_index is None or self.target_element is None:
            raise ValueError("preserved/replaced atoms require complete target identity fields")
        if self.target_index < 0:
            raise ValueError("target_index must not be negative")
        if not self.target_element.strip():
            raise ValueError("target_element must not be blank")
        if self.action is AtomLineageAction.PRESERVED:
            if self.target_atom_uid != self.source_atom_uid:
                raise ValueError("preserved atoms must keep atom_uid")
            if self.target_element != self.source_element:
                raise ValueError("preserved atoms must keep chemical element")
        elif self.action is AtomLineageAction.REPLACED:
            if self.target_atom_uid == self.source_atom_uid:
                raise ValueError("replacement atoms must receive a fresh atom_uid")
            if self.target_element == self.source_element:
                raise ValueError("replacement atoms must change chemical element")


@dataclass(frozen=True, slots=True)
class StructureMutationResult:
    """New immutable snapshot plus complete atom-lineage events from its parent."""

    source_snapshot_id: StructureSnapshotId
    snapshot: StructureSnapshot
    lineage: tuple[AtomLineageEvent, ...]

    def __post_init__(self) -> None:
        if self.snapshot.parent_snapshot_id != self.source_snapshot_id:
            raise ValueError("mutation snapshot must directly reference the source snapshot")
        if self.snapshot.origin is not StructureOrigin.EDITED:
            raise ValueError("mutation snapshot origin must be EDITED")
        if not self.lineage:
            raise ValueError("mutation lineage must not be empty")
        source_uids = tuple(event.source_atom_uid for event in self.lineage)
        if len(source_uids) != len(set(source_uids)):
            raise ValueError("mutation lineage source atom_uids must be unique")

        target_events = tuple(
            event for event in self.lineage if event.action is not AtomLineageAction.REMOVED
        )
        target_uids: list[AtomUid] = []
        target_indices: list[int] = []
        for event in target_events:
            assert event.target_atom_uid is not None
            assert event.target_index is not None
            assert event.target_element is not None
            target_uids.append(event.target_atom_uid)
            target_indices.append(event.target_index)

        snapshot_uids = tuple(site.atom_uid for site in self.snapshot.sites)
        if len(target_uids) != len(set(target_uids)):
            raise ValueError("mutation lineage target atom_uids must be unique")
        if set(target_uids) != set(snapshot_uids):
            raise ValueError("mutation lineage must cover every target atom exactly once")
        if set(target_indices) != set(range(len(self.snapshot.sites))):
            raise ValueError("mutation lineage target indices must cover the child snapshot")
        if len(target_indices) != len(set(target_indices)):
            raise ValueError("mutation lineage target indices must be unique")
        for event in target_events:
            assert event.target_atom_uid is not None
            assert event.target_index is not None
            assert event.target_element is not None
            target_site = self.snapshot.sites[event.target_index]
            if target_site.atom_uid != event.target_atom_uid:
                raise ValueError("mutation lineage target index must reference its target atom_uid")
            if target_site.element != event.target_element:
                raise ValueError("mutation lineage target element must match the child snapshot")

    @property
    def removed_atom_uids(self) -> tuple[AtomUid, ...]:
        """Return source atom identities removed by vacancy creation."""

        return tuple(
            event.source_atom_uid
            for event in self.lineage
            if event.action is AtomLineageAction.REMOVED
        )

    @property
    def replacement_pairs(self) -> tuple[tuple[AtomUid, AtomUid], ...]:
        """Return explicit old-to-new atom identity pairs for substitutions."""

        pairs: list[tuple[AtomUid, AtomUid]] = []
        for event in self.lineage:
            if event.action is AtomLineageAction.REPLACED:
                assert event.target_atom_uid is not None
                pairs.append((event.source_atom_uid, event.target_atom_uid))
        return tuple(pairs)


def mutate_structure(
    source: StructureSnapshot,
    *,
    vacancy_atom_uids: Sequence[AtomUid] = (),
    substitutions: Sequence[DopantSubstitution] = (),
    label: str | None = None,
) -> StructureMutationResult:
    """Create a child snapshot containing explicit vacancy and/or C->N/S/P edits.

    Mutations are addressed only by permanent ``atom_uid``. Unedited atoms preserve
    their identities exactly. A vacancy terminates the removed atom's lineage.
    Substitution terminates the carbon atom's lineage and creates a fresh dopant
    atom identity at the same fractional coordinate. Geometry relaxation is not
    performed here.
    """

    if not vacancy_atom_uids and not substitutions:
        raise StructureMutationError("at least one vacancy or substitution is required")
    if label is not None and not label.strip():
        raise StructureMutationError("label must not be blank when defined")

    vacancy_uids = tuple(vacancy_atom_uids)
    if len(vacancy_uids) != len(set(vacancy_uids)):
        raise StructureMutationError("vacancy atom_uids must be unique")

    substitution_uids = tuple(item.atom_uid for item in substitutions)
    if len(substitution_uids) != len(set(substitution_uids)):
        raise StructureMutationError("substitution atom_uids must be unique")
    overlap = set(vacancy_uids) & set(substitution_uids)
    if overlap:
        raise StructureMutationError("an atom cannot be both removed and substituted")

    source_by_uid = {site.atom_uid: site for site in source.sites}
    requested_uids = set(vacancy_uids) | set(substitution_uids)
    missing = requested_uids - set(source_by_uid)
    if missing:
        raise StructureMutationError("all mutation atom_uids must exist in the source snapshot")

    substitution_by_uid = {item.atom_uid: item.dopant for item in substitutions}
    for atom_uid, requested_dopant in substitution_by_uid.items():
        source_site = source_by_uid[atom_uid]
        if source_site.element != "C":
            raise StructureMutationError(
                f"substitution target must be carbon: {source_site.element} -> {requested_dopant}"
            )

    if len(vacancy_uids) >= len(source.sites) and not substitutions:
        raise StructureMutationError("a mutation cannot remove every atom")

    target_sites: list[StructureSite] = []
    lineage: list[AtomLineageEvent] = []
    vacancy_set = set(vacancy_uids)
    for source_index, source_site in enumerate(source.sites):
        if source_site.atom_uid in vacancy_set:
            lineage.append(
                AtomLineageEvent(
                    action=AtomLineageAction.REMOVED,
                    source_atom_uid=source_site.atom_uid,
                    source_index=source_index,
                    source_element=source_site.element,
                )
            )
            continue

        target_index = len(target_sites)
        dopant = substitution_by_uid.get(source_site.atom_uid)
        if dopant is not None:
            replacement_uid = new_atom_uid()
            target_sites.append(
                StructureSite(
                    atom_uid=replacement_uid,
                    element=dopant,
                    fractional_coords=source_site.fractional_coords,
                )
            )
            lineage.append(
                AtomLineageEvent(
                    action=AtomLineageAction.REPLACED,
                    source_atom_uid=source_site.atom_uid,
                    source_index=source_index,
                    source_element=source_site.element,
                    target_atom_uid=replacement_uid,
                    target_index=target_index,
                    target_element=dopant,
                )
            )
            continue

        target_sites.append(source_site)
        lineage.append(
            AtomLineageEvent(
                action=AtomLineageAction.PRESERVED,
                source_atom_uid=source_site.atom_uid,
                source_index=source_index,
                source_element=source_site.element,
                target_atom_uid=source_site.atom_uid,
                target_index=target_index,
                target_element=source_site.element,
            )
        )

    if not target_sites:
        raise StructureMutationError("a mutation cannot produce an empty structure")

    snapshot = StructureSnapshot(
        lattice=source.lattice,
        sites=tuple(target_sites),
        label=label if label is not None else source.label,
        origin=StructureOrigin.EDITED,
        parent_snapshot_id=source.id,
        periodic=source.periodic,
    )
    return StructureMutationResult(
        source_snapshot_id=source.id,
        snapshot=snapshot,
        lineage=tuple(lineage),
    )


def remove_vacancies(
    source: StructureSnapshot,
    atom_uids: Sequence[AtomUid],
    *,
    label: str | None = None,
) -> StructureMutationResult:
    """Create an immutable child snapshot with the selected atoms removed."""

    return mutate_structure(source, vacancy_atom_uids=atom_uids, label=label)


def substitute_dopants(
    source: StructureSnapshot,
    substitutions: Sequence[DopantSubstitution],
    *,
    label: str | None = None,
) -> StructureMutationResult:
    """Create an immutable child snapshot with substitutional N/S/P dopants."""

    return mutate_structure(source, substitutions=substitutions, label=label)

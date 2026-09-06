"""Scientific inventory, provenance, dependency, and freshness read models."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from ecatvasp.domain import (
    ActiveSite,
    AdsorptionState,
    Analysis,
    AnalysisStatus,
    Artifact,
    Calculation,
    CalculationScientificStatus,
    Catalyst,
    ExecutionAttempt,
    MethodFingerprint,
    Project,
    RemoteJob,
    StateConformer,
    StructureSnapshot,
    StructureVariant,
)
from ecatvasp.provenance import (
    DependencyKind,
    DependencyRecord,
    FreshnessEngine,
    FreshnessReason,
    FreshnessResult,
    FreshnessState,
    ProvenanceIntegrityError,
    ProvenanceRecord,
    scientific_hash,
)
from ecatvasp.storage.model import ProjectBundle


class WorkspaceInspectionError(ValueError):
    """Raised when a workspace inspection projection cannot be derived exactly."""


class WorkspaceEntityKind(StrEnum):
    """Stable presentation labels for provenance-capable project entity families."""

    PROJECT = "project"
    CATALYST = "catalyst"
    STRUCTURE_VARIANT = "structure_variant"
    STRUCTURE_SNAPSHOT = "structure_snapshot"
    ACTIVE_SITE = "active_site"
    ADSORPTION_STATE = "adsorption_state"
    STATE_CONFORMER = "state_conformer"
    METHOD_FINGERPRINT = "method_fingerprint"
    CALCULATION = "calculation"
    EXECUTION_ATTEMPT = "execution_attempt"
    REMOTE_JOB = "remote_job"
    ARTIFACT = "artifact"
    ANALYSIS = "analysis"


class WorkspaceStatusDomain(StrEnum):
    """Lifecycle namespaces that must remain distinct in presentation layers."""

    CALCULATION_SCIENTIFIC = "calculation_scientific"
    ANALYSIS = "analysis"
    EXECUTION_ATTEMPT = "execution_attempt"
    SCHEDULER = "scheduler"


@dataclass(frozen=True, slots=True)
class WorkspaceProvenanceView:
    """Exact source provenance metadata attached to one scientific entity."""

    provenance_id: UUID
    subject_id: UUID
    tool: str
    tool_version: str
    parameters_hash: str | None
    method_fingerprint_id: UUID | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class WorkspaceDependencyView:
    """Exact persisted dependency edge exposed without reinterpretation."""

    dependency_id: UUID
    upstream_id: UUID
    downstream_id: UUID
    kind: DependencyKind
    role: str
    recorded_hash: str


@dataclass(frozen=True, slots=True)
class WorkspaceFreshnessReasonView:
    """Machine-readable freshness reason with exact upstream/dependency references."""

    code: str
    upstream_id: UUID | None
    dependency_id: UUID | None


@dataclass(frozen=True, slots=True)
class WorkspaceFreshnessView:
    """Presentation form of one authoritative FreshnessEngine decision."""

    state: FreshnessState
    reasons: tuple[WorkspaceFreshnessReasonView, ...] = ()


@dataclass(frozen=True, slots=True)
class WorkspaceInventoryRow:
    """One source-linked project entity row for inspection clients."""

    entity_id: UUID
    entity_kind: WorkspaceEntityKind
    display_label: str
    status_domain: WorkspaceStatusDomain | None
    status: str | None
    current_scientific_hash: str | None
    provenance: tuple[WorkspaceProvenanceView, ...]
    incoming_dependencies: tuple[WorkspaceDependencyView, ...]
    outgoing_dependencies: tuple[WorkspaceDependencyView, ...]
    scientific_ancestor_ids: tuple[UUID, ...]
    freshness: WorkspaceFreshnessView
    attention_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WorkspaceScientificInventory:
    """Deterministic inspection projection over one validated ProjectBundle."""

    project_id: UUID
    rows: tuple[WorkspaceInventoryRow, ...]
    dependencies: tuple[WorkspaceDependencyView, ...]

    def row(self, entity_id: UUID) -> WorkspaceInventoryRow:
        """Return one inventory row by exact entity id."""

        for item in self.rows:
            if item.entity_id == entity_id:
                return item
        raise KeyError(entity_id)


def build_scientific_inventory(
    bundle: ProjectBundle,
    *,
    observed_hashes: Mapping[UUID, str] | None = None,
    invalid_ids: set[UUID] | None = None,
    superseded_ids: set[UUID] | None = None,
) -> WorkspaceScientificInventory:
    """Build source-linked inventory rows using the existing provenance/freshness authority."""

    bundle.validate()
    entities = bundle.provenance_entities()
    entity_by_id = {_entity_id(entity): entity for entity in entities}
    node_ids = set(entity_by_id)

    hashes = _current_scientific_hashes(entities)
    if observed_hashes is not None:
        unknown = set(observed_hashes) - node_ids
        if unknown:
            raise WorkspaceInspectionError(
                "observed scientific hash references an entity outside the project inventory"
            )
        hashes.update(
            {entity_id: _normalize_sha256(value) for entity_id, value in observed_hashes.items()}
        )

    inferred_invalid = {
        entity.id
        for entity in entities
        if (
            isinstance(entity, Calculation)
            and entity.status is CalculationScientificStatus.INVALID
        )
        or (isinstance(entity, Analysis) and entity.status is AnalysisStatus.INVALID)
    }
    explicit_invalid = set() if invalid_ids is None else set(invalid_ids)
    explicit_superseded = set() if superseded_ids is None else set(superseded_ids)

    try:
        freshness_by_id = FreshnessEngine(bundle.dependency_records).evaluate(
            node_ids=node_ids,
            current_hashes=hashes,
            invalid_ids=inferred_invalid | explicit_invalid,
            superseded_ids=explicit_superseded,
        )
    except ProvenanceIntegrityError as error:
        raise WorkspaceInspectionError(str(error)) from error

    provenance_by_subject = _provenance_index(bundle.provenance_records)
    incoming, outgoing = _dependency_indexes(bundle.dependency_records)
    dependency_views = tuple(
        _dependency_view(record)
        for record in sorted(bundle.dependency_records, key=lambda item: str(item.id))
    )

    rows = tuple(
        _inventory_row(
            entity=entity,
            current_hash=hashes.get(entity_id),
            provenance=provenance_by_subject.get(entity_id, ()),
            incoming=incoming.get(entity_id, ()),
            outgoing=outgoing.get(entity_id, ()),
            scientific_ancestor_ids=_scientific_ancestor_ids(
                entity_id=entity_id,
                dependencies=bundle.dependency_records,
            ),
            freshness=freshness_by_id[entity_id],
        )
        for entity_id, entity in sorted(
            entity_by_id.items(),
            key=lambda item: (_entity_kind(item[1]).value, str(item[0])),
        )
    )
    return WorkspaceScientificInventory(
        project_id=bundle.project.id,
        rows=rows,
        dependencies=dependency_views,
    )


def _inventory_row(
    *,
    entity: object,
    current_hash: str | None,
    provenance: tuple[ProvenanceRecord, ...],
    incoming: tuple[DependencyRecord, ...],
    outgoing: tuple[DependencyRecord, ...],
    scientific_ancestor_ids: tuple[UUID, ...],
    freshness: FreshnessResult,
) -> WorkspaceInventoryRow:
    entity_id = _entity_id(entity)
    status_domain, status = _status(entity)
    return WorkspaceInventoryRow(
        entity_id=entity_id,
        entity_kind=_entity_kind(entity),
        display_label=_display_label(entity),
        status_domain=status_domain,
        status=status,
        current_scientific_hash=current_hash,
        provenance=tuple(_provenance_view(item) for item in provenance),
        incoming_dependencies=tuple(_dependency_view(item) for item in incoming),
        outgoing_dependencies=tuple(_dependency_view(item) for item in outgoing),
        scientific_ancestor_ids=scientific_ancestor_ids,
        freshness=_freshness_view(freshness),
        attention_codes=_attention_codes(entity, freshness),
    )


def _current_scientific_hashes(entities: tuple[object, ...]) -> dict[UUID, str]:
    hashes: dict[UUID, str] = {}
    for entity in entities:
        try:
            value = scientific_hash(entity)  # type: ignore[arg-type]
        except (TypeError, ProvenanceIntegrityError):
            continue
        hashes[_entity_id(entity)] = value
    return hashes


def _provenance_index(
    records: tuple[ProvenanceRecord, ...],
) -> dict[UUID, tuple[ProvenanceRecord, ...]]:
    result: dict[UUID, list[ProvenanceRecord]] = {}
    for record in records:
        result.setdefault(record.subject_id, []).append(record)
    return {
        subject_id: tuple(sorted(items, key=lambda item: (item.created_at, str(item.id))))
        for subject_id, items in result.items()
    }


def _dependency_indexes(
    records: tuple[DependencyRecord, ...],
) -> tuple[
    dict[UUID, tuple[DependencyRecord, ...]],
    dict[UUID, tuple[DependencyRecord, ...]],
]:
    incoming_mutable: dict[UUID, list[DependencyRecord]] = {}
    outgoing_mutable: dict[UUID, list[DependencyRecord]] = {}
    for record in records:
        incoming_mutable.setdefault(record.downstream_id, []).append(record)
        outgoing_mutable.setdefault(record.upstream_id, []).append(record)
    incoming = {
        entity_id: tuple(sorted(items, key=lambda item: str(item.id)))
        for entity_id, items in incoming_mutable.items()
    }
    outgoing = {
        entity_id: tuple(sorted(items, key=lambda item: str(item.id)))
        for entity_id, items in outgoing_mutable.items()
    }
    return incoming, outgoing


def _scientific_ancestor_ids(
    *,
    entity_id: UUID,
    dependencies: tuple[DependencyRecord, ...],
) -> tuple[UUID, ...]:
    incoming: dict[UUID, list[UUID]] = {}
    for record in dependencies:
        if record.kind is DependencyKind.SCIENTIFIC:
            incoming.setdefault(record.downstream_id, []).append(record.upstream_id)

    ancestors: set[UUID] = set()
    stack = list(sorted(incoming.get(entity_id, ()), key=str, reverse=True))
    while stack:
        upstream_id = stack.pop()
        if upstream_id in ancestors:
            continue
        ancestors.add(upstream_id)
        stack.extend(sorted(incoming.get(upstream_id, ()), key=str, reverse=True))
    return tuple(sorted(ancestors, key=str))


def _provenance_view(record: ProvenanceRecord) -> WorkspaceProvenanceView:
    return WorkspaceProvenanceView(
        provenance_id=record.id,
        subject_id=record.subject_id,
        tool=record.tool,
        tool_version=record.tool_version,
        parameters_hash=record.parameters_hash,
        method_fingerprint_id=record.method_fingerprint_id,
        created_at=record.created_at,
    )


def _dependency_view(record: DependencyRecord) -> WorkspaceDependencyView:
    return WorkspaceDependencyView(
        dependency_id=record.id,
        upstream_id=record.upstream_id,
        downstream_id=record.downstream_id,
        kind=record.kind,
        role=record.role,
        recorded_hash=record.recorded_hash,
    )


def _freshness_view(result: FreshnessResult) -> WorkspaceFreshnessView:
    return WorkspaceFreshnessView(
        state=result.state,
        reasons=tuple(_freshness_reason_view(reason) for reason in result.reasons),
    )


def _freshness_reason_view(reason: FreshnessReason) -> WorkspaceFreshnessReasonView:
    return WorkspaceFreshnessReasonView(
        code=reason.code,
        upstream_id=reason.upstream_id,
        dependency_id=reason.dependency_id,
    )


def _status(entity: object) -> tuple[WorkspaceStatusDomain | None, str | None]:
    if isinstance(entity, Calculation):
        return WorkspaceStatusDomain.CALCULATION_SCIENTIFIC, entity.status.value
    if isinstance(entity, Analysis):
        return WorkspaceStatusDomain.ANALYSIS, entity.status.value
    if isinstance(entity, ExecutionAttempt):
        return WorkspaceStatusDomain.EXECUTION_ATTEMPT, entity.status.value
    if isinstance(entity, RemoteJob):
        return WorkspaceStatusDomain.SCHEDULER, entity.state.value
    return None, None


def _attention_codes(entity: object, freshness: FreshnessResult) -> tuple[str, ...]:
    codes: list[str] = []
    if isinstance(entity, Calculation):
        if entity.status is CalculationScientificStatus.BLOCKED:
            codes.append("calculation_status_blocked")
        elif entity.status is CalculationScientificStatus.STALE:
            codes.append("calculation_status_stale")
        elif entity.status is CalculationScientificStatus.INVALID:
            codes.append("calculation_status_invalid")
    elif isinstance(entity, Analysis):
        if entity.status is AnalysisStatus.BLOCKED:
            codes.append("analysis_status_blocked")
        elif entity.status is AnalysisStatus.STALE:
            codes.append("analysis_status_stale")
        elif entity.status is AnalysisStatus.INVALID:
            codes.append("analysis_status_invalid")
    if freshness.state is not FreshnessState.FRESH:
        codes.append(f"freshness_{freshness.state.value}")
    return tuple(codes)


def _entity_kind(entity: object) -> WorkspaceEntityKind:
    if isinstance(entity, Project):
        return WorkspaceEntityKind.PROJECT
    if isinstance(entity, Catalyst):
        return WorkspaceEntityKind.CATALYST
    if isinstance(entity, StructureVariant):
        return WorkspaceEntityKind.STRUCTURE_VARIANT
    if isinstance(entity, StructureSnapshot):
        return WorkspaceEntityKind.STRUCTURE_SNAPSHOT
    if isinstance(entity, ActiveSite):
        return WorkspaceEntityKind.ACTIVE_SITE
    if isinstance(entity, AdsorptionState):
        return WorkspaceEntityKind.ADSORPTION_STATE
    if isinstance(entity, StateConformer):
        return WorkspaceEntityKind.STATE_CONFORMER
    if isinstance(entity, MethodFingerprint):
        return WorkspaceEntityKind.METHOD_FINGERPRINT
    if isinstance(entity, Calculation):
        return WorkspaceEntityKind.CALCULATION
    if isinstance(entity, ExecutionAttempt):
        return WorkspaceEntityKind.EXECUTION_ATTEMPT
    if isinstance(entity, RemoteJob):
        return WorkspaceEntityKind.REMOTE_JOB
    if isinstance(entity, Artifact):
        return WorkspaceEntityKind.ARTIFACT
    if isinstance(entity, Analysis):
        return WorkspaceEntityKind.ANALYSIS
    raise WorkspaceInspectionError(
        f"unsupported provenance-capable entity type: {type(entity).__name__}"
    )


def _display_label(entity: object) -> str:
    if isinstance(entity, (Project, Catalyst, StructureVariant, StateConformer)):
        return entity.name
    if isinstance(entity, StructureSnapshot):
        return entity.label or "structure snapshot"
    if isinstance(entity, ActiveSite):
        return entity.topology or f"active site ({entity.nuclearity} center)"
    if isinstance(entity, AdsorptionState):
        return entity.state_label
    if isinstance(entity, MethodFingerprint):
        return f"method {entity.instance_hash[:12]}"
    if isinstance(entity, Calculation):
        return entity.slug or entity.calculation_type.value
    if isinstance(entity, ExecutionAttempt):
        return f"attempt {entity.attempt_number}"
    if isinstance(entity, RemoteJob):
        return f"{entity.scheduler.value}:{entity.scheduler_job_id}"
    if isinstance(entity, Artifact):
        return entity.artifact_type.value
    if isinstance(entity, Analysis):
        return entity.analysis_type.value
    raise WorkspaceInspectionError(
        f"unsupported provenance-capable entity type: {type(entity).__name__}"
    )


def _entity_id(entity: object) -> UUID:
    value = getattr(entity, "id", None)
    if not isinstance(value, UUID):
        raise WorkspaceInspectionError("workspace inventory entities must expose UUID ids")
    return value


def _normalize_sha256(value: str) -> str:
    normalized = value.lower()
    if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
        raise WorkspaceInspectionError(
            "observed scientific hash must be a 64-character hexadecimal SHA-256 digest"
        )
    return normalized

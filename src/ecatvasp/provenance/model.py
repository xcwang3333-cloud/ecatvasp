"""Provenance, dependency, and freshness semantics for ECatVASP."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import NewType
from uuid import UUID

from ecatvasp.domain import (
    ActiveSite,
    AdsorptionState,
    Analysis,
    Artifact,
    Calculation,
    MethodFingerprint,
    StateConformer,
    StructureSnapshot,
    StructureVariant,
    canonical_sha256,
)
from ecatvasp.domain.ids import MethodFingerprintId, new_uuid7

DependencyRecordId = NewType("DependencyRecordId", UUID)
ProvenanceRecordId = NewType("ProvenanceRecordId", UUID)


def new_dependency_record_id() -> DependencyRecordId:
    return DependencyRecordId(new_uuid7())


def new_provenance_record_id() -> ProvenanceRecordId:
    return ProvenanceRecordId(new_uuid7())


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _require_text(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank")


def _normalized_sha256(value: str, field_name: str) -> str:
    normalized = value.lower()
    valid_hex = all(character in "0123456789abcdef" for character in normalized)
    if len(normalized) != 64 or not valid_hex:
        raise ValueError(f"{field_name} must be a 64-character hexadecimal SHA-256 digest")
    return normalized


class DependencyKind(StrEnum):
    """Dependency classes with different freshness-propagation semantics."""

    SCIENTIFIC = "scientific"
    ORGANIZATIONAL = "organizational"
    DISPLAY = "display"
    EXECUTION = "execution"


class FreshnessState(StrEnum):
    """Scientific validity of an immutable result relative to current upstream inputs."""

    FRESH = "fresh"
    STALE = "stale"
    INVALID = "invalid"
    SUPERSEDED = "superseded"


@dataclass(frozen=True, slots=True)
class DependencyRecord:
    """Snapshot of one upstream dependency when a downstream object was produced."""

    upstream_id: UUID
    downstream_id: UUID
    kind: DependencyKind
    role: str
    recorded_hash: str
    id: DependencyRecordId = field(default_factory=new_dependency_record_id)

    def __post_init__(self) -> None:
        if self.upstream_id == self.downstream_id:
            raise ValueError(
                "a dependency cannot reference the same upstream and downstream object"
            )
        _require_text(self.role, "role")
        object.__setattr__(
            self,
            "recorded_hash",
            _normalized_sha256(self.recorded_hash, "recorded_hash"),
        )


@dataclass(frozen=True, slots=True)
class ProvenanceRecord:
    """Audit record describing how one immutable scientific object was produced."""

    subject_id: UUID
    tool: str
    tool_version: str
    id: ProvenanceRecordId = field(default_factory=new_provenance_record_id)
    parameters_hash: str | None = None
    method_fingerprint_id: MethodFingerprintId | None = None
    created_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        _require_text(self.tool, "tool")
        _require_text(self.tool_version, "tool_version")
        if self.parameters_hash is not None:
            object.__setattr__(
                self,
                "parameters_hash",
                _normalized_sha256(self.parameters_hash, "parameters_hash"),
            )
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")


@dataclass(frozen=True, slots=True)
class FreshnessReason:
    """Machine-readable reason for a non-fresh freshness decision."""

    code: str
    upstream_id: UUID | None = None
    dependency_id: DependencyRecordId | None = None

    def __post_init__(self) -> None:
        _require_text(self.code, "code")


@dataclass(frozen=True, slots=True)
class FreshnessResult:
    """Freshness decision for one current project entity."""

    subject_id: UUID
    state: FreshnessState
    reasons: tuple[FreshnessReason, ...] = ()


class ProvenanceIntegrityError(ValueError):
    """Raised when dependency/provenance records cannot form a valid graph."""


def scientific_hash(
    value: StructureVariant
    | StructureSnapshot
    | ActiveSite
    | AdsorptionState
    | StateConformer
    | MethodFingerprint
    | Calculation
    | Artifact
    | Analysis,
) -> str:
    """Return a hash of scientific content, excluding lifecycle/display metadata."""

    if isinstance(value, MethodFingerprint):
        return value.instance_hash
    if isinstance(value, Artifact):
        if value.sha256 is None:
            raise ProvenanceIntegrityError(
                "Artifact requires a content SHA-256 before it can be a scientific dependency"
            )
        return _normalized_sha256(value.sha256, "Artifact.sha256")
    if isinstance(value, StructureSnapshot):
        return canonical_sha256(
            {
                "lattice": value.lattice,
                "sites": value.sites,
                "periodic": value.periodic,
            }
        )
    if isinstance(value, StructureVariant):
        return canonical_sha256(
            {
                "catalyst_id": value.catalyst_id,
                "variant_type": value.variant_type,
                "parent_variant_id": value.parent_variant_id,
                "topology_tags": value.topology_tags,
                "current_structure_snapshot_id": value.current_structure_snapshot_id,
            }
        )
    if isinstance(value, ActiveSite):
        return canonical_sha256(
            {
                "structure_variant_id": value.structure_variant_id,
                "center_atom_uids": value.center_atom_uids,
                "topology": value.topology,
                "coordination_environment": value.coordination_environment,
                "side_labels": value.side_labels,
            }
        )
    if isinstance(value, AdsorptionState):
        return canonical_sha256(
            {
                "structure_variant_id": value.structure_variant_id,
                "state_label": value.state_label,
                "active_site_id": value.active_site_id,
                "adsorbates": value.adsorbates,
                "coverage": value.coverage,
                "reaction_role": value.reaction_role,
            }
        )
    if isinstance(value, StateConformer):
        return canonical_sha256(
            {
                "adsorption_state_id": value.adsorption_state_id,
                "structure_snapshot_id": value.structure_snapshot_id,
                "binding_mode": value.binding_mode,
                "binding_edges": value.binding_edges,
                "orientation": value.orientation,
            }
        )
    if isinstance(value, Calculation):
        return canonical_sha256(
            {
                "calculation_type": value.calculation_type,
                "input_structure_snapshot_id": value.input_structure_snapshot_id,
                "recipe_id": value.recipe_id,
                "method_fingerprint_id": value.method_fingerprint_id,
                "engine": value.engine,
            }
        )
    if isinstance(value, Analysis):
        return canonical_sha256(
            {
                "analysis_type": value.analysis_type,
                "input_artifact_ids": value.input_artifact_ids,
                "tool": value.tool,
                "tool_version": value.tool_version,
                "parameters_hash": value.parameters_hash,
            }
        )
    raise TypeError(f"unsupported scientific hash type: {type(value).__name__}")


class DependencyGraph:
    """Validated immutable dependency graph used by the freshness engine."""

    def __init__(self, records: tuple[DependencyRecord, ...]) -> None:
        self.records = records
        self._validate()

    def _validate(self) -> None:
        ids = [record.id for record in self.records]
        if len(ids) != len(set(ids)):
            raise ProvenanceIntegrityError("DependencyRecord ids must be unique")

        semantic_keys = [
            (record.upstream_id, record.downstream_id, record.kind, record.role)
            for record in self.records
        ]
        if len(semantic_keys) != len(set(semantic_keys)):
            raise ProvenanceIntegrityError("duplicate dependency semantics are not allowed")

        adjacency: dict[UUID, set[UUID]] = defaultdict(set)
        indegree: dict[UUID, int] = defaultdict(int)
        nodes: set[UUID] = set()
        for record in self.records:
            nodes.add(record.upstream_id)
            nodes.add(record.downstream_id)
            if record.downstream_id not in adjacency[record.upstream_id]:
                adjacency[record.upstream_id].add(record.downstream_id)
                indegree[record.downstream_id] += 1
                indegree.setdefault(record.upstream_id, 0)

        queue = deque(node for node in nodes if indegree[node] == 0)
        visited = 0
        while queue:
            node = queue.popleft()
            visited += 1
            for downstream in adjacency[node]:
                indegree[downstream] -= 1
                if indegree[downstream] == 0:
                    queue.append(downstream)
        if visited != len(nodes):
            raise ProvenanceIntegrityError("provenance dependency records must form a DAG")

    def topological_nodes(self, node_ids: set[UUID]) -> tuple[UUID, ...]:
        """Return all requested nodes in dependency order, including isolated nodes."""

        adjacency: dict[UUID, set[UUID]] = defaultdict(set)
        indegree = {node_id: 0 for node_id in node_ids}
        for record in self.records:
            if record.upstream_id not in node_ids or record.downstream_id not in node_ids:
                raise ProvenanceIntegrityError(
                    "dependency references an entity outside the evaluation set"
                )
            if record.downstream_id not in adjacency[record.upstream_id]:
                adjacency[record.upstream_id].add(record.downstream_id)
                indegree[record.downstream_id] += 1

        queue = deque(sorted((node for node, degree in indegree.items() if degree == 0), key=str))
        ordered: list[UUID] = []
        while queue:
            node = queue.popleft()
            ordered.append(node)
            for downstream in sorted(adjacency[node], key=str):
                indegree[downstream] -= 1
                if indegree[downstream] == 0:
                    queue.append(downstream)
        if len(ordered) != len(node_ids):
            raise ProvenanceIntegrityError("evaluation node set contains a dependency cycle")
        return tuple(ordered)


class FreshnessEngine:
    """Evaluate local freshness without mutating immutable domain objects."""

    def __init__(self, dependencies: tuple[DependencyRecord, ...]) -> None:
        self.graph = DependencyGraph(dependencies)

    def evaluate(
        self,
        *,
        node_ids: set[UUID],
        current_hashes: dict[UUID, str],
        invalid_ids: set[UUID] | None = None,
        superseded_ids: set[UUID] | None = None,
    ) -> dict[UUID, FreshnessResult]:
        """Evaluate direct hash changes and propagate only scientific invalidation."""

        invalid = set() if invalid_ids is None else set(invalid_ids)
        superseded = set() if superseded_ids is None else set(superseded_ids)
        unknown_overrides = (invalid | superseded) - node_ids
        if unknown_overrides:
            raise ProvenanceIntegrityError("freshness override references an unknown entity")

        ordered = self.graph.topological_nodes(node_ids)
        state = {node_id: FreshnessState.FRESH for node_id in node_ids}
        reasons: dict[UUID, list[FreshnessReason]] = defaultdict(list)

        for node_id in superseded:
            state[node_id] = FreshnessState.SUPERSEDED
            reasons[node_id].append(FreshnessReason(code="explicitly_superseded"))
        for node_id in invalid:
            state[node_id] = FreshnessState.INVALID
            reasons[node_id].append(FreshnessReason(code="explicitly_invalid"))

        incoming: dict[UUID, list[DependencyRecord]] = defaultdict(list)
        for record in self.graph.records:
            incoming[record.downstream_id].append(record)

        for downstream_id in ordered:
            for record in incoming[downstream_id]:
                if record.kind is not DependencyKind.SCIENTIFIC:
                    continue

                upstream_state = state[record.upstream_id]
                if upstream_state is FreshnessState.INVALID:
                    self._promote(
                        downstream_id,
                        FreshnessState.INVALID,
                        FreshnessReason(
                            code="upstream_invalid",
                            upstream_id=record.upstream_id,
                            dependency_id=record.id,
                        ),
                        state,
                        reasons,
                    )
                    continue
                if upstream_state is FreshnessState.STALE:
                    self._promote(
                        downstream_id,
                        FreshnessState.STALE,
                        FreshnessReason(
                            code="upstream_stale",
                            upstream_id=record.upstream_id,
                            dependency_id=record.id,
                        ),
                        state,
                        reasons,
                    )

                current_hash = current_hashes.get(record.upstream_id)
                if current_hash is None:
                    self._promote(
                        downstream_id,
                        FreshnessState.INVALID,
                        FreshnessReason(
                            code="scientific_hash_missing",
                            upstream_id=record.upstream_id,
                            dependency_id=record.id,
                        ),
                        state,
                        reasons,
                    )
                    continue
                normalized = _normalized_sha256(current_hash, "current scientific hash")
                if normalized != record.recorded_hash:
                    self._promote(
                        downstream_id,
                        FreshnessState.STALE,
                        FreshnessReason(
                            code="scientific_hash_changed",
                            upstream_id=record.upstream_id,
                            dependency_id=record.id,
                        ),
                        state,
                        reasons,
                    )

        return {
            node_id: FreshnessResult(
                subject_id=node_id,
                state=state[node_id],
                reasons=tuple(reasons[node_id]),
            )
            for node_id in sorted(node_ids, key=str)
        }

    @staticmethod
    def _promote(
        subject_id: UUID,
        candidate: FreshnessState,
        reason: FreshnessReason,
        state: dict[UUID, FreshnessState],
        reasons: dict[UUID, list[FreshnessReason]],
    ) -> None:
        precedence = {
            FreshnessState.FRESH: 0,
            FreshnessState.SUPERSEDED: 1,
            FreshnessState.STALE: 2,
            FreshnessState.INVALID: 3,
        }
        if precedence[candidate] > precedence[state[subject_id]]:
            state[subject_id] = candidate
        if reason not in reasons[subject_id]:
            reasons[subject_id].append(reason)

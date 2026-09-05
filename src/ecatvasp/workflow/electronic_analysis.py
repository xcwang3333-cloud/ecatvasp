"""Pure electronic-analysis reconciliation on top of v0.6 workflow gates.

This module derives current analysis readiness from immutable project facts. It deliberately does
not persist another workflow plan, generation, or lifecycle state machine.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID

from ecatvasp.domain import (
    Analysis,
    AnalysisProducerRef,
    AnalysisStatus,
    AnalysisType,
    Artifact,
    ArtifactAvailability,
    canonical_sha256,
)
from ecatvasp.domain.ids import AnalysisId, ArtifactId, CalculationId, ProjectId
from ecatvasp.provenance import (
    DependencyKind,
    DependencyRecord,
    FreshnessEngine,
    FreshnessReason,
    FreshnessResult,
    FreshnessState,
    ProvenanceIntegrityError,
    scientific_hash,
)
from ecatvasp.storage import ProjectBundle, ProjectStore
from ecatvasp.workflow.gates import (
    WorkflowScientificGateEvaluation,
    WorkflowStepReadiness,
    WorkflowStepScientificState,
)

ELECTRONIC_ANALYSIS_TYPES = frozenset(
    {
        AnalysisType.DOS,
        AnalysisType.PDOS,
        AnalysisType.BADER,
        AnalysisType.CHARGE_DIFFERENCE,
        AnalysisType.COHP,
        AnalysisType.BAND_CENTER,
    }
)


class ElectronicAnalysisReconciliationError(ValueError):
    """Raised when electronic-analysis readiness cannot be derived exactly."""


class ElectronicAnalysisScientificState(StrEnum):
    """Derived scientific state for one exact electronic-analysis requirement."""

    UNMATERIALIZED = "unmaterialized"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"
    STALE = "stale"
    INVALID = "invalid"
    SUPERSEDED = "superseded"


@dataclass(frozen=True, slots=True)
class ElectronicWorkflowAnchor:
    """Exact v0.6 current-generation identity expected by an analysis requirement."""

    step_key: str
    calculation_id: CalculationId

    def __post_init__(self) -> None:
        if not self.step_key.strip():
            raise ElectronicAnalysisReconciliationError(
                "electronic workflow anchor requires a non-blank step_key"
            )


@dataclass(frozen=True, slots=True)
class ElectronicAnalysisRequirement:
    """Ephemeral desired Analysis identity; never persisted as another workflow object."""

    key: str
    project_id: ProjectId
    analysis_type: AnalysisType
    input_artifact_ids: tuple[ArtifactId, ...]
    parameters_hash: str
    workflow_anchor: ElectronicWorkflowAnchor | None = None

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise ElectronicAnalysisReconciliationError(
                "electronic analysis requirement requires a non-blank key"
            )
        if self.analysis_type not in ELECTRONIC_ANALYSIS_TYPES:
            raise ElectronicAnalysisReconciliationError(
                "electronic analysis requirement uses a non-electronic AnalysisType"
            )
        if not self.input_artifact_ids:
            raise ElectronicAnalysisReconciliationError(
                "electronic analysis requirement needs at least one exact input Artifact"
            )
        if len(self.input_artifact_ids) != len(set(self.input_artifact_ids)):
            raise ElectronicAnalysisReconciliationError(
                "electronic analysis requirement input Artifact ids must be unique"
            )
        object.__setattr__(
            self,
            "parameters_hash",
            _normalized_sha256(self.parameters_hash, "parameters_hash"),
        )


@dataclass(frozen=True, slots=True)
class ElectronicAnalysisProjection:
    """Current derived state/readiness for one exact requirement."""

    key: str
    analysis_type: AnalysisType
    scientific_state: ElectronicAnalysisScientificState
    readiness: WorkflowStepReadiness
    input_artifact_ids: tuple[ArtifactId, ...]
    analysis_id: AnalysisId | None = None
    output_artifact_ids: tuple[ArtifactId, ...] = ()
    freshness_state: FreshnessState | None = None
    workflow_step_key: str | None = None
    workflow_calculation_id: CalculationId | None = None
    reason_codes: tuple[str, ...] = ()
    projection_hash: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "projection_hash",
            canonical_sha256(
                {
                    "key": self.key,
                    "analysis_type": self.analysis_type,
                    "scientific_state": self.scientific_state,
                    "readiness": self.readiness,
                    "input_artifact_ids": self.input_artifact_ids,
                    "analysis_id": self.analysis_id,
                    "output_artifact_ids": self.output_artifact_ids,
                    "freshness_state": self.freshness_state,
                    "workflow_step_key": self.workflow_step_key,
                    "workflow_calculation_id": self.workflow_calculation_id,
                    "reason_codes": self.reason_codes,
                }
            ),
        )


@dataclass(frozen=True, slots=True)
class ElectronicAnalysisReconciliationReport:
    """Deterministic pure projection for a set of electronic-analysis requirements."""

    projections: tuple[ElectronicAnalysisProjection, ...]
    report_hash: str = field(init=False)

    def __post_init__(self) -> None:
        keys = tuple(item.key for item in self.projections)
        if len(keys) != len(set(keys)):
            raise ElectronicAnalysisReconciliationError(
                "electronic reconciliation projection keys must be unique"
            )
        object.__setattr__(
            self,
            "report_hash",
            canonical_sha256(
                {
                    "projection_hashes": tuple(
                        item.projection_hash for item in self.projections
                    )
                }
            ),
        )

    def requirement(self, key: str) -> ElectronicAnalysisProjection:
        """Resolve one requirement projection by its stable ephemeral key."""

        for item in self.projections:
            if item.key == key:
                return item
        raise KeyError(key)


def reconcile_electronic_analyses(
    *,
    requirements: tuple[ElectronicAnalysisRequirement, ...],
    analyses: tuple[Analysis, ...],
    artifacts: tuple[Artifact, ...],
    dependencies: tuple[DependencyRecord, ...],
    current_hashes: Mapping[UUID, str] | None = None,
    workflow_gates: WorkflowScientificGateEvaluation | None = None,
    invalid_ids: set[UUID] | None = None,
    superseded_ids: set[UUID] | None = None,
) -> ElectronicAnalysisReconciliationReport:
    """Derive exact electronic-analysis readiness without mutating persisted state."""

    _validate_requirement_keys(requirements)
    analysis_by_id = _analysis_index(analyses)
    artifact_by_id = _artifact_index(artifacts)
    _validate_requirement_inputs(requirements, artifact_by_id)

    output_by_analysis: dict[AnalysisId, list[Artifact]] = {}
    for artifact in artifacts:
        if not isinstance(artifact.producer, AnalysisProducerRef):
            continue
        if artifact.producer.id not in analysis_by_id:
            raise ElectronicAnalysisReconciliationError(
                "analysis-produced Artifact references a missing Analysis"
            )
        output_by_analysis.setdefault(artifact.producer.id, []).append(artifact)

    matching = {
        requirement.key: _exact_analysis_match(requirement, analyses)
        for requirement in requirements
    }
    node_ids = {
        node_id
        for dependency in dependencies
        for node_id in (dependency.upstream_id, dependency.downstream_id)
    }
    node_ids.update(
        artifact_id
        for requirement in requirements
        for artifact_id in requirement.input_artifact_ids
    )
    for analysis in matching.values():
        if analysis is None:
            continue
        node_ids.add(analysis.id)
        node_ids.update(item.id for item in output_by_analysis.get(analysis.id, ()))

    hashes = _current_hashes(
        analyses=analyses,
        artifacts=artifacts,
        overrides=current_hashes,
    )
    try:
        freshness = FreshnessEngine(dependencies).evaluate(
            node_ids=node_ids,
            current_hashes=hashes,
            invalid_ids=set() if invalid_ids is None else set(invalid_ids),
            superseded_ids=set() if superseded_ids is None else set(superseded_ids),
        )
    except ProvenanceIntegrityError as error:
        raise ElectronicAnalysisReconciliationError(str(error)) from error

    projections: list[ElectronicAnalysisProjection] = []
    for requirement in requirements:
        analysis = matching[requirement.key]
        outputs: tuple[Artifact, ...] = ()
        if analysis is not None:
            outputs = tuple(
                sorted(
                    output_by_analysis.get(analysis.id, ()),
                    key=lambda item: str(item.id),
                )
            )
        projections.append(
            _project_requirement(
                requirement=requirement,
                analysis=analysis,
                artifact_by_id=artifact_by_id,
                outputs=outputs,
                dependencies=dependencies,
                freshness=freshness,
                workflow_gates=workflow_gates,
            )
        )
    return ElectronicAnalysisReconciliationReport(projections=tuple(projections))


def reconcile_electronic_analyses_from_store(
    *,
    store: ProjectStore,
    requirements: tuple[ElectronicAnalysisRequirement, ...],
    workflow_gates: WorkflowScientificGateEvaluation | None = None,
    current_hash_overrides: Mapping[UUID, str] | None = None,
    invalid_ids: set[UUID] | None = None,
    superseded_ids: set[UUID] | None = None,
) -> ElectronicAnalysisReconciliationReport:
    """Reopen ProjectStore and recompute the same pure reconciliation projection."""

    bundle = store.open()
    hashes = _bundle_scientific_hashes(bundle)
    if current_hash_overrides is not None:
        hashes.update(_normalized_hash_mapping(current_hash_overrides))
    return reconcile_electronic_analyses(
        requirements=requirements,
        analyses=bundle.analyses,
        artifacts=bundle.artifacts,
        dependencies=bundle.dependency_records,
        current_hashes=hashes,
        workflow_gates=workflow_gates,
        invalid_ids=invalid_ids,
        superseded_ids=superseded_ids,
    )


def _project_requirement(
    *,
    requirement: ElectronicAnalysisRequirement,
    analysis: Analysis | None,
    artifact_by_id: dict[ArtifactId, Artifact],
    outputs: tuple[Artifact, ...],
    dependencies: tuple[DependencyRecord, ...],
    freshness: dict[UUID, FreshnessResult],
    workflow_gates: WorkflowScientificGateEvaluation | None,
) -> ElectronicAnalysisProjection:
    workflow_state, workflow_readiness, workflow_reasons = _workflow_gate_state(
        requirement=requirement,
        workflow_gates=workflow_gates,
    )
    if workflow_state is not None:
        return _projection(
            requirement=requirement,
            state=workflow_state,
            readiness=workflow_readiness,
            analysis=analysis,
            outputs=outputs,
            freshness_state=None if analysis is None else freshness[analysis.id].state,
            reason_codes=workflow_reasons,
        )

    input_state, input_readiness, input_reasons = _input_gate_state(
        requirement=requirement,
        artifact_by_id=artifact_by_id,
        freshness=freshness,
    )
    if input_state is not None:
        return _projection(
            requirement=requirement,
            state=input_state,
            readiness=input_readiness,
            analysis=analysis,
            outputs=outputs,
            freshness_state=None if analysis is None else freshness[analysis.id].state,
            reason_codes=input_reasons,
        )

    wait_reasons = (
        workflow_reasons
        if workflow_readiness is WorkflowStepReadiness.WAITING
        else ()
    ) + (
        input_reasons
        if input_readiness is WorkflowStepReadiness.WAITING
        else ()
    )

    if analysis is None:
        return _projection(
            requirement=requirement,
            state=ElectronicAnalysisScientificState.UNMATERIALIZED,
            readiness=(
                WorkflowStepReadiness.WAITING
                if wait_reasons
                else WorkflowStepReadiness.READY
            ),
            analysis=None,
            outputs=(),
            freshness_state=None,
            reason_codes=("exact_analysis_absent", *wait_reasons),
        )

    analysis_freshness = freshness[analysis.id]
    nonfresh = _nonfresh_projection_state(analysis_freshness.state)
    if nonfresh is not None:
        return _projection(
            requirement=requirement,
            state=nonfresh,
            readiness=WorkflowStepReadiness.BLOCKED,
            analysis=analysis,
            outputs=outputs,
            freshness_state=analysis_freshness.state,
            reason_codes=(
                "analysis_not_fresh",
                *_freshness_reason_codes(analysis_freshness.reasons),
            ),
        )

    status_projection = _analysis_status_projection(analysis.status)
    if status_projection is not None:
        state, readiness, reason = status_projection
        return _projection(
            requirement=requirement,
            state=state,
            readiness=readiness,
            analysis=analysis,
            outputs=outputs,
            freshness_state=analysis_freshness.state,
            reason_codes=(reason,),
        )

    if not outputs:
        return _projection(
            requirement=requirement,
            state=ElectronicAnalysisScientificState.INVALID,
            readiness=WorkflowStepReadiness.BLOCKED,
            analysis=analysis,
            outputs=(),
            freshness_state=analysis_freshness.state,
            reason_codes=("completed_analysis_has_no_output_artifact",),
        )

    provenance_issue = _completed_provenance_issue(
        requirement=requirement,
        analysis=analysis,
        outputs=outputs,
        dependencies=dependencies,
    )
    if provenance_issue is not None:
        return _projection(
            requirement=requirement,
            state=ElectronicAnalysisScientificState.INVALID,
            readiness=WorkflowStepReadiness.BLOCKED,
            analysis=analysis,
            outputs=outputs,
            freshness_state=analysis_freshness.state,
            reason_codes=(provenance_issue,),
        )

    output_state, output_readiness, output_reasons = _output_gate_state(
        outputs=outputs,
        freshness=freshness,
    )
    if output_state is not None:
        return _projection(
            requirement=requirement,
            state=output_state,
            readiness=output_readiness,
            analysis=analysis,
            outputs=outputs,
            freshness_state=analysis_freshness.state,
            reason_codes=output_reasons,
        )
    if output_readiness is WorkflowStepReadiness.WAITING:
        return _projection(
            requirement=requirement,
            state=ElectronicAnalysisScientificState.COMPLETED,
            readiness=WorkflowStepReadiness.WAITING,
            analysis=analysis,
            outputs=outputs,
            freshness_state=analysis_freshness.state,
            reason_codes=output_reasons,
        )
    if wait_reasons:
        return _projection(
            requirement=requirement,
            state=ElectronicAnalysisScientificState.COMPLETED,
            readiness=WorkflowStepReadiness.WAITING,
            analysis=analysis,
            outputs=outputs,
            freshness_state=analysis_freshness.state,
            reason_codes=wait_reasons,
        )

    return _projection(
        requirement=requirement,
        state=ElectronicAnalysisScientificState.COMPLETED,
        readiness=WorkflowStepReadiness.SATISFIED,
        analysis=analysis,
        outputs=outputs,
        freshness_state=analysis_freshness.state,
        reason_codes=("exact_analysis_fresh_and_reopenable",),
    )


def _workflow_gate_state(
    *,
    requirement: ElectronicAnalysisRequirement,
    workflow_gates: WorkflowScientificGateEvaluation | None,
) -> tuple[
    ElectronicAnalysisScientificState | None,
    WorkflowStepReadiness,
    tuple[str, ...],
]:
    anchor = requirement.workflow_anchor
    if anchor is None:
        return None, WorkflowStepReadiness.READY, ()
    if workflow_gates is None:
        raise ElectronicAnalysisReconciliationError(
            "workflow-anchored electronic requirement needs workflow gate evidence"
        )
    selections = tuple(
        item for item in workflow_gates.binding_selections if item.step_key == anchor.step_key
    )
    gates = tuple(
        item for item in workflow_gates.step_gates if item.step_key == anchor.step_key
    )
    if len(selections) != 1 or len(gates) != 1:
        raise ElectronicAnalysisReconciliationError(
            "workflow anchor step must resolve exactly once in gate projection"
        )
    selection = selections[0]
    gate = gates[0]
    current = selection.current_calculation
    if current is None:
        return None, WorkflowStepReadiness.WAITING, ("workflow_step_unmaterialized",)
    if current.project_id != requirement.project_id:
        raise ElectronicAnalysisReconciliationError(
            "workflow anchor current Calculation belongs to another Project"
        )
    if current.id != anchor.calculation_id:
        return (
            ElectronicAnalysisScientificState.SUPERSEDED,
            WorkflowStepReadiness.BLOCKED,
            ("workflow_anchor_not_current_generation",),
        )
    if selection.current_binding is None:
        raise ElectronicAnalysisReconciliationError(
            "workflow anchor current Calculation has no current binding"
        )
    if gate.current_binding_id != selection.current_binding.id:
        raise ElectronicAnalysisReconciliationError(
            "workflow step gate does not reference its current binding"
        )
    if gate.calculation_id != current.id:
        raise ElectronicAnalysisReconciliationError(
            "workflow step gate does not reference its current binding Calculation"
        )
    if gate.scientific_state is WorkflowStepScientificState.INVALID:
        return (
            ElectronicAnalysisScientificState.INVALID,
            WorkflowStepReadiness.BLOCKED,
            ("workflow_step_invalid",),
        )
    if gate.scientific_state is WorkflowStepScientificState.STALE:
        return (
            ElectronicAnalysisScientificState.STALE,
            WorkflowStepReadiness.BLOCKED,
            ("workflow_step_stale",),
        )
    if gate.scientific_state is WorkflowStepScientificState.SUPERSEDED:
        return (
            ElectronicAnalysisScientificState.SUPERSEDED,
            WorkflowStepReadiness.BLOCKED,
            ("workflow_step_superseded",),
        )
    if gate.readiness is WorkflowStepReadiness.BLOCKED:
        return (
            ElectronicAnalysisScientificState.BLOCKED,
            WorkflowStepReadiness.BLOCKED,
            ("workflow_step_blocked",),
        )
    if gate.readiness is not WorkflowStepReadiness.SATISFIED:
        return None, WorkflowStepReadiness.WAITING, ("workflow_step_not_satisfied",)
    return None, WorkflowStepReadiness.READY, ()


def _input_gate_state(
    *,
    requirement: ElectronicAnalysisRequirement,
    artifact_by_id: dict[ArtifactId, Artifact],
    freshness: dict[UUID, FreshnessResult],
) -> tuple[
    ElectronicAnalysisScientificState | None,
    WorkflowStepReadiness,
    tuple[str, ...],
]:
    retrieval_wait = False
    for artifact_id in requirement.input_artifact_ids:
        artifact = artifact_by_id[artifact_id]
        result = freshness[artifact_id]
        nonfresh = _nonfresh_projection_state(result.state)
        if nonfresh is not None:
            return (
                nonfresh,
                WorkflowStepReadiness.BLOCKED,
                (
                    "input_artifact_not_fresh",
                    *_freshness_reason_codes(result.reasons),
                ),
            )
        if artifact.sha256 is None:
            return (
                ElectronicAnalysisScientificState.INVALID,
                WorkflowStepReadiness.BLOCKED,
                ("input_artifact_sha256_missing",),
            )
        if artifact.availability is ArtifactAvailability.MISSING:
            return (
                ElectronicAnalysisScientificState.BLOCKED,
                WorkflowStepReadiness.BLOCKED,
                ("input_artifact_missing",),
            )
        if artifact.availability in {
            ArtifactAvailability.REMOTE,
            ArtifactAvailability.ARCHIVED,
        }:
            retrieval_wait = True
    if retrieval_wait:
        return None, WorkflowStepReadiness.WAITING, ("input_artifact_retrieval_required",)
    return None, WorkflowStepReadiness.READY, ()


def _output_gate_state(
    *,
    outputs: tuple[Artifact, ...],
    freshness: dict[UUID, FreshnessResult],
) -> tuple[
    ElectronicAnalysisScientificState | None,
    WorkflowStepReadiness,
    tuple[str, ...],
]:
    retrieval_wait = False
    for artifact in outputs:
        result = freshness[artifact.id]
        nonfresh = _nonfresh_projection_state(result.state)
        if nonfresh is not None:
            return (
                nonfresh,
                WorkflowStepReadiness.BLOCKED,
                (
                    "output_artifact_not_fresh",
                    *_freshness_reason_codes(result.reasons),
                ),
            )
        if artifact.sha256 is None:
            return (
                ElectronicAnalysisScientificState.INVALID,
                WorkflowStepReadiness.BLOCKED,
                ("output_artifact_sha256_missing",),
            )
        if artifact.availability is ArtifactAvailability.MISSING:
            return (
                ElectronicAnalysisScientificState.INVALID,
                WorkflowStepReadiness.BLOCKED,
                ("completed_analysis_output_missing",),
            )
        if artifact.availability in {
            ArtifactAvailability.REMOTE,
            ArtifactAvailability.ARCHIVED,
        }:
            retrieval_wait = True
    if retrieval_wait:
        return None, WorkflowStepReadiness.WAITING, ("output_artifact_retrieval_required",)
    return None, WorkflowStepReadiness.SATISFIED, ()


def _completed_provenance_issue(
    *,
    requirement: ElectronicAnalysisRequirement,
    analysis: Analysis,
    outputs: tuple[Artifact, ...],
    dependencies: tuple[DependencyRecord, ...],
) -> str | None:
    for artifact_id in requirement.input_artifact_ids:
        if not any(
            dependency.kind is DependencyKind.SCIENTIFIC
            and dependency.upstream_id == artifact_id
            and dependency.downstream_id == analysis.id
            for dependency in dependencies
        ):
            return "completed_analysis_missing_input_scientific_dependency"
    for artifact in outputs:
        if not any(
            dependency.kind is DependencyKind.SCIENTIFIC
            and dependency.upstream_id == analysis.id
            and dependency.downstream_id == artifact.id
            for dependency in dependencies
        ):
            return "completed_analysis_missing_output_scientific_dependency"
    return None


def _analysis_status_projection(
    status: AnalysisStatus,
) -> tuple[
    ElectronicAnalysisScientificState,
    WorkflowStepReadiness,
    str,
] | None:
    if status is AnalysisStatus.COMPLETED:
        return None
    if status in {AnalysisStatus.DRAFT, AnalysisStatus.READY, AnalysisStatus.RUNNING}:
        return (
            ElectronicAnalysisScientificState.IN_PROGRESS,
            WorkflowStepReadiness.WAITING,
            f"analysis_status_{status.value}",
        )
    if status is AnalysisStatus.BLOCKED:
        return (
            ElectronicAnalysisScientificState.BLOCKED,
            WorkflowStepReadiness.BLOCKED,
            "analysis_status_blocked",
        )
    if status is AnalysisStatus.FAILED:
        return (
            ElectronicAnalysisScientificState.FAILED,
            WorkflowStepReadiness.BLOCKED,
            "analysis_status_failed",
        )
    if status is AnalysisStatus.STALE:
        return (
            ElectronicAnalysisScientificState.STALE,
            WorkflowStepReadiness.BLOCKED,
            "analysis_status_stale",
        )
    if status is AnalysisStatus.INVALID:
        return (
            ElectronicAnalysisScientificState.INVALID,
            WorkflowStepReadiness.BLOCKED,
            "analysis_status_invalid",
        )
    raise ElectronicAnalysisReconciliationError("unsupported AnalysisStatus")


def _projection(
    *,
    requirement: ElectronicAnalysisRequirement,
    state: ElectronicAnalysisScientificState,
    readiness: WorkflowStepReadiness,
    analysis: Analysis | None,
    outputs: tuple[Artifact, ...],
    freshness_state: FreshnessState | None,
    reason_codes: tuple[str, ...],
) -> ElectronicAnalysisProjection:
    anchor = requirement.workflow_anchor
    return ElectronicAnalysisProjection(
        key=requirement.key,
        analysis_type=requirement.analysis_type,
        scientific_state=state,
        readiness=readiness,
        input_artifact_ids=requirement.input_artifact_ids,
        analysis_id=None if analysis is None else analysis.id,
        output_artifact_ids=tuple(item.id for item in outputs),
        freshness_state=freshness_state,
        workflow_step_key=None if anchor is None else anchor.step_key,
        workflow_calculation_id=None if anchor is None else anchor.calculation_id,
        reason_codes=tuple(dict.fromkeys(reason_codes)),
    )


def _exact_analysis_match(
    requirement: ElectronicAnalysisRequirement,
    analyses: tuple[Analysis, ...],
) -> Analysis | None:
    matches = tuple(
        analysis
        for analysis in analyses
        if analysis.project_id == requirement.project_id
        and analysis.analysis_type is requirement.analysis_type
        and analysis.input_artifact_ids == requirement.input_artifact_ids
        and analysis.parameters_hash == requirement.parameters_hash
    )
    if len(matches) > 1:
        raise ElectronicAnalysisReconciliationError(
            f"requirement {requirement.key!r} has duplicate exact Analysis identities"
        )
    return None if not matches else matches[0]


def _current_hashes(
    *,
    analyses: tuple[Analysis, ...],
    artifacts: tuple[Artifact, ...],
    overrides: Mapping[UUID, str] | None,
) -> dict[UUID, str]:
    result = {item.id: scientific_hash(item) for item in artifacts}
    result.update({item.id: scientific_hash(item) for item in analyses})
    if overrides is not None:
        result.update(_normalized_hash_mapping(overrides))
    return result


def _bundle_scientific_hashes(bundle: ProjectBundle) -> dict[UUID, str]:
    result: dict[UUID, str] = {}
    result.update({item.id: scientific_hash(item) for item in bundle.structure_variants})
    result.update({item.id: scientific_hash(item) for item in bundle.structure_snapshots})
    result.update({item.id: scientific_hash(item) for item in bundle.active_sites})
    result.update({item.id: scientific_hash(item) for item in bundle.adsorption_states})
    result.update({item.id: scientific_hash(item) for item in bundle.state_conformers})
    result.update({item.id: scientific_hash(item) for item in bundle.method_fingerprints})
    result.update({item.id: scientific_hash(item) for item in bundle.calculations})
    result.update({item.id: scientific_hash(item) for item in bundle.artifacts})
    result.update({item.id: scientific_hash(item) for item in bundle.analyses})
    return result


def _nonfresh_projection_state(
    state: FreshnessState,
) -> ElectronicAnalysisScientificState | None:
    if state is FreshnessState.FRESH:
        return None
    if state is FreshnessState.STALE:
        return ElectronicAnalysisScientificState.STALE
    if state is FreshnessState.INVALID:
        return ElectronicAnalysisScientificState.INVALID
    if state is FreshnessState.SUPERSEDED:
        return ElectronicAnalysisScientificState.SUPERSEDED
    raise ElectronicAnalysisReconciliationError("unsupported FreshnessState")


def _freshness_reason_codes(reasons: tuple[FreshnessReason, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item.code for item in reasons))


def _validate_requirement_keys(
    requirements: tuple[ElectronicAnalysisRequirement, ...],
) -> None:
    keys = tuple(item.key for item in requirements)
    if len(keys) != len(set(keys)):
        raise ElectronicAnalysisReconciliationError(
            "electronic analysis requirement keys must be unique"
        )


def _validate_requirement_inputs(
    requirements: tuple[ElectronicAnalysisRequirement, ...],
    artifact_by_id: dict[ArtifactId, Artifact],
) -> None:
    missing = {
        artifact_id
        for requirement in requirements
        for artifact_id in requirement.input_artifact_ids
        if artifact_id not in artifact_by_id
    }
    if missing:
        raise ElectronicAnalysisReconciliationError(
            "electronic requirement references a missing exact input Artifact"
        )


def _analysis_index(analyses: tuple[Analysis, ...]) -> dict[AnalysisId, Analysis]:
    result: dict[AnalysisId, Analysis] = {}
    for analysis in analyses:
        if analysis.id in result:
            raise ElectronicAnalysisReconciliationError("Analysis ids must be unique")
        result[analysis.id] = analysis
    return result


def _artifact_index(artifacts: tuple[Artifact, ...]) -> dict[ArtifactId, Artifact]:
    result: dict[ArtifactId, Artifact] = {}
    for artifact in artifacts:
        if artifact.id in result:
            raise ElectronicAnalysisReconciliationError("Artifact ids must be unique")
        result[artifact.id] = artifact
    return result


def _normalized_hash_mapping(values: Mapping[UUID, str]) -> dict[UUID, str]:
    return {
        subject_id: _normalized_sha256(value, "current scientific hash")
        for subject_id, value in values.items()
    }


def _normalized_sha256(value: str, field_name: str) -> str:
    normalized = value.strip().lower()
    if len(normalized) != 64:
        raise ElectronicAnalysisReconciliationError(
            f"{field_name} must be a 64-character SHA-256 digest"
        )
    try:
        int(normalized, 16)
    except ValueError as error:
        raise ElectronicAnalysisReconciliationError(
            f"{field_name} must contain only hexadecimal characters"
        ) from error
    return normalized

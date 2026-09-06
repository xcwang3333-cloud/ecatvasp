"""Pure thermochemistry/reaction-analysis reconciliation for v0.8 Block 8.

The projection reuses v0.6 workflow gates and the shared SCIENTIFIC FreshnessEngine.  It does not
persist a thermochemistry workflow, generation, or acceptance state machine.  Reaction requirements
may anchor to multiple current Calculation generations because one pathway can depend on several
slab/adsorbate/gas preparation workflows.
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

THERMOCHEMISTRY_ANALYSIS_TYPES = frozenset(
    {AnalysisType.THERMOCHEMISTRY, AnalysisType.REACTION_DIAGRAM}
)


class ThermochemistryReconciliationError(ValueError):
    """Raised when thermochemistry/reaction readiness cannot be derived exactly."""


class ThermochemistryAnalysisScientificState(StrEnum):
    UNMATERIALIZED = "unmaterialized"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"
    STALE = "stale"
    INVALID = "invalid"
    SUPERSEDED = "superseded"


@dataclass(frozen=True, slots=True)
class ThermochemistryWorkflowAnchor:
    """One exact current v0.6 workflow Calculation generation required by an Analysis."""

    step_key: str
    calculation_id: CalculationId

    def __post_init__(self) -> None:
        if not self.step_key.strip():
            raise ThermochemistryReconciliationError(
                "thermochemistry workflow anchor requires a non-blank step_key"
            )


@dataclass(frozen=True, slots=True)
class ThermochemistryAnalysisRequirement:
    """Ephemeral desired THERMOCHEMISTRY/REACTION_DIAGRAM Analysis identity."""

    key: str
    project_id: ProjectId
    analysis_type: AnalysisType
    input_artifact_ids: tuple[ArtifactId, ...]
    parameters_hash: str
    workflow_anchors: tuple[ThermochemistryWorkflowAnchor, ...] = ()

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise ThermochemistryReconciliationError("requirement requires a non-blank key")
        if self.analysis_type not in THERMOCHEMISTRY_ANALYSIS_TYPES:
            raise ThermochemistryReconciliationError(
                "requirement uses a non-thermochemistry/reaction AnalysisType"
            )
        if not self.input_artifact_ids:
            raise ThermochemistryReconciliationError(
                "requirement needs at least one exact input Artifact"
            )
        if len(self.input_artifact_ids) != len(set(self.input_artifact_ids)):
            raise ThermochemistryReconciliationError(
                "requirement input Artifact ids must be unique"
            )
        anchors = tuple(sorted(self.workflow_anchors, key=lambda item: item.step_key))
        keys = tuple(item.step_key for item in anchors)
        if len(keys) != len(set(keys)):
            raise ThermochemistryReconciliationError(
                "workflow anchor step_keys must be unique within a requirement"
            )
        object.__setattr__(self, "workflow_anchors", anchors)
        object.__setattr__(
            self,
            "parameters_hash",
            _normalized_sha256(self.parameters_hash, "parameters_hash"),
        )


@dataclass(frozen=True, slots=True)
class ThermochemistryAnalysisProjection:
    """Current derived state/readiness for one exact requirement."""

    key: str
    analysis_type: AnalysisType
    scientific_state: ThermochemistryAnalysisScientificState
    readiness: WorkflowStepReadiness
    input_artifact_ids: tuple[ArtifactId, ...]
    workflow_anchors: tuple[ThermochemistryWorkflowAnchor, ...] = ()
    analysis_id: AnalysisId | None = None
    output_artifact_ids: tuple[ArtifactId, ...] = ()
    freshness_state: FreshnessState | None = None
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
                    "workflow_anchors": self.workflow_anchors,
                    "analysis_id": self.analysis_id,
                    "output_artifact_ids": self.output_artifact_ids,
                    "freshness_state": self.freshness_state,
                    "reason_codes": self.reason_codes,
                }
            ),
        )


@dataclass(frozen=True, slots=True)
class ThermochemistryReconciliationReport:
    projections: tuple[ThermochemistryAnalysisProjection, ...]
    report_hash: str = field(init=False)

    def __post_init__(self) -> None:
        keys = tuple(item.key for item in self.projections)
        if len(keys) != len(set(keys)):
            raise ThermochemistryReconciliationError("projection keys must be unique")
        object.__setattr__(
            self,
            "report_hash",
            canonical_sha256(
                {"projection_hashes": tuple(item.projection_hash for item in self.projections)}
            ),
        )

    def requirement(self, key: str) -> ThermochemistryAnalysisProjection:
        for item in self.projections:
            if item.key == key:
                return item
        raise KeyError(key)


def reconcile_thermochemistry_analyses(
    *,
    requirements: tuple[ThermochemistryAnalysisRequirement, ...],
    analyses: tuple[Analysis, ...],
    artifacts: tuple[Artifact, ...],
    dependencies: tuple[DependencyRecord, ...],
    current_hashes: Mapping[UUID, str] | None = None,
    workflow_gates: WorkflowScientificGateEvaluation | None = None,
    invalid_ids: set[UUID] | None = None,
    superseded_ids: set[UUID] | None = None,
) -> ThermochemistryReconciliationReport:
    """Derive exact thermochemistry/reaction readiness without mutating persisted state."""

    _validate_requirement_keys(requirements)
    analysis_by_id = _analysis_index(analyses)
    artifact_by_id = _artifact_index(artifacts)
    _validate_requirement_inputs(requirements, artifact_by_id)

    output_by_analysis: dict[AnalysisId, list[Artifact]] = {}
    for artifact in artifacts:
        if not isinstance(artifact.producer, AnalysisProducerRef):
            continue
        if artifact.producer.id not in analysis_by_id:
            raise ThermochemistryReconciliationError(
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

    hashes = _current_hashes(analyses=analyses, artifacts=artifacts, overrides=current_hashes)
    try:
        freshness = FreshnessEngine(dependencies).evaluate(
            node_ids=node_ids,
            current_hashes=hashes,
            invalid_ids=set() if invalid_ids is None else set(invalid_ids),
            superseded_ids=set() if superseded_ids is None else set(superseded_ids),
        )
    except ProvenanceIntegrityError as error:
        raise ThermochemistryReconciliationError(str(error)) from error

    projections: list[ThermochemistryAnalysisProjection] = []
    for requirement in requirements:
        analysis = matching[requirement.key]
        outputs: tuple[Artifact, ...] = ()
        if analysis is not None:
            outputs = tuple(
                sorted(output_by_analysis.get(analysis.id, ()), key=lambda item: str(item.id))
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
    return ThermochemistryReconciliationReport(projections=tuple(projections))


def reconcile_thermochemistry_analyses_from_store(
    *,
    store: ProjectStore,
    requirements: tuple[ThermochemistryAnalysisRequirement, ...],
    workflow_gates: WorkflowScientificGateEvaluation | None = None,
    current_hash_overrides: Mapping[UUID, str] | None = None,
    invalid_ids: set[UUID] | None = None,
    superseded_ids: set[UUID] | None = None,
) -> ThermochemistryReconciliationReport:
    """Reopen ProjectStore and recompute the same pure reconciliation projection."""

    bundle = store.open()
    hashes = _bundle_scientific_hashes(bundle)
    if current_hash_overrides is not None:
        hashes.update(_normalized_hash_mapping(current_hash_overrides))
    return reconcile_thermochemistry_analyses(
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
    requirement: ThermochemistryAnalysisRequirement,
    analysis: Analysis | None,
    artifact_by_id: dict[ArtifactId, Artifact],
    outputs: tuple[Artifact, ...],
    dependencies: tuple[DependencyRecord, ...],
    freshness: dict[UUID, FreshnessResult],
    workflow_gates: WorkflowScientificGateEvaluation | None,
) -> ThermochemistryAnalysisProjection:
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

    input_state, input_readiness, input_reasons = _artifact_gate_state(
        artifact_ids=requirement.input_artifact_ids,
        artifact_by_id=artifact_by_id,
        freshness=freshness,
        output=False,
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

    wait_reasons = tuple(
        dict.fromkeys(
            (
                *(workflow_reasons if workflow_readiness is WorkflowStepReadiness.WAITING else ()),
                *(input_reasons if input_readiness is WorkflowStepReadiness.WAITING else ()),
            )
        )
    )
    if analysis is None:
        return _projection(
            requirement=requirement,
            state=ThermochemistryAnalysisScientificState.UNMATERIALIZED,
            readiness=(
                WorkflowStepReadiness.WAITING if wait_reasons else WorkflowStepReadiness.READY
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
            state=ThermochemistryAnalysisScientificState.INVALID,
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
            state=ThermochemistryAnalysisScientificState.INVALID,
            readiness=WorkflowStepReadiness.BLOCKED,
            analysis=analysis,
            outputs=outputs,
            freshness_state=analysis_freshness.state,
            reason_codes=(provenance_issue,),
        )

    output_state, output_readiness, output_reasons = _artifact_gate_state(
        artifact_ids=tuple(item.id for item in outputs),
        artifact_by_id=artifact_by_id,
        freshness=freshness,
        output=True,
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
    if output_readiness is WorkflowStepReadiness.WAITING or wait_reasons:
        return _projection(
            requirement=requirement,
            state=ThermochemistryAnalysisScientificState.COMPLETED,
            readiness=WorkflowStepReadiness.WAITING,
            analysis=analysis,
            outputs=outputs,
            freshness_state=analysis_freshness.state,
            reason_codes=tuple(dict.fromkeys((*output_reasons, *wait_reasons))),
        )
    return _projection(
        requirement=requirement,
        state=ThermochemistryAnalysisScientificState.COMPLETED,
        readiness=WorkflowStepReadiness.SATISFIED,
        analysis=analysis,
        outputs=outputs,
        freshness_state=analysis_freshness.state,
        reason_codes=("exact_analysis_fresh_and_reopenable",),
    )


def _workflow_gate_state(
    *,
    requirement: ThermochemistryAnalysisRequirement,
    workflow_gates: WorkflowScientificGateEvaluation | None,
) -> tuple[ThermochemistryAnalysisScientificState | None, WorkflowStepReadiness, tuple[str, ...]]:
    if not requirement.workflow_anchors:
        return None, WorkflowStepReadiness.READY, ()
    if workflow_gates is None:
        raise ThermochemistryReconciliationError(
            "workflow-anchored requirement needs workflow gate evidence"
        )

    candidates: list[ThermochemistryAnalysisScientificState] = []
    reasons: list[str] = []
    waiting = False
    for anchor in requirement.workflow_anchors:
        selections = tuple(
            item
            for item in workflow_gates.binding_selections
            if item.step_key == anchor.step_key
        )
        gates = tuple(
            item for item in workflow_gates.step_gates if item.step_key == anchor.step_key
        )
        if len(selections) != 1 or len(gates) != 1:
            raise ThermochemistryReconciliationError(
                "workflow anchor step must resolve exactly once in gate projection"
            )
        selection = selections[0]
        gate = gates[0]
        current = selection.current_calculation
        if current is None:
            waiting = True
            reasons.append(f"workflow_step_unmaterialized:{anchor.step_key}")
            continue
        if current.project_id != requirement.project_id:
            raise ThermochemistryReconciliationError(
                "workflow anchor current Calculation belongs to another Project"
            )
        if current.id != anchor.calculation_id:
            candidates.append(ThermochemistryAnalysisScientificState.SUPERSEDED)
            reasons.append(f"workflow_anchor_not_current_generation:{anchor.step_key}")
            continue
        if selection.current_binding is None:
            raise ThermochemistryReconciliationError(
                "workflow anchor current Calculation has no current binding"
            )
        if (
            gate.current_binding_id != selection.current_binding.id
            or gate.calculation_id != current.id
        ):
            raise ThermochemistryReconciliationError(
                "workflow gate does not reference its selected current binding/Calculation"
            )
        if gate.scientific_state is WorkflowStepScientificState.INVALID:
            candidates.append(ThermochemistryAnalysisScientificState.INVALID)
            reasons.append(f"workflow_step_invalid:{anchor.step_key}")
        elif gate.scientific_state is WorkflowStepScientificState.STALE:
            candidates.append(ThermochemistryAnalysisScientificState.STALE)
            reasons.append(f"workflow_step_stale:{anchor.step_key}")
        elif gate.scientific_state is WorkflowStepScientificState.SUPERSEDED:
            candidates.append(ThermochemistryAnalysisScientificState.SUPERSEDED)
            reasons.append(f"workflow_step_superseded:{anchor.step_key}")
        elif gate.readiness is WorkflowStepReadiness.BLOCKED:
            candidates.append(ThermochemistryAnalysisScientificState.BLOCKED)
            reasons.append(f"workflow_step_blocked:{anchor.step_key}")
        elif gate.readiness is not WorkflowStepReadiness.SATISFIED:
            waiting = True
            reasons.append(f"workflow_step_not_satisfied:{anchor.step_key}")

    if candidates:
        precedence = {
            ThermochemistryAnalysisScientificState.BLOCKED: 1,
            ThermochemistryAnalysisScientificState.SUPERSEDED: 2,
            ThermochemistryAnalysisScientificState.STALE: 3,
            ThermochemistryAnalysisScientificState.INVALID: 4,
        }
        state = max(candidates, key=lambda item: precedence[item])
        return state, WorkflowStepReadiness.BLOCKED, tuple(dict.fromkeys(reasons))
    if waiting:
        return None, WorkflowStepReadiness.WAITING, tuple(dict.fromkeys(reasons))
    return None, WorkflowStepReadiness.READY, ()


def _artifact_gate_state(
    *,
    artifact_ids: tuple[ArtifactId, ...],
    artifact_by_id: dict[ArtifactId, Artifact],
    freshness: dict[UUID, FreshnessResult],
    output: bool,
) -> tuple[ThermochemistryAnalysisScientificState | None, WorkflowStepReadiness, tuple[str, ...]]:
    retrieval_wait = False
    for artifact_id in artifact_ids:
        artifact = artifact_by_id[artifact_id]
        result = freshness[artifact_id]
        nonfresh = _nonfresh_projection_state(result.state)
        if nonfresh is not None:
            return (
                nonfresh,
                WorkflowStepReadiness.BLOCKED,
                (
                    "output_artifact_not_fresh" if output else "input_artifact_not_fresh",
                    *_freshness_reason_codes(result.reasons),
                ),
            )
        if artifact.sha256 is None:
            return (
                ThermochemistryAnalysisScientificState.INVALID,
                WorkflowStepReadiness.BLOCKED,
                ("artifact_sha256_missing",),
            )
        if artifact.availability is ArtifactAvailability.MISSING:
            missing_state = (
                ThermochemistryAnalysisScientificState.INVALID
                if output
                else ThermochemistryAnalysisScientificState.BLOCKED
            )
            return (
                missing_state,
                WorkflowStepReadiness.BLOCKED,
                ("completed_analysis_output_missing" if output else "input_artifact_missing",),
            )
        if artifact.availability in {ArtifactAvailability.REMOTE, ArtifactAvailability.ARCHIVED}:
            retrieval_wait = True
    if retrieval_wait:
        return None, WorkflowStepReadiness.WAITING, (
            "output_artifact_retrieval_required" if output else "input_artifact_retrieval_required",
        )
    return None, WorkflowStepReadiness.SATISFIED if output else WorkflowStepReadiness.READY, ()


def _completed_provenance_issue(
    *,
    requirement: ThermochemistryAnalysisRequirement,
    analysis: Analysis,
    outputs: tuple[Artifact, ...],
    dependencies: tuple[DependencyRecord, ...],
) -> str | None:
    for artifact_id in requirement.input_artifact_ids:
        if not any(
            item.kind is DependencyKind.SCIENTIFIC
            and item.upstream_id == artifact_id
            and item.downstream_id == analysis.id
            for item in dependencies
        ):
            return "completed_analysis_missing_input_scientific_dependency"
    for artifact in outputs:
        if not any(
            item.kind is DependencyKind.SCIENTIFIC
            and item.upstream_id == analysis.id
            and item.downstream_id == artifact.id
            for item in dependencies
        ):
            return "completed_analysis_missing_output_scientific_dependency"
    return None


def _analysis_status_projection(
    status: AnalysisStatus,
) -> tuple[ThermochemistryAnalysisScientificState, WorkflowStepReadiness, str] | None:
    if status is AnalysisStatus.COMPLETED:
        return None
    if status in {AnalysisStatus.DRAFT, AnalysisStatus.READY, AnalysisStatus.RUNNING}:
        return (
            ThermochemistryAnalysisScientificState.IN_PROGRESS,
            WorkflowStepReadiness.WAITING,
            f"analysis_status_{status.value}",
        )
    mapping = {
        AnalysisStatus.BLOCKED: ThermochemistryAnalysisScientificState.BLOCKED,
        AnalysisStatus.FAILED: ThermochemistryAnalysisScientificState.FAILED,
        AnalysisStatus.STALE: ThermochemistryAnalysisScientificState.STALE,
        AnalysisStatus.INVALID: ThermochemistryAnalysisScientificState.INVALID,
    }
    try:
        state = mapping[status]
    except KeyError as error:
        raise ThermochemistryReconciliationError("unsupported AnalysisStatus") from error
    return state, WorkflowStepReadiness.BLOCKED, f"analysis_status_{status.value}"


def _projection(
    *,
    requirement: ThermochemistryAnalysisRequirement,
    state: ThermochemistryAnalysisScientificState,
    readiness: WorkflowStepReadiness,
    analysis: Analysis | None,
    outputs: tuple[Artifact, ...],
    freshness_state: FreshnessState | None,
    reason_codes: tuple[str, ...],
) -> ThermochemistryAnalysisProjection:
    return ThermochemistryAnalysisProjection(
        key=requirement.key,
        analysis_type=requirement.analysis_type,
        scientific_state=state,
        readiness=readiness,
        input_artifact_ids=requirement.input_artifact_ids,
        workflow_anchors=requirement.workflow_anchors,
        analysis_id=None if analysis is None else analysis.id,
        output_artifact_ids=tuple(item.id for item in outputs),
        freshness_state=freshness_state,
        reason_codes=tuple(dict.fromkeys(reason_codes)),
    )


def _exact_analysis_match(
    requirement: ThermochemistryAnalysisRequirement,
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
        raise ThermochemistryReconciliationError(
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
) -> ThermochemistryAnalysisScientificState | None:
    if state is FreshnessState.FRESH:
        return None
    if state is FreshnessState.STALE:
        return ThermochemistryAnalysisScientificState.STALE
    if state is FreshnessState.INVALID:
        return ThermochemistryAnalysisScientificState.INVALID
    if state is FreshnessState.SUPERSEDED:
        return ThermochemistryAnalysisScientificState.SUPERSEDED
    raise ThermochemistryReconciliationError("unsupported FreshnessState")


def _freshness_reason_codes(reasons: tuple[FreshnessReason, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item.code for item in reasons))


def _validate_requirement_keys(
    requirements: tuple[ThermochemistryAnalysisRequirement, ...],
) -> None:
    keys = tuple(item.key for item in requirements)
    if len(keys) != len(set(keys)):
        raise ThermochemistryReconciliationError("requirement keys must be unique")


def _validate_requirement_inputs(
    requirements: tuple[ThermochemistryAnalysisRequirement, ...],
    artifact_by_id: dict[ArtifactId, Artifact],
) -> None:
    missing = {
        artifact_id
        for requirement in requirements
        for artifact_id in requirement.input_artifact_ids
        if artifact_id not in artifact_by_id
    }
    if missing:
        raise ThermochemistryReconciliationError(
            "requirement references a missing exact input Artifact"
        )


def _analysis_index(analyses: tuple[Analysis, ...]) -> dict[AnalysisId, Analysis]:
    result: dict[AnalysisId, Analysis] = {}
    for analysis in analyses:
        if analysis.id in result:
            raise ThermochemistryReconciliationError("Analysis ids must be unique")
        result[analysis.id] = analysis
    return result


def _artifact_index(artifacts: tuple[Artifact, ...]) -> dict[ArtifactId, Artifact]:
    result: dict[ArtifactId, Artifact] = {}
    for artifact in artifacts:
        if artifact.id in result:
            raise ThermochemistryReconciliationError("Artifact ids must be unique")
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
        raise ThermochemistryReconciliationError(
            f"{field_name} must be a 64-character SHA-256 digest"
        )
    try:
        int(normalized, 16)
    except ValueError as error:
        raise ThermochemistryReconciliationError(
            f"{field_name} must contain only hexadecimal characters"
        ) from error
    return normalized

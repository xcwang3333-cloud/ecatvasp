"""Project-scoped application services over existing ECatVASP authorities.

The service reduces client orchestration boilerplate but deliberately does not own
scientific truth, workflow history, scheduler state, or persisted UI/session state.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from uuid import UUID

from ecatvasp.domain import (
    Calculation,
    CalculationId,
    MethodFingerprint,
    MethodFingerprintId,
    ProjectId,
    StructureSnapshot,
    StructureSnapshotId,
    StructureVariant,
    StructureVariantId,
    WorkflowPlanId,
    WorkflowRecipeIdentity,
)
from ecatvasp.execution.batch import BatchConcurrencyPolicy
from ecatvasp.reporting import (
    ScientificPresentation,
    ScientificReport,
    build_scientific_report,
    render_inventory_csv,
    render_report_json,
    render_report_markdown,
)
from ecatvasp.storage import ProjectBundle, ProjectStore
from ecatvasp.vasp import (
    ExecutionPlan,
    ProjectNumericalLock,
    VaspConvergenceAssessment,
    VaspConvergenceEvidence,
    VaspResultDocument,
    VaspScientificResultIntake,
    VaspScientificResultMaterialization,
    VaspSystemContext,
    materialize_vasp_scientific_result,
    promote_vasp_contcar_snapshot,
    reconstruct_vasp_contcar_snapshot,
)
from ecatvasp.vasp.result_supporting_provenance import (
    bind_vasp_atom_identity_result_provenance,
)
from ecatvasp.vasp.structure_promotion import (
    VaspContcarReconstruction,
    VaspStructurePromotionResult,
)
from ecatvasp.vasp.structure_provenance import (
    VaspContcarReconstructionProvenance,
    build_vasp_contcar_reconstruction_provenance,
)
from ecatvasp.workflow import (
    AcceptedStructureSource,
    WorkflowDispatchPersistenceResult,
    WorkflowMaterializationPersistenceResult,
    WorkflowOrchestrationEvaluation,
    WorkflowPlanPersistenceResult,
    WorkflowRecoveryAttemptSource,
    persist_or_reuse_workflow_materialization,
    persist_or_reuse_workflow_plan,
    persist_workflow_dispatch_wave,
    plan_scientific_workflow,
)
from ecatvasp.workspace import (
    WorkflowReadinessDashboard,
    WorkspaceProjection,
    WorkspaceScientificInventory,
    build_scientific_inventory,
    build_workspace_projection,
)


class ApplicationServiceError(ValueError):
    """Raised when an application operation cannot resolve current durable project state."""


class ApplicationReportFormat(StrEnum):
    """Deterministic Block 5 report renderings exposed by the application service."""

    JSON = "json"
    CSV = "csv"
    MARKDOWN = "markdown"


@dataclass(frozen=True, slots=True)
class ApplicationInspectionResult:
    """Current workspace and provenance/freshness projections from one ProjectStore reopen."""

    projection: WorkspaceProjection
    inventory: WorkspaceScientificInventory


@dataclass(frozen=True, slots=True)
class ApplicationReportResult:
    """One transient scientific report plus the requested deterministic rendering."""

    report: ScientificReport
    format: ApplicationReportFormat
    content: str


@dataclass(frozen=True, slots=True)
class ApplicationVaspAnalysisResult:
    """Receipt for one durably persisted managed VASP scientific-result graph."""

    project_id: ProjectId
    materialization: VaspScientificResultMaterialization


@dataclass(frozen=True, slots=True)
class ApplicationStructurePromotionResult:
    """Receipt for one reconstruction + explicit converged promotion persisted atomically."""

    project_id: ProjectId
    reconstruction: VaspContcarReconstruction
    promotion: VaspStructurePromotionResult
    reconstruction_provenance: VaspContcarReconstructionProvenance


class ProjectApplicationService:
    """Experiment-like application seam bound to one durable ECatVASP project store."""

    def __init__(self, store: ProjectStore) -> None:
        self._store = store

    @property
    def store(self) -> ProjectStore:
        """Return the underlying durable ProjectStore authority."""

        return self._store

    def inspect(
        self,
        *,
        observed_hashes: Mapping[UUID, str] | None = None,
        invalid_ids: set[UUID] | None = None,
        superseded_ids: set[UUID] | None = None,
    ) -> ApplicationInspectionResult:
        """Reopen and project current project/workspace/provenance state."""

        bundle = self._store.open()
        return ApplicationInspectionResult(
            projection=build_workspace_projection(bundle),
            inventory=build_scientific_inventory(
                bundle,
                observed_hashes=observed_hashes,
                invalid_ids=invalid_ids,
                superseded_ids=superseded_ids,
            ),
        )

    def prepare_workflow(
        self,
        *,
        workflow_recipe: WorkflowRecipeIdentity,
        root_structure_snapshot_id: StructureSnapshotId,
        parameters_hash: str | None = None,
    ) -> WorkflowPlanPersistenceResult:
        """Plan and durably persist/reuse canonical workflow intent."""

        bundle = self._store.open()
        _require_snapshot(bundle, root_structure_snapshot_id)
        planning = plan_scientific_workflow(
            project_id=bundle.project.id,
            workflow_recipe=workflow_recipe,
            root_structure_snapshot_id=root_structure_snapshot_id,
            parameters_hash=parameters_hash,
        )
        return persist_or_reuse_workflow_plan(store=self._store, planning=planning)

    def prepare_workflow_step(
        self,
        *,
        workflow_plan_id: WorkflowPlanId,
        orchestration: WorkflowOrchestrationEvaluation,
        step_key: str,
        method_fingerprint_id: MethodFingerprintId,
        system_context: VaspSystemContext,
        project_lock: ProjectNumericalLock | None,
        accepted_structure_source: AcceptedStructureSource | None = None,
    ) -> WorkflowMaterializationPersistenceResult:
        """Materialize/reuse one exact current workflow generation through v0.6 durability."""

        bundle = self._store.open()
        plan = _require_workflow_plan(bundle, workflow_plan_id)
        fingerprint = _require_method_fingerprint(bundle, method_fingerprint_id)
        incoming = tuple(
            edge for edge in plan.edges if edge.downstream_step_key == step_key
        )
        root_snapshot: StructureSnapshot | None = None
        if not incoming:
            if accepted_structure_source is not None:
                raise ApplicationServiceError(
                    "root workflow step must not receive an accepted-structure source"
                )
            root_snapshot = _require_snapshot(bundle, plan.root_structure_snapshot_id)
        elif accepted_structure_source is None:
            raise ApplicationServiceError(
                "downstream workflow step requires an explicit AcceptedStructureSource"
            )

        return persist_or_reuse_workflow_materialization(
            store=self._store,
            plan=plan,
            orchestration=orchestration,
            step_key=step_key,
            fingerprint=fingerprint,
            system_context=system_context,
            project_lock=project_lock,
            root_snapshot=root_snapshot,
            accepted_structure_source=accepted_structure_source,
        )

    def run_workflow(
        self,
        *,
        workflow_plan_id: WorkflowPlanId,
        orchestration: WorkflowOrchestrationEvaluation,
        concurrency: BatchConcurrencyPolicy,
        recovery_attempt_sources: tuple[WorkflowRecoveryAttemptSource, ...] = (),
    ) -> WorkflowDispatchPersistenceResult:
        """Persist replay-safe ExecutionAttempts and return the scheduler dispatch handoff.

        This operation does not submit a scheduler job and does not infer scientific success from
        scheduler state.
        """

        bundle = self._store.open()
        plan = _require_workflow_plan(bundle, workflow_plan_id)
        return persist_workflow_dispatch_wave(
            store=self._store,
            plan=plan,
            orchestration=orchestration,
            concurrency=concurrency,
            recovery_attempt_sources=recovery_attempt_sources,
        )

    def analyze_vasp_result(
        self,
        *,
        calculation_id: CalculationId,
        intake: VaspScientificResultIntake,
        result: VaspResultDocument,
        assessment: VaspConvergenceAssessment,
        execution_plan: ExecutionPlan | None = None,
    ) -> ApplicationVaspAnalysisResult:
        """Persist an already-normalized and already-classified managed VASP result graph."""

        bundle = self._store.open()
        calculation = _require_calculation(bundle, calculation_id)
        materialization = materialize_vasp_scientific_result(
            project_root=self._store.root,
            calculation=calculation,
            intake=intake,
            result=result,
            assessment=assessment,
        )
        uid_bound = (
            result.forces is not None
            or result.magnetization is not None
            or result.frequencies is not None
        )
        if uid_bound:
            if execution_plan is None:
                raise ApplicationServiceError(
                    "UID-bound VASP result requires the exact managed ExecutionPlan"
                )
            materialization = bind_vasp_atom_identity_result_provenance(
                plan=execution_plan,
                result=result,
                materialization=materialization,
            )

        _require_persisted_artifact_inputs(bundle, materialization)
        _require_new_entity_ids(
            bundle,
            (
                *materialization.analyses,
                *materialization.artifacts,
                *materialization.provenance_records,
                *materialization.dependency_records,
            ),
        )
        updated = replace(
            bundle,
            calculations=_replace_calculation(
                bundle.calculations,
                materialization.updated_calculation,
            ),
            artifacts=(*bundle.artifacts, *materialization.artifacts),
            analyses=(*bundle.analyses, *materialization.analyses),
            provenance_records=(
                *bundle.provenance_records,
                *materialization.provenance_records,
            ),
            dependency_records=(
                *bundle.dependency_records,
                *materialization.dependency_records,
            ),
        )
        self._store.save(updated)
        reopened = self._store.open()
        persisted = _require_calculation(reopened, calculation_id)
        if persisted != materialization.updated_calculation:
            raise ApplicationServiceError(
                "VASP scientific-result Calculation failed post-save verification"
            )
        return ApplicationVaspAnalysisResult(
            project_id=reopened.project.id,
            materialization=materialization,
        )

    def promote_vasp_structure(
        self,
        *,
        structure_variant_id: StructureVariantId,
        calculation_id: CalculationId,
        method_fingerprint_id: MethodFingerprintId,
        execution_plan: ExecutionPlan,
        intake: object,
        evidence: VaspConvergenceEvidence,
        label: str | None = None,
    ) -> ApplicationStructurePromotionResult:
        """Reconstruct, gate, and atomically persist one explicit converged CONTCAR promotion."""

        from ecatvasp.vasp import VaspResultArtifactIntake

        if not isinstance(intake, VaspResultArtifactIntake):
            raise ApplicationServiceError(
                "structure promotion requires a managed VaspResultArtifactIntake"
            )
        bundle = self._store.open()
        variant = _require_structure_variant(bundle, structure_variant_id)
        calculation = _require_calculation(bundle, calculation_id)
        fingerprint = _require_method_fingerprint(bundle, method_fingerprint_id)
        input_snapshot = _require_snapshot(
            bundle,
            calculation.input_structure_snapshot_id,
        )
        reconstruction = reconstruct_vasp_contcar_snapshot(
            project_root=self._store.root,
            calculation=calculation,
            plan=execution_plan,
            intake=intake,
            input_snapshot=input_snapshot,
            label=label,
        )
        provenance = build_vasp_contcar_reconstruction_provenance(
            calculation=calculation,
            plan=execution_plan,
            intake=intake,
            input_snapshot=input_snapshot,
            reconstruction=reconstruction,
        )
        promotion = promote_vasp_contcar_snapshot(
            variant=variant,
            calculation=calculation,
            fingerprint=fingerprint,
            evidence=evidence,
            input_snapshot=input_snapshot,
            reconstruction=reconstruction,
        )
        _require_new_entity_ids(
            bundle,
            (
                reconstruction.snapshot,
                provenance.provenance_record,
                *provenance.dependency_records,
            ),
        )
        updated = replace(
            bundle,
            structure_variants=_replace_structure_variant(
                bundle.structure_variants,
                promotion.updated_variant,
            ),
            structure_snapshots=(
                *bundle.structure_snapshots,
                reconstruction.snapshot,
            ),
            provenance_records=(
                *bundle.provenance_records,
                provenance.provenance_record,
            ),
            dependency_records=(
                *bundle.dependency_records,
                *provenance.dependency_records,
            ),
        )
        self._store.save(updated)
        reopened = self._store.open()
        persisted_variant = _require_structure_variant(reopened, structure_variant_id)
        if persisted_variant != promotion.updated_variant:
            raise ApplicationServiceError("structure promotion failed post-save verification")
        _require_snapshot(reopened, reconstruction.snapshot.id)
        return ApplicationStructurePromotionResult(
            project_id=reopened.project.id,
            reconstruction=reconstruction,
            promotion=promotion,
            reconstruction_provenance=provenance,
        )

    def report(
        self,
        *,
        format: ApplicationReportFormat | str,
        inventory: WorkspaceScientificInventory | None = None,
        readiness: tuple[WorkflowReadinessDashboard, ...] = (),
        presentations: tuple[ScientificPresentation, ...] = (),
    ) -> ApplicationReportResult:
        """Render one deterministic Block 5 report from current reopened project state."""

        try:
            selected_format = ApplicationReportFormat(format)
        except ValueError as error:
            raise ApplicationServiceError("unsupported application report format") from error
        bundle = self._store.open()
        report = build_scientific_report(
            bundle,
            inventory=inventory,
            readiness=readiness,
            presentations=presentations,
        )
        if selected_format is ApplicationReportFormat.JSON:
            content = render_report_json(report)
        elif selected_format is ApplicationReportFormat.CSV:
            content = render_inventory_csv(report)
        else:
            content = render_report_markdown(report)
        return ApplicationReportResult(
            report=report,
            format=selected_format,
            content=content,
        )


def _require_workflow_plan(bundle: ProjectBundle, plan_id: WorkflowPlanId):
    for item in bundle.workflow_plans:
        if item.id == plan_id:
            return item
    raise ApplicationServiceError("workflow plan is absent from the current ProjectStore")


def _require_method_fingerprint(
    bundle: ProjectBundle,
    fingerprint_id: MethodFingerprintId,
) -> MethodFingerprint:
    for item in bundle.method_fingerprints:
        if item.id == fingerprint_id:
            return item
    raise ApplicationServiceError("MethodFingerprint is absent from the current ProjectStore")


def _require_snapshot(
    bundle: ProjectBundle,
    snapshot_id: StructureSnapshotId,
) -> StructureSnapshot:
    for item in bundle.structure_snapshots:
        if item.id == snapshot_id:
            return item
    raise ApplicationServiceError("StructureSnapshot is absent from the current ProjectStore")


def _require_structure_variant(
    bundle: ProjectBundle,
    variant_id: StructureVariantId,
) -> StructureVariant:
    for item in bundle.structure_variants:
        if item.id == variant_id:
            return item
    raise ApplicationServiceError("StructureVariant is absent from the current ProjectStore")


def _require_calculation(
    bundle: ProjectBundle,
    calculation_id: CalculationId,
) -> Calculation:
    for item in bundle.calculations:
        if item.id == calculation_id:
            return item
    raise ApplicationServiceError("Calculation is absent from the current ProjectStore")


def _replace_calculation(
    values: tuple[Calculation, ...],
    updated: Calculation,
) -> tuple[Calculation, ...]:
    matches = sum(item.id == updated.id for item in values)
    if matches != 1:
        raise ApplicationServiceError(
            "scientific-result persistence requires exactly one current Calculation"
        )
    return tuple(updated if item.id == updated.id else item for item in values)


def _replace_structure_variant(
    values: tuple[StructureVariant, ...],
    updated: StructureVariant,
) -> tuple[StructureVariant, ...]:
    matches = sum(item.id == updated.id for item in values)
    if matches != 1:
        raise ApplicationServiceError(
            "structure promotion requires exactly one current StructureVariant"
        )
    return tuple(updated if item.id == updated.id else item for item in values)


def _require_new_entity_ids(bundle: ProjectBundle, values: tuple[object, ...]) -> None:
    current_ids = {_entity_uuid(item) for item in bundle.entities()}
    new_ids = tuple(_entity_uuid(item) for item in values)
    if len(new_ids) != len(set(new_ids)):
        raise ApplicationServiceError("application mutation produced duplicate new entity UUIDs")
    if current_ids.intersection(new_ids):
        raise ApplicationServiceError("application mutation collides with a persisted entity UUID")


def _require_persisted_artifact_inputs(
    bundle: ProjectBundle,
    materialization: VaspScientificResultMaterialization,
) -> None:
    known = {item.id for item in bundle.artifacts}
    new_ids = {item.id for item in materialization.artifacts}
    for analysis in materialization.analyses:
        missing = set(analysis.input_artifact_ids) - known - new_ids
        if missing:
            raise ApplicationServiceError(
                "VASP scientific-result Analysis references an Artifact outside ProjectStore"
            )


def _entity_uuid(entity: object) -> UUID:
    value = getattr(entity, "id", None)
    if not isinstance(value, UUID):
        raise ApplicationServiceError("application mutation entity must expose a UUID id")
    return value

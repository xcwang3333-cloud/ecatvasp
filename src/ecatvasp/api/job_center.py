"""Task-oriented HPC execution application service for v1.1 Block 4.

The service composes existing VASP preparation, workflow dispatch, SSH/Slurm,
monitoring, cancellation, and retrieval authorities. It preserves the frozen
separation between Calculation, ExecutionAttempt, RemoteJob, and scientific
result interpretation. ProjectStore is reopened before every operation and each
execution-side-effect phase is persisted and verified before the next phase.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import UUID

from ecatvasp.api.application import ApplicationServiceError, ProjectApplicationService
from ecatvasp.api.calculation_wizard import WizardNumericalEvidence
from ecatvasp.api.execution_plan_resolver import resolve_execution_plan
from ecatvasp.api.workflow_runtime import build_current_workflow_orchestration
from ecatvasp.domain import (
    Artifact,
    ArtifactType,
    AtomUid,
    Calculation,
    CalculationId,
    ExecutionAttempt,
    ExecutionAttemptId,
    ExecutionAttemptProducerRef,
    ExecutionAttemptStatus,
    ExecutionSettings,
    MethodFingerprint,
    RemoteJob,
    RemoteJobId,
    SchedulerState,
    SchedulerType,
    ScientificWorkflowPlan,
    StructureSnapshot,
    WorkflowPlanId,
    WorkflowStepBinding,
)
from ecatvasp.execution.adapters import TransportAdapter
from ecatvasp.execution.batch import BatchConcurrencyPolicy
from ecatvasp.execution.monitoring import (
    SlurmMonitoringPackage,
    cancel_remote_slurm,
    monitor_remote_slurm,
)
from ecatvasp.execution.remote import RemotePotcarLibrary, stage_remote_runtime
from ecatvasp.execution.retrieval import RemoteRetrievalPackage, retrieve_remote_outputs
from ecatvasp.execution.slurm import (
    SlurmAdapter,
    resolve_scheduler_resources,
    submit_remote_slurm,
)
from ecatvasp.execution.targets import ExecutionTargetProfile, TransportKind
from ecatvasp.storage import ProjectBundle, ProjectStore
from ecatvasp.vasp.analysis_pipeline import prepare_analysis_prerequisite_inputs
from ecatvasp.vasp.contracts import (
    LatticeAxis,
    ProjectNumericalLock,
    VaspSystemContext,
    VaspSystemKind,
)
from ecatvasp.vasp.execution_plan import ExecutionPlan, build_execution_plan
from ecatvasp.vasp.frequency import FrequencySelection
from ecatvasp.vasp.frequency_pipeline import prepare_frequency_calculation_inputs
from ecatvasp.vasp.incar import ECATVASP_DIPOLE_AXIS
from ecatvasp.vasp.materialization import MaterializedInputSet
from ecatvasp.vasp.pipeline import prepare_core_calculation_inputs
from ecatvasp.vasp.potcar import LocalPotcarLibrary, ResolvedPotcarSet
from ecatvasp.vasp.recipes import (
    RECIPE_ADSORBATE_RELAX,
    RECIPE_CHARGE_DENSITY_STATIC,
    RECIPE_DOS_PREREQUISITE,
    RECIPE_FULL_FREQUENCY,
    RECIPE_GAS_FREQUENCY,
    RECIPE_GAS_RELAX,
    RECIPE_GROUND_STATE_STATIC,
    RECIPE_LOBSTER_PREREQUISITE,
    RECIPE_SELECTED_ATOM_FREQUENCY,
    RECIPE_SLAB_RELAX,
)
from ecatvasp.workflow.orchestration import (
    WorkflowExecutionSource,
    WorkflowOrchestrationAction,
)

_CORE_RECIPES = frozenset(
    {
        RECIPE_SLAB_RELAX,
        RECIPE_ADSORBATE_RELAX,
        RECIPE_GAS_RELAX,
        RECIPE_GROUND_STATE_STATIC,
    }
)
_FREQUENCY_RECIPES = frozenset(
    {
        RECIPE_SELECTED_ATOM_FREQUENCY,
        RECIPE_FULL_FREQUENCY,
        RECIPE_GAS_FREQUENCY,
    }
)
_ANALYSIS_RECIPES = frozenset(
    {
        RECIPE_DOS_PREREQUISITE,
        RECIPE_CHARGE_DENSITY_STATIC,
        RECIPE_LOBSTER_PREREQUISITE,
    }
)


@dataclass(frozen=True, slots=True)
class JobCenterExecutionPreparation:
    """Exact execution-ready plan derived from current durable scientific state."""

    calculation_id: CalculationId
    workflow_plan_id: WorkflowPlanId
    step_key: str
    plan: ExecutionPlan
    reused_input_artifacts: bool


@dataclass(frozen=True, slots=True)
class JobCenterSubmissionReceipt:
    """Durable receipt after CREATED -> STAGING -> QUEUED Slurm submission."""

    calculation_id: CalculationId
    attempt_id: ExecutionAttemptId
    remote_job_id: RemoteJobId
    attempt_status: ExecutionAttemptStatus
    scheduler_state: SchedulerState
    scheduler_job_id: str
    plan_hash: str


@dataclass(frozen=True, slots=True)
class JobCenterObservationReceipt:
    """One persisted scheduler observation without scientific convergence inference."""

    calculation_id: CalculationId
    attempt_id: ExecutionAttemptId
    remote_job_id: RemoteJobId
    attempt_status: ExecutionAttemptStatus
    scheduler_state: SchedulerState
    ionic_step: int | None
    electronic_iteration: int | None


@dataclass(frozen=True, slots=True)
class JobCenterRetrievalReceipt:
    """Persisted retrieval receipt; VASP parsing remains Block 5 scope."""

    calculation_id: CalculationId
    attempt_id: ExecutionAttemptId
    remote_job_id: RemoteJobId
    attempt_status: ExecutionAttemptStatus
    retrieved_artifact_ids: tuple[str, ...]
    retrieval_hash: str


class ProjectJobCenterApplicationService(ProjectApplicationService):
    """Durable application seam for HPC preparation, submission and observation."""

    def catalog(self) -> dict[str, object]:
        """Return execution state without collapsing scientific and scheduler status."""

        bundle = self.store.open()
        attempts_by_calculation: dict[CalculationId, list[ExecutionAttempt]] = {}
        for attempt in bundle.execution_attempts:
            attempts_by_calculation.setdefault(attempt.calculation_id, []).append(attempt)
        jobs_by_attempt: dict[ExecutionAttemptId, list[RemoteJob]] = {}
        for job in bundle.remote_jobs:
            jobs_by_attempt.setdefault(job.execution_attempt_id, []).append(job)

        calculations: list[dict[str, object]] = []
        for calculation in bundle.calculations:
            attempts = sorted(
                attempts_by_calculation.get(calculation.id, ()),
                key=lambda item: item.attempt_number,
            )
            latest = attempts[-1] if attempts else None
            jobs = (
                sorted(
                    jobs_by_attempt.get(latest.id, ()),
                    key=lambda item: str(item.id),
                )
                if latest is not None
                else []
            )
            latest_job = jobs[-1] if jobs else None
            calculations.append(
                {
                    "calculation_id": str(calculation.id),
                    "calculation_type": calculation.calculation_type.value,
                    "recipe_id": calculation.recipe_id,
                    "scientific_status": calculation.status.value,
                    "latest_attempt_id": str(latest.id) if latest is not None else None,
                    "attempt_status": latest.status.value if latest is not None else None,
                    "remote_job_id": str(latest_job.id) if latest_job is not None else None,
                    "scheduler_state": (
                        latest_job.state.value if latest_job is not None else None
                    ),
                    "scheduler_job_id": (
                        latest_job.scheduler_job_id if latest_job is not None else None
                    ),
                }
            )
        return {
            "project_id": str(bundle.project.id),
            "project_name": bundle.project.name,
            "calculations": calculations,
        }

    def prepare_execution(
        self,
        *,
        calculation_id: CalculationId,
        potcar_root: Path,
        numerical_evidence: WizardNumericalEvidence,
        execution_settings: ExecutionSettings,
        frequency_atom_uids: tuple[str, ...] = (),
    ) -> JobCenterExecutionPreparation:
        """Materialize verified VASP inputs and build one exact Slurm ExecutionPlan."""

        bundle = self.store.open()
        calculation = _require_calculation(bundle, calculation_id)
        fingerprint = _require_fingerprint(bundle, calculation)
        snapshot = _require_snapshot(bundle, calculation)
        workflow_plan, step_key = _require_current_workflow_binding(bundle, calculation)
        context = _resolve_system_context(snapshot, fingerprint)
        lock = _project_lock(
            bundle=bundle,
            fingerprint=fingerprint,
            context=context,
            evidence=numerical_evidence,
        )
        library = LocalPotcarLibrary(
            family=fingerprint.method.potcar_family,
            root=potcar_root,
        )
        materialized, resolved_potcars = _prepare_inputs(
            project_root=self.store.root,
            calculation=calculation,
            snapshot=snapshot,
            fingerprint=fingerprint,
            context=context,
            library=library,
            lock=lock,
            evidence=numerical_evidence,
            frequency_atom_uids=frequency_atom_uids,
        )
        persisted_materialized, reused = _persist_or_reuse_input_materialization(
            store=self.store,
            candidate=materialized,
        )

        portable_settings = replace(
            execution_settings,
            nodes=None,
            cores=None,
            memory_mb=None,
            walltime_seconds=None,
            partition=None,
        )
        portable_plan = build_execution_plan(
            project_root=self.store.root,
            calculation=calculation,
            fingerprint=fingerprint,
            system_context=context,
            materialized=persisted_materialized,
            resolved_potcars=resolved_potcars,
            execution_settings=portable_settings,
        )
        plan = replace(portable_plan, execution_settings=execution_settings)
        resolve_scheduler_resources(plan.execution_settings)
        return JobCenterExecutionPreparation(
            calculation_id=calculation.id,
            workflow_plan_id=workflow_plan.id,
            step_key=step_key,
            plan=plan,
            reused_input_artifacts=reused,
        )

    def submit_remote_slurm(
        self,
        *,
        preparation: JobCenterExecutionPreparation,
        target: ExecutionTargetProfile,
        remote_potcars: RemotePotcarLibrary,
        transport: TransportAdapter,
    ) -> JobCenterSubmissionReceipt:
        """Persist CREATED before SSH staging, then persist STAGING before sbatch."""

        if (
            target.transport is not TransportKind.SSH
            or target.scheduler is not SchedulerType.SLURM
        ):
            raise ApplicationServiceError(
                "Job Center submission requires an SSH+SLURM target"
            )
        bundle = self.store.open()
        calculation = _require_calculation(bundle, preparation.calculation_id)
        workflow_plan, current_binding = _require_current_workflow_binding_by_plan(
            bundle,
            calculation,
            preparation.workflow_plan_id,
            preparation.step_key,
        )
        source = WorkflowExecutionSource(
            step_key=preparation.step_key,
            binding=current_binding,
            calculation=calculation,
            plan=preparation.plan,
        )
        orchestration = build_current_workflow_orchestration(
            bundle,
            workflow_plan,
            execution_sources=(source,),
        )
        handoff = orchestration.step(preparation.step_key)
        if handoff.action is not WorkflowOrchestrationAction.EXECUTION_READY:
            raise ApplicationServiceError(
                "current workflow generation is not execution-ready: "
                f"{handoff.action.value}"
            )
        dispatch = self.run_workflow(
            workflow_plan_id=workflow_plan.id,
            orchestration=orchestration,
            concurrency=BatchConcurrencyPolicy(max_active=1),
        )
        tickets = tuple(
            item
            for item in dispatch.wave.tickets
            if item.calculation.id == calculation.id
        )
        if len(tickets) != 1:
            raise ApplicationServiceError(
                "workflow dispatch did not return exactly one current Calculation ticket"
            )
        ticket = tickets[0]
        if ticket.plan.plan_hash != preparation.plan.plan_hash:
            raise ApplicationServiceError(
                "dispatch ticket ExecutionPlan changed before staging"
            )

        staged = stage_remote_runtime(
            project_root=self.store.root,
            plan=preparation.plan,
            calculation=calculation,
            attempt=ticket.attempt,
            target=target,
            transport=transport,
            potcars=remote_potcars,
        )
        _persist_execution_phase(
            store=self.store,
            attempt=staged.attempt,
            artifacts=staged.artifacts,
        )
        persisted_attempt = _require_attempt(self.store.open(), staged.attempt.id)
        if persisted_attempt.status is not ExecutionAttemptStatus.STAGING:
            raise ApplicationServiceError(
                "staged ExecutionAttempt failed durable verification"
            )
        persisted_stage = replace(staged, attempt=persisted_attempt)
        scheduler = SlurmAdapter(transport)
        submitted = submit_remote_slurm(
            staged=persisted_stage,
            transport=transport,
            scheduler=scheduler,
        )
        _persist_execution_phase(
            store=self.store,
            attempt=submitted.attempt,
            artifacts=submitted.artifacts,
            remote_job=submitted.remote_job,
        )
        reopened = self.store.open()
        attempt = _require_attempt(reopened, submitted.attempt.id)
        remote_job = _require_remote_job(reopened, submitted.remote_job.id)
        if attempt.status is not ExecutionAttemptStatus.QUEUED:
            raise ApplicationServiceError(
                "submitted ExecutionAttempt failed durable verification"
            )
        return JobCenterSubmissionReceipt(
            calculation_id=calculation.id,
            attempt_id=attempt.id,
            remote_job_id=remote_job.id,
            attempt_status=attempt.status,
            scheduler_state=remote_job.state,
            scheduler_job_id=remote_job.scheduler_job_id,
            plan_hash=preparation.plan.plan_hash,
        )

    def refresh_job(
        self,
        *,
        remote_job_id: RemoteJobId,
        target: ExecutionTargetProfile,
        transport: TransportAdapter,
    ) -> JobCenterObservationReceipt:
        bundle = self.store.open()
        remote_job = _require_remote_job(bundle, remote_job_id)
        attempt = _require_attempt(bundle, remote_job.execution_attempt_id)
        calculation = _require_calculation(bundle, attempt.calculation_id)
        _require_attempt_target(self.store.root, bundle, attempt.id, target)
        package = monitor_remote_slurm(
            project_root=self.store.root,
            attempt=attempt,
            remote_job=remote_job,
            target=target,
            transport=transport,
            scheduler=SlurmAdapter(transport),
        )
        _persist_monitoring_phase(store=self.store, package=package)
        return _observation_receipt(calculation, package)

    def cancel_job(
        self,
        *,
        remote_job_id: RemoteJobId,
        target: ExecutionTargetProfile,
        transport: TransportAdapter,
    ) -> JobCenterObservationReceipt:
        bundle = self.store.open()
        remote_job = _require_remote_job(bundle, remote_job_id)
        attempt = _require_attempt(bundle, remote_job.execution_attempt_id)
        calculation = _require_calculation(bundle, attempt.calculation_id)
        _require_attempt_target(self.store.root, bundle, attempt.id, target)
        package = cancel_remote_slurm(
            project_root=self.store.root,
            attempt=attempt,
            remote_job=remote_job,
            target=target,
            transport=transport,
            scheduler=SlurmAdapter(transport),
        )
        _persist_monitoring_phase(store=self.store, package=package)
        return _observation_receipt(calculation, package)

    def retrieve_job_outputs(
        self,
        *,
        remote_job_id: RemoteJobId,
        target: ExecutionTargetProfile,
        transport: TransportAdapter,
        requested_roles: tuple[str, ...] = (),
        release_remote_roles: tuple[str, ...] = (),
        discard_remote_roles: tuple[str, ...] = (),
    ) -> JobCenterRetrievalReceipt:
        bundle = self.store.open()
        remote_job = _require_remote_job(bundle, remote_job_id)
        attempt = _require_attempt(bundle, remote_job.execution_attempt_id)
        calculation = _require_calculation(bundle, attempt.calculation_id)
        _require_attempt_target(self.store.root, bundle, attempt.id, target)
        plan = resolve_execution_plan(self.store.root, bundle, attempt.id)
        package = retrieve_remote_outputs(
            project_root=self.store.root,
            plan=plan,
            attempt=attempt,
            remote_job=remote_job,
            target=target,
            transport=transport,
            requested_roles=requested_roles,
            release_remote_roles=release_remote_roles,
            discard_remote_roles=discard_remote_roles,
        )
        _persist_retrieval_phase(store=self.store, package=package)
        persisted_attempt = _require_attempt(self.store.open(), attempt.id)
        return JobCenterRetrievalReceipt(
            calculation_id=calculation.id,
            attempt_id=persisted_attempt.id,
            remote_job_id=remote_job.id,
            attempt_status=persisted_attempt.status,
            retrieved_artifact_ids=tuple(str(item.id) for item in package.artifacts),
            retrieval_hash=package.manifest.retrieval_hash,
        )


def _prepare_inputs(
    *,
    project_root: Path,
    calculation: Calculation,
    snapshot: StructureSnapshot,
    fingerprint: MethodFingerprint,
    context: VaspSystemContext,
    library: LocalPotcarLibrary,
    lock: ProjectNumericalLock,
    evidence: WizardNumericalEvidence,
    frequency_atom_uids: tuple[str, ...],
) -> tuple[MaterializedInputSet, ResolvedPotcarSet]:
    recipe_id = calculation.recipe_id
    if recipe_id in _CORE_RECIPES:
        core_result = prepare_core_calculation_inputs(
            project_root=project_root,
            calculation=calculation,
            snapshot=snapshot,
            fingerprint=fingerprint,
            system_context=context,
            potcar_library=library,
            project_lock=lock,
            encut_evidence=evidence.encut,
            kpoint_evidence=evidence.kpoints,
        )
        return core_result.materialized, core_result.resolved_potcars

    if recipe_id in _FREQUENCY_RECIPES:
        selection: FrequencySelection | None = None
        if recipe_id == RECIPE_SELECTED_ATOM_FREQUENCY:
            if not frequency_atom_uids:
                raise ApplicationServiceError(
                    "selected-atom frequency execution requires exact atom UID selection"
                )
            try:
                selection = FrequencySelection(
                    tuple(AtomUid(UUID(value)) for value in frequency_atom_uids)
                )
            except ValueError as error:
                raise ApplicationServiceError(
                    "selected-atom frequency contains an invalid atom UID"
                ) from error
        elif frequency_atom_uids:
            raise ApplicationServiceError(
                "full/gas frequency execution must not carry selected atom UIDs"
            )
        frequency_result = prepare_frequency_calculation_inputs(
            project_root=project_root,
            calculation=calculation,
            snapshot=snapshot,
            fingerprint=fingerprint,
            system_context=context,
            potcar_library=library,
            project_lock=lock,
            encut_evidence=evidence.encut,
            kpoint_evidence=evidence.kpoints,
            selection=selection,
        )
        return frequency_result.materialized, frequency_result.resolved_potcars

    if recipe_id in _ANALYSIS_RECIPES:
        if frequency_atom_uids:
            raise ApplicationServiceError(
                "analysis prerequisite execution must not carry frequency atom UIDs"
            )
        analysis_result = prepare_analysis_prerequisite_inputs(
            project_root=project_root,
            calculation=calculation,
            snapshot=snapshot,
            fingerprint=fingerprint,
            system_context=context,
            potcar_library=library,
            project_lock=lock,
            encut_evidence=evidence.encut,
            kpoint_evidence=evidence.kpoints,
        )
        return analysis_result.materialized, analysis_result.resolved_potcars

    raise ApplicationServiceError(f"recipe is not supported by Job Center: {recipe_id}")


def _project_lock(
    *,
    bundle: ProjectBundle,
    fingerprint: MethodFingerprint,
    context: VaspSystemContext,
    evidence: WizardNumericalEvidence,
) -> ProjectNumericalLock:
    if evidence.encut.core_method_hash != fingerprint.core_method_hash:
        raise ApplicationServiceError(
            "ENCUT evidence does not match current MethodFingerprint"
        )
    if evidence.encut.selected_encut_ev != fingerprint.protocol.encut_ev:
        raise ApplicationServiceError(
            "ENCUT evidence does not match fingerprinted ENCUT"
        )
    if context.kind is not VaspSystemKind.MOLECULE_0D and evidence.kpoints is None:
        raise ApplicationServiceError(
            "solid execution requires validated k-point evidence"
        )
    if (
        evidence.kpoints is not None
        and evidence.kpoints.core_method_hash != fingerprint.core_method_hash
    ):
        raise ApplicationServiceError(
            "k-point evidence does not match current MethodFingerprint"
        )
    return ProjectNumericalLock(
        project_id=bundle.project.id,
        system_kind=context.kind,
        core_method_hash=fingerprint.core_method_hash,
        encut_ev=fingerprint.protocol.encut_ev,
        encut_validation_hash=evidence.encut.analysis_hash,
        kpoints=fingerprint.protocol.kpoints,
        kpoints_validation_hash=(
            evidence.kpoints.analysis_hash if evidence.kpoints is not None else None
        ),
    )


def _resolve_system_context(
    snapshot: StructureSnapshot,
    fingerprint: MethodFingerprint,
) -> VaspSystemContext:
    if snapshot.periodic == (False, False, False):
        return VaspSystemContext(VaspSystemKind.MOLECULE_0D)
    axes = tuple(
        item.value
        for item in fingerprint.protocol.extra_parameters
        if item.name == ECATVASP_DIPOLE_AXIS
    )
    if axes:
        if len(axes) != 1 or not isinstance(axes[0], str):
            raise ApplicationServiceError(
                "Protocol contains invalid slab vacuum-axis identity"
            )
        try:
            axis = LatticeAxis(axes[0])
        except ValueError as error:
            raise ApplicationServiceError(
                "Protocol contains an unsupported slab vacuum axis"
            ) from error
        return VaspSystemContext(VaspSystemKind.SLAB_2D, vacuum_axis=axis)
    if snapshot.periodic == (True, True, True):
        return VaspSystemContext(VaspSystemKind.PERIODIC_3D)
    raise ApplicationServiceError(
        "periodic Calculation lacks an explicit slab vacuum axis and is not fully 3D"
    )


def _persist_or_reuse_input_materialization(
    *,
    store: ProjectStore,
    candidate: MaterializedInputSet,
) -> tuple[MaterializedInputSet, bool]:
    bundle = store.open()
    candidate_paths = tuple(item.local_path for item in candidate.artifacts)
    if any(path is None for path in candidate_paths):
        raise ApplicationServiceError(
            "materialized VASP input artifact is missing local_path"
        )
    wanted = set(candidate_paths)
    existing = tuple(item for item in bundle.artifacts if item.local_path in wanted)
    if existing:
        if len(existing) != len(candidate.artifacts):
            raise ApplicationServiceError(
                "persisted VASP input artifact set is partial; refusing ambiguous reuse"
            )
        by_path = {item.local_path: item for item in existing}
        ordered: list[Artifact] = []
        for fresh in candidate.artifacts:
            persisted = by_path.get(fresh.local_path)
            if (
                persisted is None
                or persisted.artifact_type is not fresh.artifact_type
                or persisted.sha256 != fresh.sha256
                or persisted.size_bytes != fresh.size_bytes
                or persisted.producer != fresh.producer
            ):
                raise ApplicationServiceError(
                    "persisted VASP input artifacts drift from regenerated inputs"
                )
            ordered.append(persisted)
        return (
            MaterializedInputSet(
                calculation_id=candidate.calculation_id,
                input_directory=candidate.input_directory,
                manifest=candidate.manifest,
                artifacts=tuple(ordered),
                provenance_records=(),
                dependency_records=(),
            ),
            True,
        )

    known_ids = {getattr(item, "id", None) for item in bundle.entities()}
    new_entities = (
        *candidate.artifacts,
        *candidate.provenance_records,
        *candidate.dependency_records,
    )
    if any(getattr(item, "id", None) in known_ids for item in new_entities):
        raise ApplicationServiceError(
            "new VASP input provenance unexpectedly reuses an entity id"
        )
    store.save(
        replace(
            bundle,
            artifacts=(*bundle.artifacts, *candidate.artifacts),
            provenance_records=(
                *bundle.provenance_records,
                *candidate.provenance_records,
            ),
            dependency_records=(
                *bundle.dependency_records,
                *candidate.dependency_records,
            ),
        )
    )
    reopened = store.open()
    persisted_ids = {item.id for item in reopened.artifacts}
    if any(item.id not in persisted_ids for item in candidate.artifacts):
        raise ApplicationServiceError(
            "VASP input artifacts failed post-save verification"
        )
    return candidate, False


def _persist_execution_phase(
    *,
    store: ProjectStore,
    attempt: ExecutionAttempt,
    artifacts: tuple[Artifact, ...],
    remote_job: RemoteJob | None = None,
) -> None:
    bundle = store.open()
    current = _require_attempt(bundle, attempt.id)
    if current.calculation_id != attempt.calculation_id:
        raise ApplicationServiceError(
            "ExecutionAttempt changed Calculation during execution phase"
        )
    attempts = tuple(
        attempt if item.id == attempt.id else item
        for item in bundle.execution_attempts
    )
    known_artifact_ids = {item.id for item in bundle.artifacts}
    append_artifacts = tuple(
        item for item in artifacts if item.id not in known_artifact_ids
    )
    remote_jobs = bundle.remote_jobs
    if remote_job is not None:
        matches = tuple(item for item in remote_jobs if item.id == remote_job.id)
        if matches and matches != (remote_job,):
            raise ApplicationServiceError(
                "RemoteJob id collision during execution persistence"
            )
        if not matches:
            remote_jobs = (*remote_jobs, remote_job)
    store.save(
        replace(
            bundle,
            execution_attempts=attempts,
            remote_jobs=remote_jobs,
            artifacts=(*bundle.artifacts, *append_artifacts),
        )
    )
    reopened = store.open()
    if _require_attempt(reopened, attempt.id) != attempt:
        raise ApplicationServiceError(
            "ExecutionAttempt failed post-save verification"
        )
    for artifact in artifacts:
        persisted = tuple(item for item in reopened.artifacts if item.id == artifact.id)
        if persisted != (artifact,):
            raise ApplicationServiceError(
                "execution Artifact failed post-save verification"
            )
    if (
        remote_job is not None
        and _require_remote_job(reopened, remote_job.id) != remote_job
    ):
        raise ApplicationServiceError("RemoteJob failed post-save verification")


def _persist_monitoring_phase(
    *,
    store: ProjectStore,
    package: SlurmMonitoringPackage,
) -> None:
    bundle = store.open()
    _require_attempt(bundle, package.attempt.id)
    _require_remote_job(bundle, package.remote_job.id)
    attempts = tuple(
        package.attempt if item.id == package.attempt.id else item
        for item in bundle.execution_attempts
    )
    jobs = tuple(
        package.remote_job if item.id == package.remote_job.id else item
        for item in bundle.remote_jobs
    )
    if package.artifact.id in {item.id for item in bundle.artifacts}:
        raise ApplicationServiceError("scheduler observation Artifact id already exists")
    store.save(
        replace(
            bundle,
            execution_attempts=attempts,
            remote_jobs=jobs,
            artifacts=(*bundle.artifacts, package.artifact),
        )
    )
    reopened = store.open()
    if _require_attempt(reopened, package.attempt.id) != package.attempt:
        raise ApplicationServiceError(
            "monitored ExecutionAttempt failed post-save verification"
        )
    if _require_remote_job(reopened, package.remote_job.id) != package.remote_job:
        raise ApplicationServiceError(
            "monitored RemoteJob failed post-save verification"
        )


def _persist_retrieval_phase(
    *,
    store: ProjectStore,
    package: RemoteRetrievalPackage,
) -> None:
    bundle = store.open()
    _require_attempt(bundle, package.attempt.id)
    attempts = tuple(
        package.attempt if item.id == package.attempt.id else item
        for item in bundle.execution_attempts
    )
    known = {item.id for item in bundle.artifacts}
    if any(item.id in known for item in package.artifacts):
        raise ApplicationServiceError("retrieval Artifact id already exists")
    store.save(
        replace(
            bundle,
            execution_attempts=attempts,
            artifacts=(*bundle.artifacts, *package.artifacts),
        )
    )
    reopened = store.open()
    if _require_attempt(reopened, package.attempt.id) != package.attempt:
        raise ApplicationServiceError(
            "retrieval ExecutionAttempt failed post-save verification"
        )


def _require_attempt_target(
    project_root: Path,
    bundle: ProjectBundle,
    attempt_id: ExecutionAttemptId,
    target: ExecutionTargetProfile,
) -> None:
    manifests = tuple(
        item
        for item in bundle.artifacts
        if item.artifact_type is ArtifactType.REMOTE_STAGE_MANIFEST
        and item.producer == ExecutionAttemptProducerRef(attempt_id)
        and item.local_path is not None
    )
    if len(manifests) != 1:
        raise ApplicationServiceError(
            "ExecutionAttempt requires exactly one remote-stage manifest before remote control"
        )
    artifact = manifests[0]
    root = Path(project_root).resolve()
    assert artifact.local_path is not None
    path = (root / artifact.local_path).resolve()
    if root not in path.parents or not path.is_file():
        raise ApplicationServiceError(
            "remote-stage manifest is missing or escaped project root"
        )
    body = path.read_bytes()
    if artifact.sha256 != hashlib.sha256(body).hexdigest():
        raise ApplicationServiceError("remote-stage manifest SHA-256 drift detected")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ApplicationServiceError(
            "remote-stage manifest is invalid JSON"
        ) from error
    environment = payload.get("environment") if isinstance(payload, dict) else None
    if (
        not isinstance(environment, dict)
        or environment.get("target_hash") != target.target_hash
    ):
        raise ApplicationServiceError(
            "current execution target does not match staged target identity"
        )


def _observation_receipt(
    calculation: Calculation,
    package: SlurmMonitoringPackage,
) -> JobCenterObservationReceipt:
    return JobCenterObservationReceipt(
        calculation_id=calculation.id,
        attempt_id=package.attempt.id,
        remote_job_id=package.remote_job.id,
        attempt_status=package.attempt.status,
        scheduler_state=package.remote_job.state,
        ionic_step=package.progress.ionic_step,
        electronic_iteration=package.progress.electronic_iteration,
    )


def _require_calculation(
    bundle: ProjectBundle,
    calculation_id: CalculationId,
) -> Calculation:
    matches = tuple(item for item in bundle.calculations if item.id == calculation_id)
    if len(matches) != 1:
        raise ApplicationServiceError(
            "Calculation is absent or duplicated in current ProjectStore"
        )
    return matches[0]


def _require_fingerprint(
    bundle: ProjectBundle,
    calculation: Calculation,
) -> MethodFingerprint:
    matches = tuple(
        item
        for item in bundle.method_fingerprints
        if item.id == calculation.method_fingerprint_id
    )
    if len(matches) != 1:
        raise ApplicationServiceError(
            "Calculation MethodFingerprint is absent or duplicated"
        )
    return matches[0]


def _require_snapshot(
    bundle: ProjectBundle,
    calculation: Calculation,
) -> StructureSnapshot:
    matches = tuple(
        item
        for item in bundle.structure_snapshots
        if item.id == calculation.input_structure_snapshot_id
    )
    if len(matches) != 1:
        raise ApplicationServiceError(
            "Calculation input StructureSnapshot is absent or duplicated"
        )
    return matches[0]


def _require_current_workflow_binding(
    bundle: ProjectBundle,
    calculation: Calculation,
) -> tuple[ScientificWorkflowPlan, str]:
    bindings = tuple(
        item
        for item in bundle.workflow_step_bindings
        if item.calculation_id == calculation.id
    )
    if len(bindings) != 1:
        raise ApplicationServiceError(
            "Job Center Calculation requires exactly one workflow binding"
        )
    binding = bindings[0]
    plans = tuple(
        item for item in bundle.workflow_plans if item.id == binding.workflow_plan_id
    )
    if len(plans) != 1:
        raise ApplicationServiceError("workflow binding references absent or duplicated plan")
    plan = plans[0]
    same_step = tuple(
        item
        for item in bundle.workflow_step_bindings
        if item.workflow_plan_id == plan.id and item.step_key == binding.step_key
    )
    current = max(same_step, key=lambda item: item.generation)
    if current.id != binding.id:
        raise ApplicationServiceError(
            "Job Center refuses a superseded workflow Calculation"
        )
    return plan, binding.step_key


def _require_current_workflow_binding_by_plan(
    bundle: ProjectBundle,
    calculation: Calculation,
    workflow_plan_id: WorkflowPlanId,
    step_key: str,
) -> tuple[ScientificWorkflowPlan, WorkflowStepBinding]:
    plans = tuple(item for item in bundle.workflow_plans if item.id == workflow_plan_id)
    if len(plans) != 1:
        raise ApplicationServiceError(
            "workflow plan is absent or duplicated in current ProjectStore"
        )
    plan = plans[0]
    bindings = tuple(
        item
        for item in bundle.workflow_step_bindings
        if item.workflow_plan_id == plan.id and item.step_key == step_key
    )
    if not bindings:
        raise ApplicationServiceError("workflow step has no durable binding")
    current = max(bindings, key=lambda item: item.generation)
    if current.calculation_id != calculation.id:
        raise ApplicationServiceError(
            "requested Calculation is not the current workflow generation"
        )
    return plan, current


def _require_attempt(
    bundle: ProjectBundle,
    attempt_id: ExecutionAttemptId,
) -> ExecutionAttempt:
    matches = tuple(item for item in bundle.execution_attempts if item.id == attempt_id)
    if len(matches) != 1:
        raise ApplicationServiceError("ExecutionAttempt is absent or duplicated")
    return matches[0]


def _require_remote_job(
    bundle: ProjectBundle,
    remote_job_id: RemoteJobId,
) -> RemoteJob:
    matches = tuple(item for item in bundle.remote_jobs if item.id == remote_job_id)
    if len(matches) != 1:
        raise ApplicationServiceError("RemoteJob is absent or duplicated")
    return matches[0]


__all__ = [
    "JobCenterExecutionPreparation",
    "JobCenterObservationReceipt",
    "JobCenterRetrievalReceipt",
    "JobCenterSubmissionReceipt",
    "ProjectJobCenterApplicationService",
]

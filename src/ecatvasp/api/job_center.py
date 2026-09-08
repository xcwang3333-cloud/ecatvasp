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
from ecatvasp.api.workflow_runtime import build_current_workflow_orchestration
from ecatvasp.domain import (
    Artifact,
    ArtifactAvailability,
    ArtifactType,
    Calculation,
    CalculationId,
    ExecutionAttempt,
    ExecutionAttemptId,
    ExecutionAttemptProducerRef,
    ExecutionAttemptStatus,
    ExecutionSettings,
    MethodFingerprint,
    ParameterEntry,
    RemoteJob,
    RemoteJobId,
    SchedulerState,
    SchedulerType,
    StructureSnapshot,
    WorkflowPlanId,
)
from ecatvasp.execution.adapters import TransportAdapter
from ecatvasp.execution.batch import BatchConcurrencyPolicy
from ecatvasp.execution.monitoring import (
    SlurmMonitoringPackage,
    cancel_remote_slurm,
    monitor_remote_slurm,
)
from ecatvasp.execution.remote import (
    RemotePotcarLibrary,
    RemoteStagePackage,
    stage_remote_runtime,
)
from ecatvasp.execution.retrieval import RemoteRetrievalPackage, retrieve_remote_outputs
from ecatvasp.execution.slurm import SlurmAdapter, SlurmSubmissionPackage, submit_remote_slurm
from ecatvasp.execution.targets import ExecutionTargetProfile, TransportKind
from ecatvasp.storage import ProjectBundle, ProjectStore
from ecatvasp.vasp.analysis_pipeline import prepare_analysis_prerequisite_inputs
from ecatvasp.vasp.contracts import LatticeAxis, ProjectNumericalLock, VaspSystemContext, VaspSystemKind
from ecatvasp.vasp.execution_plan import (
    ExecutionPlan,
    ExpectedOutput,
    PotcarResolutionEntry,
    PotcarResolutionRequest,
    StagingInput,
    StagingInputKind,
    VaspRuntimeCapability,
    VaspRuntimeConstraints,
    build_execution_plan,
)
from ecatvasp.vasp.frequency import FrequencySelection
from ecatvasp.vasp.frequency_pipeline import prepare_frequency_calculation_inputs
from ecatvasp.vasp.incar import ECATVASP_DIPOLE_AXIS
from ecatvasp.vasp.kpoints import KPointValidationEvidence
from ecatvasp.vasp.materialization import MaterializedInputSet
from ecatvasp.vasp.pipeline import prepare_core_calculation_inputs
from ecatvasp.vasp.potcar import EncCutValidationEvidence, LocalPotcarLibrary, ResolvedPotcarSet
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
from ecatvasp.workflow.orchestration import WorkflowExecutionSource, WorkflowOrchestrationAction

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
        """Return task-oriented execution state while preserving lifecycle namespaces."""

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
                    "scheduler_state": latest_job.state.value if latest_job is not None else None,
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
        """Materialize verified VASP inputs and build one exact Slurm ExecutionPlan.

        Scheduler resources remain execution-only. The scientific input pipeline is
        selected solely from the current persisted Calculation recipe.
        """

        bundle = self.store.open()
        calculation = _require_calculation(bundle, calculation_id)
        fingerprint = _require_fingerprint(bundle, calculation)
        snapshot = _require_snapshot(bundle, calculation)
        plan_record, step_key = _require_current_workflow_binding(bundle, calculation)
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

        # v0.3 build_execution_plan deliberately rejected scheduler resource
        # placement. Build its portable scientific handoff first, then add exact
        # v0.4 execution-only resources as a new immutable plan value.
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
        # Resource resolution is an execution-only validation gate. It performs
        # no scheduler side effect.
        from ecatvasp.execution.slurm import resolve_scheduler_resources

        resolve_scheduler_resources(plan.execution_settings)
        return JobCenterExecutionPreparation(
            calculation_id=calculation.id,
            workflow_plan_id=plan_record.id,
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

        if target.transport is not TransportKind.SSH or target.scheduler is not SchedulerType.SLURM:
            raise ApplicationServiceError("Job Center submission requires an SSH+SLURM target")
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
                f"current workflow generation is not execution-ready: {handoff.action.value}"
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
            raise ApplicationServiceError("dispatch ticket ExecutionPlan changed before staging")

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
            raise ApplicationServiceError("staged ExecutionAttempt failed durable verification")
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
            raise ApplicationServiceError("submitted ExecutionAttempt failed durable verification")
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
        """Observe Slurm once and persist execution/scheduler truth separately."""

        bundle = self.store.open()
        remote_job = _require_remote_job(bundle, remote_job_id)
        attempt = _require_attempt(bundle, remote_job.execution_attempt_id)
        calculation = _require_calculation(bundle, attempt.calculation_id)
        _require_attempt_target(self.store.root, bundle, attempt.id, target)
        scheduler = SlurmAdapter(transport)
        package = monitor_remote_slurm(
            project_root=self.store.root,
            attempt=attempt,
            remote_job=remote_job,
            target=target,
            transport=transport,
            scheduler=scheduler,
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
        """Request cancellation and persist only the scheduler state actually observed."""

        bundle = self.store.open()
        remote_job = _require_remote_job(bundle, remote_job_id)
        attempt = _require_attempt(bundle, remote_job.execution_attempt_id)
        calculation = _require_calculation(bundle, attempt.calculation_id)
        _require_attempt_target(self.store.root, bundle, attempt.id, target)
        scheduler = SlurmAdapter(transport)
        package = cancel_remote_slurm(
            project_root=self.store.root,
            attempt=attempt,
            remote_job=remote_job,
            target=target,
            transport=transport,
            scheduler=scheduler,
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
        """Retrieve exact expected outputs using the durable attempt ExecutionPlan artifact."""

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
        reopened = self.store.open()
        persisted_attempt = _require_attempt(reopened, attempt.id)
        return JobCenterRetrievalReceipt(
            calculation_id=calculation.id,
            attempt_id=persisted_attempt.id,
            remote_job_id=remote_job.id,
            attempt_status=persisted_attempt.status,
            retrieved_artifact_ids=tuple(str(item.id) for item in package.artifacts),
            retrieval_hash=package.manifest.retrieval_hash,
        )


def resolve_execution_plan(
    project_root: Path | str,
    bundle: ProjectBundle,
    attempt_id: ExecutionAttemptId,
) -> ExecutionPlan:
    """Load and verify the exact managed execution-plan.json for one attempt."""

    attempt = _require_attempt(bundle, attempt_id)
    matches = tuple(
        item
        for item in bundle.artifacts
        if item.artifact_type is ArtifactType.EXECUTION_PLAN
        and item.producer == ExecutionAttemptProducerRef(attempt.id)
        and item.local_path is not None
        and item.availability in {ArtifactAvailability.LOCAL, ArtifactAvailability.BOTH}
    )
    if len(matches) != 1:
        raise ApplicationServiceError(
            "ExecutionAttempt requires exactly one durable local ExecutionPlan artifact"
        )
    artifact = matches[0]
    root = Path(project_root).resolve()
    path = (root / artifact.local_path).resolve()
    if root not in path.parents or not path.is_file():
        raise ApplicationServiceError("ExecutionPlan artifact path is missing or escaped project root")
    body = path.read_bytes()
    digest = hashlib.sha256(body).hexdigest()
    if artifact.sha256 != digest:
        raise ApplicationServiceError("ExecutionPlan artifact SHA-256 drift detected")
    try:
        raw = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ApplicationServiceError("ExecutionPlan artifact is not valid UTF-8 JSON") from error
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ApplicationServiceError("ExecutionPlan artifact has unsupported schema")
    plan_raw = raw.get("plan")
    if not isinstance(plan_raw, dict):
        raise ApplicationServiceError("ExecutionPlan artifact is missing plan payload")
    plan = _decode_execution_plan(plan_raw)
    if raw.get("plan_hash") != plan.plan_hash:
        raise ApplicationServiceError("ExecutionPlan payload hash does not match artifact envelope")
    if attempt.execution_plan_hash != plan.plan_hash:
        raise ApplicationServiceError("ExecutionPlan artifact does not match durable ExecutionAttempt")
    if attempt.input_manifest_hash != plan.input_manifest_sha256:
        raise ApplicationServiceError("ExecutionPlan input manifest does not match durable attempt")
    return plan


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
        result = prepare_core_calculation_inputs(
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
        return result.materialized, result.resolved_potcars
    if recipe_id in _FREQUENCY_RECIPES:
        selection = None
        if recipe_id == RECIPE_SELECTED_ATOM_FREQUENCY:
            if not frequency_atom_uids:
                raise ApplicationServiceError(
                    "selected-atom frequency execution requires the exact atom UID selection"
                )
            from ecatvasp.domain import AtomUid

            selection = FrequencySelection(tuple(AtomUid(UUID(value)) for value in frequency_atom_uids))
        elif frequency_atom_uids:
            raise ApplicationServiceError(
                "full/gas frequency execution must not carry selected atom UIDs"
            )
        result = prepare_frequency_calculation_inputs(
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
        return result.materialized, result.resolved_potcars
    if recipe_id in _ANALYSIS_RECIPES:
        if frequency_atom_uids:
            raise ApplicationServiceError(
                "analysis prerequisite execution must not carry frequency atom UIDs"
            )
        result = prepare_analysis_prerequisite_inputs(
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
        return result.materialized, result.resolved_potcars
    raise ApplicationServiceError(f"recipe is not supported by Job Center: {recipe_id}")


def _project_lock(
    *,
    bundle: ProjectBundle,
    fingerprint: MethodFingerprint,
    context: VaspSystemContext,
    evidence: WizardNumericalEvidence,
) -> ProjectNumericalLock:
    if evidence.encut.core_method_hash != fingerprint.core_method_hash:
        raise ApplicationServiceError("ENCUT evidence does not match current MethodFingerprint")
    if evidence.encut.selected_encut_ev != fingerprint.protocol.encut_ev:
        raise ApplicationServiceError("ENCUT evidence does not match fingerprinted ENCUT")
    if context.kind is not VaspSystemKind.MOLECULE_0D and evidence.kpoints is None:
        raise ApplicationServiceError("solid execution requires validated k-point evidence")
    if evidence.kpoints is not None and evidence.kpoints.core_method_hash != fingerprint.core_method_hash:
        raise ApplicationServiceError("k-point evidence does not match current MethodFingerprint")
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
            raise ApplicationServiceError("Protocol contains invalid slab vacuum-axis identity")
        return VaspSystemContext(
            VaspSystemKind.SLAB_2D,
            vacuum_axis=LatticeAxis(axes[0]),
        )
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
        raise ApplicationServiceError("materialized VASP input artifact is missing local_path")
    wanted = set(candidate_paths)
    existing = tuple(
        item
        for item in bundle.artifacts
        if item.local_path in wanted
    )
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
                    "persisted VASP input artifacts drift from regenerated scientific inputs"
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

    known_ids = {item.id for item in bundle.entities}
    new_entities = (
        *candidate.artifacts,
        *candidate.provenance_records,
        *candidate.dependency_records,
    )
    if any(getattr(item, "id", None) in known_ids for item in new_entities):
        raise ApplicationServiceError("new VASP input provenance unexpectedly reuses an entity id")
    store.save(
        replace(
            bundle,
            artifacts=(*bundle.artifacts, *candidate.artifacts),
            provenance_records=(*bundle.provenance_records, *candidate.provenance_records),
            dependency_records=(*bundle.dependency_records, *candidate.dependency_records),
        )
    )
    reopened = store.open()
    persisted_ids = {item.id for item in reopened.artifacts}
    if any(item.id not in persisted_ids for item in candidate.artifacts):
        raise ApplicationServiceError("VASP input artifacts failed post-save verification")
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
    attempts = tuple(attempt if item.id == attempt.id else item for item in bundle.execution_attempts)
    if current.calculation_id != attempt.calculation_id:
        raise ApplicationServiceError("ExecutionAttempt changed Calculation during execution phase")
    known_artifact_ids = {item.id for item in bundle.artifacts}
    append_artifacts = tuple(item for item in artifacts if item.id not in known_artifact_ids)
    remote_jobs = bundle.remote_jobs
    if remote_job is not None:
        matches = tuple(item for item in remote_jobs if item.id == remote_job.id)
        if matches and matches != (remote_job,):
            raise ApplicationServiceError("RemoteJob id collision during execution persistence")
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
        raise ApplicationServiceError("ExecutionAttempt failed post-save verification")
    for artifact in artifacts:
        persisted = tuple(item for item in reopened.artifacts if item.id == artifact.id)
        if persisted != (artifact,):
            raise ApplicationServiceError("execution Artifact failed post-save verification")
    if remote_job is not None and _require_remote_job(reopened, remote_job.id) != remote_job:
        raise ApplicationServiceError("RemoteJob failed post-save verification")


def _persist_monitoring_phase(*, store: ProjectStore, package: SlurmMonitoringPackage) -> None:
    bundle = store.open()
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
        raise ApplicationServiceError("monitored ExecutionAttempt failed post-save verification")
    if _require_remote_job(reopened, package.remote_job.id) != package.remote_job:
        raise ApplicationServiceError("monitored RemoteJob failed post-save verification")


def _persist_retrieval_phase(*, store: ProjectStore, package: RemoteRetrievalPackage) -> None:
    bundle = store.open()
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
        raise ApplicationServiceError("retrieval ExecutionAttempt failed post-save verification")


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
    path = (Path(project_root).resolve() / artifact.local_path).resolve()
    root = Path(project_root).resolve()
    if root not in path.parents or not path.is_file():
        raise ApplicationServiceError("remote-stage manifest is missing or escaped project root")
    body = path.read_bytes()
    if artifact.sha256 != hashlib.sha256(body).hexdigest():
        raise ApplicationServiceError("remote-stage manifest SHA-256 drift detected")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ApplicationServiceError("remote-stage manifest is invalid JSON") from error
    environment = payload.get("environment") if isinstance(payload, dict) else None
    if not isinstance(environment, dict) or environment.get("target_hash") != target.target_hash:
        raise ApplicationServiceError("current execution target does not match staged target identity")


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


def _require_calculation(bundle: ProjectBundle, calculation_id: CalculationId) -> Calculation:
    matches = tuple(item for item in bundle.calculations if item.id == calculation_id)
    if len(matches) != 1:
        raise ApplicationServiceError("Calculation is absent or duplicated in current ProjectStore")
    return matches[0]


def _require_fingerprint(bundle: ProjectBundle, calculation: Calculation) -> MethodFingerprint:
    matches = tuple(
        item for item in bundle.method_fingerprints if item.id == calculation.method_fingerprint_id
    )
    if len(matches) != 1:
        raise ApplicationServiceError("Calculation MethodFingerprint is absent or duplicated")
    return matches[0]


def _require_snapshot(bundle: ProjectBundle, calculation: Calculation) -> StructureSnapshot:
    matches = tuple(
        item
        for item in bundle.structure_snapshots
        if item.id == calculation.input_structure_snapshot_id
    )
    if len(matches) != 1:
        raise ApplicationServiceError("Calculation input StructureSnapshot is absent or duplicated")
    return matches[0]


def _require_current_workflow_binding(
    bundle: ProjectBundle,
    calculation: Calculation,
) -> tuple[object, str]:
    bindings = tuple(
        item for item in bundle.workflow_step_bindings if item.calculation_id == calculation.id
    )
    if len(bindings) != 1:
        raise ApplicationServiceError("Job Center Calculation requires exactly one workflow binding")
    binding = bindings[0]
    plan = next(
        (item for item in bundle.workflow_plans if item.id == binding.workflow_plan_id),
        None,
    )
    if plan is None:
        raise ApplicationServiceError("workflow binding references missing plan")
    current = max(
        (
            item
            for item in bundle.workflow_step_bindings
            if item.workflow_plan_id == plan.id and item.step_key == binding.step_key
        ),
        key=lambda item: item.generation,
    )
    if current.id != binding.id:
        raise ApplicationServiceError("Job Center refuses a superseded workflow Calculation")
    return plan, binding.step_key


def _require_current_workflow_binding_by_plan(
    bundle: ProjectBundle,
    calculation: Calculation,
    workflow_plan_id: WorkflowPlanId,
    step_key: str,
):
    plan = next((item for item in bundle.workflow_plans if item.id == workflow_plan_id), None)
    if plan is None:
        raise ApplicationServiceError("workflow plan is absent from current ProjectStore")
    bindings = tuple(
        item
        for item in bundle.workflow_step_bindings
        if item.workflow_plan_id == plan.id and item.step_key == step_key
    )
    if not bindings:
        raise ApplicationServiceError("workflow step has no durable binding")
    current = max(bindings, key=lambda item: item.generation)
    if current.calculation_id != calculation.id:
        raise ApplicationServiceError("requested Calculation is not the current workflow generation")
    return plan, current


def _require_attempt(bundle: ProjectBundle, attempt_id: ExecutionAttemptId) -> ExecutionAttempt:
    matches = tuple(item for item in bundle.execution_attempts if item.id == attempt_id)
    if len(matches) != 1:
        raise ApplicationServiceError("ExecutionAttempt is absent or duplicated")
    return matches[0]


def _require_remote_job(bundle: ProjectBundle, remote_job_id: RemoteJobId) -> RemoteJob:
    matches = tuple(item for item in bundle.remote_jobs if item.id == remote_job_id)
    if len(matches) != 1:
        raise ApplicationServiceError("RemoteJob is absent or duplicated")
    return matches[0]


def _decode_execution_plan(raw: dict[str, object]) -> ExecutionPlan:
    try:
        context_raw = _mapping(raw, "system_context")
        vacuum = context_raw.get("vacuum_axis")
        context = VaspSystemContext(
            VaspSystemKind(_string(context_raw, "kind")),
            vacuum_axis=LatticeAxis(vacuum) if isinstance(vacuum, str) else None,
        )
        staging = tuple(
            StagingInput(
                role=_string(item, "role"),
                kind=StagingInputKind(_string(item, "kind")),
                artifact_id=UUID(_string(item, "artifact_id")),
                artifact_type=ArtifactType(_string(item, "artifact_type")),
                source_relative_path=_string(item, "source_relative_path"),
                target_relative_path=_string(item, "target_relative_path"),
                sha256=_string(item, "sha256"),
                size_bytes=_integer(item, "size_bytes"),
            )
            for item in _mapping_list(raw, "staging_inputs")
        )
        potcar_raw = _mapping(raw, "potcar_resolution")
        potcars = PotcarResolutionRequest(
            family=_string(potcar_raw, "family"),
            core_method_hash=_string(potcar_raw, "core_method_hash"),
            metadata_hash=_string(potcar_raw, "metadata_hash"),
            entries=tuple(
                PotcarResolutionEntry(
                    element=_string(item, "element"),
                    symbol=_string(item, "symbol"),
                    sha256=_string(item, "sha256"),
                )
                for item in _mapping_list(potcar_raw, "entries")
            ),
            target_relative_path=_string(potcar_raw, "target_relative_path"),
        )
        outputs = tuple(
            ExpectedOutput(
                role=_string(item, "role"),
                artifact_type=ArtifactType(_string(item, "artifact_type")),
                relative_path=_string(item, "relative_path"),
                retrieval_policy=_retrieval_policy(_string(item, "retrieval_policy")),
                required=_boolean(item, "required"),
            )
            for item in _mapping_list(raw, "expected_outputs")
        )
        constraints_raw = _mapping(raw, "runtime_constraints")
        capabilities = constraints_raw.get("required_capabilities")
        if not isinstance(capabilities, list) or any(not isinstance(item, str) for item in capabilities):
            raise ValueError("required_capabilities must be a string list")
        constraints = VaspRuntimeConstraints(
            required_version=(
                constraints_raw.get("required_version")
                if isinstance(constraints_raw.get("required_version"), str)
                else None
            ),
            required_capabilities=tuple(VaspRuntimeCapability(item) for item in capabilities),
        )
        settings_raw = _mapping(raw, "execution_settings")
        extras = settings_raw.get("extra_parameters")
        if not isinstance(extras, list):
            raise ValueError("execution extra_parameters must be a list")
        settings = ExecutionSettings(
            ncore=_optional_integer(settings_raw, "ncore"),
            kpar=_optional_integer(settings_raw, "kpar"),
            nodes=_optional_integer(settings_raw, "nodes"),
            cores=_optional_integer(settings_raw, "cores"),
            memory_mb=_optional_integer(settings_raw, "memory_mb"),
            walltime_seconds=_optional_integer(settings_raw, "walltime_seconds"),
            partition=_optional_string(settings_raw, "partition"),
            mpi_ranks=_optional_integer(settings_raw, "mpi_ranks"),
            omp_threads=_optional_integer(settings_raw, "omp_threads"),
            executable=_string(settings_raw, "executable"),
            extra_parameters=tuple(
                ParameterEntry(_string(item, "name"), item.get("value"))
                for item in extras
                if isinstance(item, dict)
            ),
        )
        plan = ExecutionPlan(
            calculation_id=UUID(_string(raw, "calculation_id")),
            recipe_id=_string(raw, "recipe_id"),
            system_context=context,
            input_manifest_artifact_id=UUID(_string(raw, "input_manifest_artifact_id")),
            input_manifest_sha256=_string(raw, "input_manifest_sha256"),
            preparation_hash=_string(raw, "preparation_hash"),
            staging_inputs=staging,
            potcar_resolution=potcars,
            expected_outputs=outputs,
            runtime_constraints=constraints,
            execution_settings=settings,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ApplicationServiceError("ExecutionPlan artifact payload is invalid") from error
    return plan


def _retrieval_policy(value: str):
    from ecatvasp.domain import RetrievalPolicy

    return RetrievalPolicy(value)


def _mapping(raw: dict[str, object], key: str) -> dict[str, object]:
    value = raw.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be an object")
    return value


def _mapping_list(raw: dict[str, object], key: str) -> tuple[dict[str, object], ...]:
    value = raw.get(key)
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError(f"{key} must be a list of objects")
    return tuple(value)  # type: ignore[arg-type]


def _string(raw: dict[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _optional_string(raw: dict[str, object], key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be null or a non-empty string")
    return value


def _integer(raw: dict[str, object], key: str) -> int:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    return value


def _optional_integer(raw: dict[str, object], key: str) -> int | None:
    value = raw.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be null or an integer")
    return value


def _boolean(raw: dict[str, object], key: str) -> bool:
    value = raw.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    return value


__all__ = [
    "JobCenterExecutionPreparation",
    "JobCenterObservationReceipt",
    "JobCenterRetrievalReceipt",
    "JobCenterSubmissionReceipt",
    "ProjectJobCenterApplicationService",
    "resolve_execution_plan",
]

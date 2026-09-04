"""Durable reopen/resume and idempotency helpers for v0.6 Block 8."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from ecatvasp.domain import (
    Calculation,
    ExecutionAttempt,
    ExecutionAttemptStatus,
    MethodFingerprint,
    RemoteJob,
    ScientificWorkflowPlan,
    StructureSnapshot,
    WorkflowStepBinding,
    canonical_sha256,
)
from ecatvasp.domain.ids import CalculationId, ExecutionAttemptId, WorkflowPlanId
from ecatvasp.execution.batch import (
    BatchConcurrencyPolicy,
    BatchDispatchWave,
    prepare_batch_dispatch_wave,
)
from ecatvasp.execution.recovery import RecoveryDecision
from ecatvasp.storage import ProjectBundle, ProjectStore
from ecatvasp.vasp.contracts import ProjectNumericalLock, VaspSystemContext
from ecatvasp.workflow.materialization import (
    AcceptedStructureSource,
    WorkflowStepMaterialization,
    materialize_workflow_step,
)
from ecatvasp.workflow.orchestration import (
    WorkflowOrchestrationAction,
    WorkflowOrchestrationEvaluation,
    WorkflowStepOrchestration,
)
from ecatvasp.workflow.planning import WorkflowPlanningResult

_RECOVERY_SOURCE_TERMINAL_STATUSES = frozenset(
    {
        ExecutionAttemptStatus.EXITED,
        ExecutionAttemptStatus.PARSED,
        ExecutionAttemptStatus.FAILED,
        ExecutionAttemptStatus.CANCELLED,
    }
)


class WorkflowDurabilityError(ValueError):
    """Raised when durable workflow replay cannot be resolved without guessing."""


@dataclass(frozen=True, slots=True)
class WorkflowResumeState:
    """Exact durable workflow facts reconstructed from one reopened ProjectStore."""

    plan: ScientificWorkflowPlan
    bindings: tuple[WorkflowStepBinding, ...]
    calculations: tuple[Calculation, ...]
    execution_attempts: tuple[ExecutionAttempt, ...]
    remote_jobs: tuple[RemoteJob, ...]
    resume_hash: str = field(init=False)

    def __post_init__(self) -> None:
        bindings = tuple(
            sorted(
                self.bindings,
                key=lambda item: (item.step_key, item.generation, str(item.id)),
            )
        )
        calculations = tuple(sorted(self.calculations, key=lambda item: str(item.id)))
        attempts = tuple(
            sorted(
                self.execution_attempts,
                key=lambda item: (
                    str(item.calculation_id),
                    item.attempt_number,
                    str(item.id),
                ),
            )
        )
        jobs = tuple(
            sorted(
                self.remote_jobs,
                key=lambda item: (str(item.execution_attempt_id), str(item.id)),
            )
        )
        binding_calculation_ids = {item.calculation_id for item in bindings}
        if {item.id for item in calculations} != binding_calculation_ids:
            raise WorkflowDurabilityError(
                "workflow resume calculations must match bound workflow Calculations exactly"
            )
        calculation_ids = {item.id for item in calculations}
        if any(item.calculation_id not in calculation_ids for item in attempts):
            raise WorkflowDurabilityError(
                "workflow resume attempt belongs to a Calculation outside the workflow"
            )
        attempt_ids = {item.id for item in attempts}
        if any(item.execution_attempt_id not in attempt_ids for item in jobs):
            raise WorkflowDurabilityError(
                "workflow resume RemoteJob belongs to an attempt outside the workflow"
            )
        object.__setattr__(self, "bindings", bindings)
        object.__setattr__(self, "calculations", calculations)
        object.__setattr__(self, "execution_attempts", attempts)
        object.__setattr__(self, "remote_jobs", jobs)
        object.__setattr__(
            self,
            "resume_hash",
            canonical_sha256(
                {
                    "workflow_plan_id": self.plan.id,
                    "plan_hash": self.plan.plan_hash,
                    "bindings": tuple(item.binding_hash for item in bindings),
                    "calculations": calculations,
                    "execution_attempts": attempts,
                    "remote_jobs": jobs,
                }
            ),
        )

    def current_binding(self, step_key: str) -> WorkflowStepBinding | None:
        """Return the highest persisted generation for one logical step."""

        values = tuple(item for item in self.bindings if item.step_key == step_key)
        if not values:
            return None
        return max(values, key=lambda item: item.generation)

    def calculation(self, calculation_id: CalculationId) -> Calculation:
        """Resolve one workflow-bound Calculation."""

        for item in self.calculations:
            if item.id == calculation_id:
                return item
        raise KeyError(calculation_id)

    def attempt(self, attempt_id: ExecutionAttemptId) -> ExecutionAttempt:
        """Resolve one persisted execution attempt inside this workflow."""

        for item in self.execution_attempts:
            if item.id == attempt_id:
                return item
        raise KeyError(attempt_id)


@dataclass(frozen=True, slots=True)
class WorkflowPlanPersistenceResult:
    """Durable plan receipt returned after ProjectStore verification."""

    plan: ScientificWorkflowPlan
    planning_hash: str
    reused: bool
    resume_state: WorkflowResumeState


@dataclass(frozen=True, slots=True)
class WorkflowMaterializationPersistenceResult:
    """Durable Calculation/binding receipt for one materialization generation."""

    materialization: WorkflowStepMaterialization
    reused: bool
    resume_state: WorkflowResumeState


@dataclass(frozen=True, slots=True)
class WorkflowRecoveryAttemptSource:
    """Exact source attempt whose recovery decision may allocate one direct successor."""

    step_key: str
    source_attempt_id: ExecutionAttemptId

    def __post_init__(self) -> None:
        if not self.step_key.strip():
            raise WorkflowDurabilityError(
                "recovery attempt source requires a non-blank step_key"
            )


@dataclass(frozen=True, slots=True)
class WorkflowDispatchPersistenceResult:
    """Post-persistence scheduler wave safe to expose to execution side effects."""

    wave: BatchDispatchWave
    newly_persisted_attempt_ids: tuple[ExecutionAttemptId, ...]
    resume_state: WorkflowResumeState


def reopen_workflow_resume_state(
    *,
    store: ProjectStore,
    workflow_plan_id: WorkflowPlanId,
) -> WorkflowResumeState:
    """Reopen one workflow entirely from durable ProjectStore facts."""

    bundle = store.open()
    plan = _require_plan(bundle=bundle, workflow_plan_id=workflow_plan_id)
    bindings = tuple(
        item
        for item in bundle.workflow_step_bindings
        if item.workflow_plan_id == plan.id
    )
    calculation_ids = {item.calculation_id for item in bindings}
    calculations = tuple(
        item for item in bundle.calculations if item.id in calculation_ids
    )
    attempt_values = tuple(
        item
        for item in bundle.execution_attempts
        if item.calculation_id in calculation_ids
    )
    attempt_ids = {item.id for item in attempt_values}
    job_values = tuple(
        item for item in bundle.remote_jobs if item.execution_attempt_id in attempt_ids
    )
    return WorkflowResumeState(
        plan=plan,
        bindings=bindings,
        calculations=calculations,
        execution_attempts=attempt_values,
        remote_jobs=job_values,
    )


def persist_or_reuse_workflow_plan(
    *,
    store: ProjectStore,
    planning: WorkflowPlanningResult,
) -> WorkflowPlanPersistenceResult:
    """Persist canonical workflow intent once and reuse it by exact ``plan_hash`` after reopen."""

    bundle = store.open()
    candidate = planning.plan
    if candidate.project_id != bundle.project.id:
        raise WorkflowDurabilityError("workflow plan candidate belongs to another Project")
    root_is_persisted = any(
        item.id == candidate.root_structure_snapshot_id
        for item in bundle.structure_snapshots
    )
    if not root_is_persisted:
        raise WorkflowDurabilityError(
            "workflow plan root StructureSnapshot is not durably persisted"
        )
    matches = tuple(
        item for item in bundle.workflow_plans if item.plan_hash == candidate.plan_hash
    )
    if len(matches) > 1:
        raise WorkflowDurabilityError(
            "multiple persisted workflow plans share one scientific plan_hash"
        )
    if matches:
        persisted = matches[0]
        resume = reopen_workflow_resume_state(
            store=store,
            workflow_plan_id=persisted.id,
        )
        return WorkflowPlanPersistenceResult(
            plan=persisted,
            planning_hash=planning.planning_hash,
            reused=True,
            resume_state=resume,
        )

    updated = replace(bundle, workflow_plans=(*bundle.workflow_plans, candidate))
    store.save(updated)
    resume = reopen_workflow_resume_state(
        store=store,
        workflow_plan_id=candidate.id,
    )
    if resume.plan.plan_hash != candidate.plan_hash:
        raise WorkflowDurabilityError(
            "persisted workflow plan failed post-save identity verification"
        )
    return WorkflowPlanPersistenceResult(
        plan=resume.plan,
        planning_hash=planning.planning_hash,
        reused=False,
        resume_state=resume,
    )


def persist_or_reuse_workflow_materialization(
    *,
    store: ProjectStore,
    plan: ScientificWorkflowPlan,
    orchestration: WorkflowOrchestrationEvaluation,
    step_key: str,
    fingerprint: MethodFingerprint,
    system_context: VaspSystemContext,
    project_lock: ProjectNumericalLock | None,
    root_snapshot: StructureSnapshot | None = None,
    accepted_structure_source: AcceptedStructureSource | None = None,
) -> WorkflowMaterializationPersistenceResult:
    """Persist one workflow generation or return its already-durable idempotent receipt."""

    bundle = store.open()
    persisted_plan = _validate_persisted_plan(bundle=bundle, plan=plan)
    handoff = _require_materialization_handoff(
        orchestration=orchestration,
        plan=persisted_plan,
        step_key=step_key,
    )
    _require_persisted_fingerprint(bundle=bundle, fingerprint=fingerprint)
    _validate_materialization_sources(
        bundle=bundle,
        handoff=handoff,
        root_snapshot=root_snapshot,
        accepted_structure_source=accepted_structure_source,
    )
    previous = _previous_binding_for_handoff(
        bundle=bundle,
        plan=persisted_plan,
        handoff=handoff,
    )
    candidate = materialize_workflow_step(
        plan=persisted_plan,
        step_key=step_key,
        fingerprint=fingerprint,
        system_context=system_context,
        project_lock=project_lock,
        root_snapshot=root_snapshot,
        accepted_structure_source=accepted_structure_source,
        previous_binding=previous,
        materialization_reason=handoff.materialization_reason,
    )
    _validate_candidate_materialization(handoff=handoff, candidate=candidate)

    existing = tuple(
        item
        for item in bundle.workflow_step_bindings
        if item.workflow_plan_id == persisted_plan.id
        and item.step_key == step_key
        and item.generation == candidate.binding.generation
    )
    if len(existing) > 1:
        raise WorkflowDurabilityError("workflow materialization generation is not unique")
    if existing:
        receipt = _reuse_existing_materialization(
            bundle=bundle,
            candidate=candidate,
            existing_binding=existing[0],
        )
        resume = reopen_workflow_resume_state(
            store=store,
            workflow_plan_id=persisted_plan.id,
        )
        return WorkflowMaterializationPersistenceResult(
            materialization=receipt,
            reused=True,
            resume_state=resume,
        )

    _validate_materialization_append_slot(
        bundle=bundle,
        plan=persisted_plan,
        step_key=step_key,
        previous_binding=previous,
    )
    updated = replace(
        bundle,
        calculations=(*bundle.calculations, candidate.calculation),
        workflow_step_bindings=(*bundle.workflow_step_bindings, candidate.binding),
    )
    store.save(updated)
    resume = reopen_workflow_resume_state(
        store=store,
        workflow_plan_id=persisted_plan.id,
    )
    persisted_binding = next(
        (item for item in resume.bindings if item.id == candidate.binding.id),
        None,
    )
    if persisted_binding is None:
        raise WorkflowDurabilityError("new workflow binding is missing after durable save")
    persisted_calculation = resume.calculation(candidate.calculation.id)
    receipt = WorkflowStepMaterialization(
        calculation=persisted_calculation,
        binding=persisted_binding,
        resolved_input_snapshot=candidate.resolved_input_snapshot,
        source_binding_id=candidate.source_binding_id,
    )
    return WorkflowMaterializationPersistenceResult(
        materialization=receipt,
        reused=False,
        resume_state=resume,
    )


def persist_workflow_dispatch_wave(
    *,
    store: ProjectStore,
    plan: ScientificWorkflowPlan,
    orchestration: WorkflowOrchestrationEvaluation,
    concurrency: BatchConcurrencyPolicy,
    recovery_attempt_sources: tuple[WorkflowRecoveryAttemptSource, ...] = (),
) -> WorkflowDispatchPersistenceResult:
    """Persist newly allocated attempts before exposing a replay-safe scheduler wave."""

    bundle = store.open()
    persisted_plan = _validate_persisted_plan(bundle=bundle, plan=plan)
    if orchestration.workflow_plan_id != persisted_plan.id:
        raise WorkflowDurabilityError(
            "workflow orchestration belongs to another persisted plan"
        )
    if orchestration.scheduler_dag is None:
        if recovery_attempt_sources:
            raise WorkflowDurabilityError(
                "recovery attempt sources require a scheduler-dispatch orchestration"
            )
        raise WorkflowDurabilityError(
            "workflow orchestration has no scheduler-dispatch work to persist"
        )

    resume = reopen_workflow_resume_state(
        store=store,
        workflow_plan_id=persisted_plan.id,
    )
    _validate_scheduler_generation_currentness(
        orchestration=orchestration,
        resume=resume,
    )
    source_by_step = _recovery_source_index(
        orchestration=orchestration,
        sources=recovery_attempt_sources,
    )
    pending_recovery = _pending_recovery_decisions(
        orchestration=orchestration,
        resume=resume,
        source_by_step=source_by_step,
    )
    first_wave = prepare_batch_dispatch_wave(
        dag=orchestration.scheduler_dag,
        concurrency=concurrency,
        attempts=resume.execution_attempts,
        remote_jobs=resume.remote_jobs,
        recovery_decisions=pending_recovery,
    )
    new_attempts = first_wave.new_attempts
    new_ids = tuple(item.id for item in new_attempts)
    if new_attempts:
        latest_resume = reopen_workflow_resume_state(
            store=store,
            workflow_plan_id=persisted_plan.id,
        )
        if latest_resume.resume_hash != resume.resume_hash:
            raise WorkflowDurabilityError(
                "durable workflow state changed while dispatch attempts were being prepared"
            )
        current_bundle = store.open()
        _validate_persisted_plan(bundle=current_bundle, plan=persisted_plan)
        known_ids = {item.id for item in current_bundle.execution_attempts}
        if any(item.id in known_ids for item in new_attempts):
            raise WorkflowDurabilityError(
                "new workflow dispatch attempt unexpectedly already exists before save"
            )
        updated = replace(
            current_bundle,
            execution_attempts=(*current_bundle.execution_attempts, *new_attempts),
        )
        store.save(updated)

    post_resume = reopen_workflow_resume_state(
        store=store,
        workflow_plan_id=persisted_plan.id,
    )
    _validate_scheduler_generation_currentness(
        orchestration=orchestration,
        resume=post_resume,
    )
    post_pending_recovery = _pending_recovery_decisions(
        orchestration=orchestration,
        resume=post_resume,
        source_by_step=source_by_step,
    )
    post_wave = prepare_batch_dispatch_wave(
        dag=orchestration.scheduler_dag,
        concurrency=concurrency,
        attempts=post_resume.execution_attempts,
        remote_jobs=post_resume.remote_jobs,
        recovery_decisions=post_pending_recovery,
    )
    if post_wave.new_attempts:
        raise WorkflowDurabilityError(
            "post-persistence scheduler reconciliation attempted to allocate duplicate attempts"
        )
    return WorkflowDispatchPersistenceResult(
        wave=post_wave,
        newly_persisted_attempt_ids=new_ids,
        resume_state=post_resume,
    )


def _require_plan(
    *,
    bundle: ProjectBundle,
    workflow_plan_id: WorkflowPlanId,
) -> ScientificWorkflowPlan:
    matches = tuple(
        item for item in bundle.workflow_plans if item.id == workflow_plan_id
    )
    if len(matches) != 1:
        raise WorkflowDurabilityError(
            "workflow plan is absent or duplicated in ProjectStore"
        )
    return matches[0]


def _validate_persisted_plan(
    *,
    bundle: ProjectBundle,
    plan: ScientificWorkflowPlan,
) -> ScientificWorkflowPlan:
    persisted = _require_plan(bundle=bundle, workflow_plan_id=plan.id)
    if persisted != plan or persisted.plan_hash != plan.plan_hash:
        raise WorkflowDurabilityError(
            "supplied workflow plan does not match durable ProjectStore"
        )
    return persisted


def _require_materialization_handoff(
    *,
    orchestration: WorkflowOrchestrationEvaluation,
    plan: ScientificWorkflowPlan,
    step_key: str,
) -> WorkflowStepOrchestration:
    if orchestration.workflow_plan_id != plan.id:
        raise WorkflowDurabilityError("workflow orchestration belongs to another plan")
    try:
        handoff = orchestration.step(step_key)
    except KeyError as error:
        raise WorkflowDurabilityError(
            f"workflow orchestration lacks step {step_key!r}"
        ) from error
    if handoff.action is not WorkflowOrchestrationAction.MATERIALIZE_STEP:
        raise WorkflowDurabilityError(
            "workflow step is not authorized for materialization"
        )
    return handoff


def _require_persisted_fingerprint(
    *,
    bundle: ProjectBundle,
    fingerprint: MethodFingerprint,
) -> None:
    matches = tuple(
        item for item in bundle.method_fingerprints if item.id == fingerprint.id
    )
    if len(matches) != 1 or matches[0] != fingerprint:
        raise WorkflowDurabilityError(
            "MethodFingerprint is not durably persisted exactly"
        )


def _validate_materialization_sources(
    *,
    bundle: ProjectBundle,
    handoff: WorkflowStepOrchestration,
    root_snapshot: StructureSnapshot | None,
    accepted_structure_source: AcceptedStructureSource | None,
) -> None:
    if root_snapshot is not None:
        persisted = tuple(
            item
            for item in bundle.structure_snapshots
            if item.id == root_snapshot.id
        )
        if len(persisted) != 1 or persisted[0] != root_snapshot:
            raise WorkflowDurabilityError(
                "root StructureSnapshot is not durably persisted exactly"
            )
    if accepted_structure_source is None:
        return
    source = accepted_structure_source
    binding_matches = tuple(
        item
        for item in bundle.workflow_step_bindings
        if item.id == source.upstream_binding.id
    )
    calculation_matches = tuple(
        item
        for item in bundle.calculations
        if item.id == source.upstream_calculation.id
    )
    snapshot_matches = tuple(
        item
        for item in bundle.structure_snapshots
        if item.id == source.promotion.snapshot.id
    )
    variant_matches = tuple(
        item
        for item in bundle.structure_variants
        if item.id == source.promotion.updated_variant.id
    )
    if len(binding_matches) != 1 or binding_matches[0] != source.upstream_binding:
        raise WorkflowDurabilityError(
            "accepted-structure source binding is not durably persisted"
        )
    if (
        len(calculation_matches) != 1
        or calculation_matches[0] != source.upstream_calculation
    ):
        raise WorkflowDurabilityError(
            "accepted-structure source Calculation is not durably persisted"
        )
    if len(snapshot_matches) != 1 or snapshot_matches[0] != source.promotion.snapshot:
        raise WorkflowDurabilityError(
            "accepted promoted StructureSnapshot is not durably persisted"
        )
    if (
        len(variant_matches) != 1
        or variant_matches[0] != source.promotion.updated_variant
    ):
        raise WorkflowDurabilityError(
            "accepted promoted StructureVariant is not durably persisted"
        )
    if handoff.source_binding_id != source.upstream_binding.id:
        raise WorkflowDurabilityError(
            "accepted-structure source does not match orchestration source binding"
        )


def _previous_binding_for_handoff(
    *,
    bundle: ProjectBundle,
    plan: ScientificWorkflowPlan,
    handoff: WorkflowStepOrchestration,
) -> WorkflowStepBinding | None:
    previous_id = handoff.previous_binding_id
    if previous_id is None:
        return None
    matches = tuple(
        item
        for item in bundle.workflow_step_bindings
        if item.id == previous_id and item.workflow_plan_id == plan.id
    )
    if len(matches) != 1:
        raise WorkflowDurabilityError(
            "previous workflow binding is not durably persisted exactly"
        )
    return matches[0]


def _validate_candidate_materialization(
    *,
    handoff: WorkflowStepOrchestration,
    candidate: WorkflowStepMaterialization,
) -> None:
    if candidate.resolved_input_snapshot.id != handoff.target_input_structure_snapshot_id:
        raise WorkflowDurabilityError(
            "materialization candidate target does not match orchestration handoff"
        )
    if candidate.source_binding_id != handoff.source_binding_id:
        raise WorkflowDurabilityError(
            "materialization candidate source binding does not match orchestration handoff"
        )
    if candidate.binding.supersedes_binding_id != handoff.previous_binding_id:
        raise WorkflowDurabilityError(
            "materialization candidate supersession does not match orchestration handoff"
        )


def _reuse_existing_materialization(
    *,
    bundle: ProjectBundle,
    candidate: WorkflowStepMaterialization,
    existing_binding: WorkflowStepBinding,
) -> WorkflowStepMaterialization:
    expected = candidate.binding
    if (
        existing_binding.workflow_plan_id != expected.workflow_plan_id
        or existing_binding.step_key != expected.step_key
        or existing_binding.generation != expected.generation
        or existing_binding.resolved_input_structure_snapshot_id
        != expected.resolved_input_structure_snapshot_id
        or existing_binding.supersedes_binding_id != expected.supersedes_binding_id
    ):
        raise WorkflowDurabilityError(
            "persisted workflow generation conflicts with replayed materialization identity"
        )
    calculation_matches = tuple(
        item
        for item in bundle.calculations
        if item.id == existing_binding.calculation_id
    )
    if len(calculation_matches) != 1:
        raise WorkflowDurabilityError(
            "persisted workflow binding Calculation is missing"
        )
    existing_calculation = calculation_matches[0]
    candidate_calculation = candidate.calculation
    if (
        existing_calculation.project_id != candidate_calculation.project_id
        or existing_calculation.calculation_type is not candidate_calculation.calculation_type
        or existing_calculation.engine is not candidate_calculation.engine
        or existing_calculation.input_structure_snapshot_id
        != candidate_calculation.input_structure_snapshot_id
        or existing_calculation.recipe_id != candidate_calculation.recipe_id
        or existing_calculation.method_fingerprint_id
        != candidate_calculation.method_fingerprint_id
    ):
        raise WorkflowDurabilityError(
            "persisted Calculation conflicts with replayed workflow materialization identity"
        )
    return WorkflowStepMaterialization(
        calculation=existing_calculation,
        binding=existing_binding,
        resolved_input_snapshot=candidate.resolved_input_snapshot,
        source_binding_id=candidate.source_binding_id,
    )


def _validate_materialization_append_slot(
    *,
    bundle: ProjectBundle,
    plan: ScientificWorkflowPlan,
    step_key: str,
    previous_binding: WorkflowStepBinding | None,
) -> None:
    step_bindings = tuple(
        item
        for item in bundle.workflow_step_bindings
        if item.workflow_plan_id == plan.id and item.step_key == step_key
    )
    if previous_binding is None:
        if step_bindings:
            raise WorkflowDurabilityError(
                "generation-1 materialization handoff is stale against persisted workflow state"
            )
        return
    current = max(step_bindings, key=lambda item: item.generation, default=None)
    if current is None or current.id != previous_binding.id:
        raise WorkflowDurabilityError(
            "rematerialization handoff does not supersede the current persisted generation"
        )


def _validate_scheduler_generation_currentness(
    *,
    orchestration: WorkflowOrchestrationEvaluation,
    resume: WorkflowResumeState,
) -> None:
    if orchestration.scheduler_dag is None:
        raise WorkflowDurabilityError(
            "scheduler currentness validation requires SchedulerDag"
        )
    handoff_by_node = {
        item.scheduler_node_id: item
        for item in orchestration.step_handoffs
        if item.scheduler_node_id is not None
    }
    for node in orchestration.scheduler_dag.nodes:
        handoff = handoff_by_node.get(node.node_id)
        if handoff is None:
            raise WorkflowDurabilityError("SchedulerDag node lacks workflow handoff")
        current = resume.current_binding(handoff.step_key)
        if current is None or handoff.current_binding_id != current.id:
            raise WorkflowDurabilityError(
                "scheduler handoff no longer references the current persisted binding generation"
            )
        if current.calculation_id != node.calculation.id:
            raise WorkflowDurabilityError(
                "SchedulerDag Calculation is not the current persisted workflow Calculation"
            )
        persisted_calculation = resume.calculation(node.calculation.id)
        if persisted_calculation != node.calculation:
            raise WorkflowDurabilityError(
                "SchedulerDag Calculation differs from the durable ProjectStore generation"
            )
        if node.plan.calculation_id != persisted_calculation.id:
            raise WorkflowDurabilityError(
                "SchedulerDag ExecutionPlan does not belong to durable current Calculation"
            )


def _recovery_source_index(
    *,
    orchestration: WorkflowOrchestrationEvaluation,
    sources: tuple[WorkflowRecoveryAttemptSource, ...],
) -> dict[str, WorkflowRecoveryAttemptSource]:
    result: dict[str, WorkflowRecoveryAttemptSource] = {}
    recovery_steps = {
        item.step_key
        for item in orchestration.step_handoffs
        if item.action is WorkflowOrchestrationAction.EXECUTION_RECOVERY_READY
    }
    for source in sources:
        if source.step_key in result:
            raise WorkflowDurabilityError(
                "recovery attempt sources must be unique by step_key"
            )
        if source.step_key not in recovery_steps:
            raise WorkflowDurabilityError(
                "recovery attempt source does not correspond to execution-recovery-ready work"
            )
        result[source.step_key] = source
    if set(result) != recovery_steps:
        raise WorkflowDurabilityError(
            "every execution-recovery-ready step requires an explicit source attempt"
        )
    return result


def _pending_recovery_decisions(
    *,
    orchestration: WorkflowOrchestrationEvaluation,
    resume: WorkflowResumeState,
    source_by_step: dict[str, WorkflowRecoveryAttemptSource],
) -> dict[str, RecoveryDecision]:
    if orchestration.scheduler_dag is None:
        return {}
    recovery_by_node = orchestration.scheduler_recovery_decisions
    result: dict[str, RecoveryDecision] = {}
    node_by_id = {item.node_id: item for item in orchestration.scheduler_dag.nodes}
    handoff_by_node = {
        item.scheduler_node_id: item
        for item in orchestration.step_handoffs
        if item.scheduler_node_id is not None
    }
    for node_id, decision in recovery_by_node.items():
        node = node_by_id[node_id]
        handoff = handoff_by_node[node_id]
        source_spec = source_by_step[handoff.step_key]
        try:
            source = resume.attempt(source_spec.source_attempt_id)
        except KeyError as error:
            raise WorkflowDurabilityError(
                "recovery source attempt is absent from durable workflow state"
            ) from error
        if source.calculation_id != node.calculation.id:
            raise WorkflowDurabilityError(
                "recovery source attempt belongs to another workflow Calculation"
            )
        if source.execution_plan_hash != decision.source_plan_hash:
            raise WorkflowDurabilityError(
                "recovery decision source plan does not match the explicit source attempt"
            )
        history = tuple(
            item
            for item in resume.execution_attempts
            if item.calculation_id == node.calculation.id
        )
        children = tuple(
            item for item in history if item.previous_attempt_id == source.id
        )
        if len(children) > 1:
            raise WorkflowDurabilityError(
                "recovery source attempt has multiple direct successors; replay is ambiguous"
            )
        if children:
            child = children[0]
            if child.attempt_number != source.attempt_number + 1:
                raise WorkflowDurabilityError(
                    "recovery successor does not immediately follow its source attempt"
                )
            if child.execution_plan_hash != node.plan.plan_hash:
                raise WorkflowDurabilityError(
                    "persisted recovery successor pins a different ExecutionPlan"
                )
            continue
        latest = max(history, key=lambda item: item.attempt_number, default=None)
        if latest is None or latest.id != source.id:
            raise WorkflowDurabilityError(
                "unconsumed recovery decision is not bound to the latest persisted attempt"
            )
        if source.status not in _RECOVERY_SOURCE_TERMINAL_STATUSES:
            raise WorkflowDurabilityError(
                "unconsumed recovery source attempt is not terminal for new-attempt recovery"
            )
        result[node_id] = decision
    return result

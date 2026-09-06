"""Workflow, execution-attempt, and scheduler readiness dashboard projections."""

from __future__ import annotations

from dataclasses import dataclass

from ecatvasp.domain import (
    ExecutionAttempt,
    RemoteJob,
    ScientificWorkflowPlan,
)
from ecatvasp.domain.ids import (
    CalculationId,
    ExecutionAttemptId,
    RemoteJobId,
    StructureSnapshotId,
    WorkflowPlanId,
    WorkflowStepBindingId,
)
from ecatvasp.provenance import FreshnessState
from ecatvasp.storage.model import ProjectBundle
from ecatvasp.workflow import (
    WorkflowEdgeGateVerdict,
    WorkflowGateError,
    WorkflowOrchestrationAction,
    WorkflowOrchestrationEvaluation,
    WorkflowScientificGateEvaluation,
    WorkflowStepReadiness,
    WorkflowStepScientificState,
    resolve_workflow_binding_generations,
)


class WorkspaceReadinessError(ValueError):
    """Raised when authoritative workflow/readiness projections do not match project state."""


@dataclass(frozen=True, slots=True)
class WorkflowRemoteJobView:
    """Exact scheduler observation linked to one ExecutionAttempt."""

    remote_job_id: RemoteJobId
    execution_attempt_id: ExecutionAttemptId
    scheduler: str
    scheduler_job_id: str
    state: str
    remote_directory: str


@dataclass(frozen=True, slots=True)
class WorkflowExecutionAttemptView:
    """Exact execution-attempt observation for one current workflow Calculation."""

    execution_attempt_id: ExecutionAttemptId
    calculation_id: CalculationId
    attempt_number: int
    status: str
    previous_attempt_id: ExecutionAttemptId | None
    remote_jobs: tuple[WorkflowRemoteJobView, ...] = ()


@dataclass(frozen=True, slots=True)
class WorkflowReadinessStepView:
    """Application-facing step view preserving authoritative scientific and execution layers."""

    step_key: str
    scientific_state: WorkflowStepScientificState
    readiness: WorkflowStepReadiness
    gate_reason_codes: tuple[str, ...]
    freshness_state: FreshnessState | None
    current_binding_id: WorkflowStepBindingId | None
    current_generation: int | None
    calculation_id: CalculationId | None
    calculation_status: str | None
    superseded_binding_ids: tuple[WorkflowStepBindingId, ...]
    superseded_calculation_ids: tuple[CalculationId, ...]
    execution_attempts: tuple[WorkflowExecutionAttemptView, ...]
    orchestration_action: WorkflowOrchestrationAction | None = None
    orchestration_reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WorkflowReadinessEdgeView:
    """Exact logical workflow edge gate without scheduler reinterpretation."""

    upstream_step_key: str
    downstream_step_key: str
    role: str
    verdict: WorkflowEdgeGateVerdict
    source_binding_id: WorkflowStepBindingId | None
    accepted_structure_snapshot_id: StructureSnapshotId | None
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WorkflowReadinessDashboard:
    """Pure readiness dashboard for one persisted ScientificWorkflowPlan."""

    workflow_plan_id: WorkflowPlanId
    steps: tuple[WorkflowReadinessStepView, ...]
    edges: tuple[WorkflowReadinessEdgeView, ...]

    def step(self, step_key: str) -> WorkflowReadinessStepView:
        """Return one dashboard step by exact logical key."""

        for item in self.steps:
            if item.step_key == step_key:
                return item
        raise KeyError(step_key)


def build_workflow_readiness_dashboard(
    bundle: ProjectBundle,
    *,
    workflow_plan_id: WorkflowPlanId,
    gates: WorkflowScientificGateEvaluation,
    orchestration: WorkflowOrchestrationEvaluation | None = None,
) -> WorkflowReadinessDashboard:
    """Project authoritative workflow gates and execution observations without recomputing them."""

    bundle.validate()
    plan = _plan(bundle, workflow_plan_id)
    _validate_gate_projection(plan=plan, bundle=bundle, gates=gates)
    _validate_orchestration_projection(plan=plan, gates=gates, orchestration=orchestration)

    attempts_by_calculation = _attempt_index(bundle.execution_attempts)
    jobs_by_attempt = _job_index(bundle.remote_jobs)
    orchestration_by_step = (
        {}
        if orchestration is None
        else {item.step_key: item for item in orchestration.step_handoffs}
    )

    steps: list[WorkflowReadinessStepView] = []
    for selection, gate in zip(gates.binding_selections, gates.step_gates, strict=True):
        binding = selection.current_binding
        calculation = selection.current_calculation
        attempts = (
            ()
            if calculation is None
            else tuple(
                _attempt_view(item, jobs_by_attempt=jobs_by_attempt)
                for item in attempts_by_calculation.get(calculation.id, ())
            )
        )
        handoff = orchestration_by_step.get(selection.step_key)
        steps.append(
            WorkflowReadinessStepView(
                step_key=selection.step_key,
                scientific_state=gate.scientific_state,
                readiness=gate.readiness,
                gate_reason_codes=gate.reason_codes,
                freshness_state=gate.freshness_state,
                current_binding_id=None if binding is None else binding.id,
                current_generation=None if binding is None else binding.generation,
                calculation_id=None if calculation is None else calculation.id,
                calculation_status=None if calculation is None else calculation.status.value,
                superseded_binding_ids=selection.superseded_binding_ids,
                superseded_calculation_ids=selection.superseded_calculation_ids,
                execution_attempts=attempts,
                orchestration_action=None if handoff is None else handoff.action,
                orchestration_reason_codes=() if handoff is None else handoff.reason_codes,
            )
        )

    edges = tuple(
        WorkflowReadinessEdgeView(
            upstream_step_key=item.upstream_step_key,
            downstream_step_key=item.downstream_step_key,
            role=item.role,
            verdict=item.verdict,
            source_binding_id=item.source_binding_id,
            accepted_structure_snapshot_id=item.accepted_structure_snapshot_id,
            reason_codes=item.reason_codes,
        )
        for item in gates.edge_gates
    )
    return WorkflowReadinessDashboard(
        workflow_plan_id=plan.id,
        steps=tuple(steps),
        edges=edges,
    )


def _plan(bundle: ProjectBundle, workflow_plan_id: WorkflowPlanId) -> ScientificWorkflowPlan:
    for plan in bundle.workflow_plans:
        if plan.id == workflow_plan_id:
            return plan
    raise WorkspaceReadinessError("workflow readiness references a missing ScientificWorkflowPlan")


def _validate_gate_projection(
    *,
    plan: ScientificWorkflowPlan,
    bundle: ProjectBundle,
    gates: WorkflowScientificGateEvaluation,
) -> None:
    if gates.workflow_plan_id != plan.id:
        raise WorkspaceReadinessError("workflow gate projection belongs to another plan")
    expected_steps = tuple(item.key for item in plan.steps)
    if tuple(item.step_key for item in gates.binding_selections) != expected_steps:
        raise WorkspaceReadinessError(
            "workflow binding selections do not match canonical plan steps"
        )
    if tuple(item.step_key for item in gates.step_gates) != expected_steps:
        raise WorkspaceReadinessError("workflow step gates do not match canonical plan steps")

    expected_edges = {
        (item.upstream_step_key, item.downstream_step_key, item.role) for item in plan.edges
    }
    actual_edges = {
        (item.upstream_step_key, item.downstream_step_key, item.role) for item in gates.edge_gates
    }
    if actual_edges != expected_edges:
        raise WorkspaceReadinessError("workflow edge gates do not match canonical plan edges")

    try:
        current_selections = resolve_workflow_binding_generations(
            plan=plan,
            bindings=bundle.workflow_step_bindings,
            calculations=bundle.calculations,
        )
    except WorkflowGateError as error:
        raise WorkspaceReadinessError(str(error)) from error
    if gates.binding_selections != current_selections:
        raise WorkspaceReadinessError(
            "workflow gate projection does not match the current persisted binding generations"
        )

    for selection, gate in zip(gates.binding_selections, gates.step_gates, strict=True):
        if selection.step_key != gate.step_key:
            raise WorkspaceReadinessError("workflow gate step order is inconsistent")
        binding = selection.current_binding
        calculation = selection.current_calculation
        if binding is None or calculation is None:
            if gate.current_binding_id is not None or gate.calculation_id is not None:
                raise WorkspaceReadinessError("unmaterialized gate carries persisted current ids")
            continue
        if gate.current_binding_id != binding.id or gate.calculation_id != calculation.id:
            raise WorkspaceReadinessError(
                "workflow gate ids do not match selected current generation"
            )

    current_superseded = tuple(
        sorted(
            (
                calculation_id
                for selection in current_selections
                for calculation_id in selection.superseded_calculation_ids
            ),
            key=str,
        )
    )
    if gates.superseded_calculation_ids != current_superseded:
        raise WorkspaceReadinessError(
            "workflow gate supersession summary does not match persisted generation history"
        )


def _validate_orchestration_projection(
    *,
    plan: ScientificWorkflowPlan,
    gates: WorkflowScientificGateEvaluation,
    orchestration: WorkflowOrchestrationEvaluation | None,
) -> None:
    if orchestration is None:
        return
    if orchestration.workflow_plan_id != plan.id:
        raise WorkspaceReadinessError("workflow orchestration projection belongs to another plan")
    expected_steps = tuple(item.key for item in plan.steps)
    if tuple(item.step_key for item in orchestration.step_handoffs) != expected_steps:
        raise WorkspaceReadinessError(
            "workflow orchestration handoffs do not match canonical plan steps"
        )
    for handoff, gate in zip(orchestration.step_handoffs, gates.step_gates, strict=True):
        if handoff.step_key != gate.step_key:
            raise WorkspaceReadinessError("workflow orchestration step order is inconsistent")
        if handoff.current_binding_id != gate.current_binding_id:
            raise WorkspaceReadinessError(
                "workflow orchestration current binding does not match scientific gate"
            )
        if handoff.calculation_id != gate.calculation_id:
            raise WorkspaceReadinessError(
                "workflow orchestration Calculation does not match scientific gate"
            )


def _attempt_index(
    attempts: tuple[ExecutionAttempt, ...],
) -> dict[CalculationId, tuple[ExecutionAttempt, ...]]:
    grouped: dict[CalculationId, list[ExecutionAttempt]] = {}
    for attempt in attempts:
        grouped.setdefault(attempt.calculation_id, []).append(attempt)
    return {
        calculation_id: tuple(sorted(items, key=lambda item: item.attempt_number))
        for calculation_id, items in grouped.items()
    }


def _job_index(jobs: tuple[RemoteJob, ...]) -> dict[ExecutionAttemptId, tuple[RemoteJob, ...]]:
    grouped: dict[ExecutionAttemptId, list[RemoteJob]] = {}
    for job in jobs:
        grouped.setdefault(job.execution_attempt_id, []).append(job)
    return {
        attempt_id: tuple(sorted(items, key=lambda item: str(item.id)))
        for attempt_id, items in grouped.items()
    }


def _attempt_view(
    attempt: ExecutionAttempt,
    *,
    jobs_by_attempt: dict[ExecutionAttemptId, tuple[RemoteJob, ...]],
) -> WorkflowExecutionAttemptView:
    return WorkflowExecutionAttemptView(
        execution_attempt_id=attempt.id,
        calculation_id=attempt.calculation_id,
        attempt_number=attempt.attempt_number,
        status=attempt.status.value,
        previous_attempt_id=attempt.previous_attempt_id,
        remote_jobs=tuple(_job_view(item) for item in jobs_by_attempt.get(attempt.id, ())),
    )


def _job_view(job: RemoteJob) -> WorkflowRemoteJobView:
    return WorkflowRemoteJobView(
        remote_job_id=job.id,
        execution_attempt_id=job.execution_attempt_id,
        scheduler=job.scheduler.value,
        scheduler_job_id=job.scheduler_job_id,
        state=job.state.value,
        remote_directory=job.remote_directory,
    )

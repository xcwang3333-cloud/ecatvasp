"""Final v0.6 workflow acceptance and cross-layer hardening."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from enum import StrEnum

from ecatvasp.domain import ScientificWorkflowPlan, canonical_sha256
from ecatvasp.domain.ids import (
    CalculationId,
    ExecutionAttemptId,
    StructureSnapshotId,
    WorkflowPlanId,
    WorkflowStepBindingId,
)
from ecatvasp.schema.version import SCHEMA_VERSION
from ecatvasp.storage import ProjectStore
from ecatvasp.workflow.durability import (
    WorkflowResumeState,
    reopen_workflow_resume_state,
)
from ecatvasp.workflow.gates import (
    WorkflowBindingSelection,
    WorkflowEdgeGateVerdict,
    WorkflowScientificGateEvaluation,
    WorkflowStepReadiness,
    WorkflowStepScientificState,
    resolve_workflow_binding_generations,
)
from ecatvasp.workflow.orchestration import (
    WorkflowOrchestrationAction,
    WorkflowOrchestrationEvaluation,
)
from ecatvasp.workflow.recipes import (
    WORKFLOW_EDGE_ACCEPTED_STRUCTURE,
    WorkflowRecipeContractError,
    validate_workflow_plan_recipe_contract,
)
from ecatvasp.workflow.recovery import (
    WorkflowRecoveryAction,
    WorkflowRecoveryPolicyEvaluation,
)


class WorkflowAcceptanceError(ValueError):
    """Raised when final v0.6 workflow acceptance cannot be established exactly."""


class WorkflowAcceptanceState(StrEnum):
    """Cross-layer state after validating one durable workflow projection."""

    COMPLETE = "complete"
    RESUMABLE = "resumable"
    ACTION_REQUIRED = "action_required"


@dataclass(frozen=True, slots=True)
class WorkflowStepAcceptance:
    """Portable acceptance summary for one logical workflow step."""

    step_key: str
    current_generation: int | None
    binding_id: WorkflowStepBindingId | None
    calculation_id: CalculationId | None
    scientific_state: WorkflowStepScientificState
    readiness: WorkflowStepReadiness
    recovery_action: WorkflowRecoveryAction
    orchestration_action: WorkflowOrchestrationAction


@dataclass(frozen=True, slots=True)
class WorkflowAcceptanceReport:
    """Deterministic final acceptance report for one v0.6 workflow snapshot."""

    workflow_plan_id: WorkflowPlanId
    plan_hash: str
    resume_hash: str
    state: WorkflowAcceptanceState
    steps: tuple[WorkflowStepAcceptance, ...]
    scheduler_node_ids: tuple[str, ...] = ()
    checks: tuple[str, ...] = ()
    schema_version: int = SCHEMA_VERSION
    acceptance_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("workflow acceptance report schema version does not match runtime")
        if len(self.scheduler_node_ids) != len(set(self.scheduler_node_ids)):
            raise ValueError("workflow acceptance scheduler node ids must be unique")
        object.__setattr__(
            self,
            "acceptance_hash",
            canonical_sha256(
                {
                    "workflow_plan_id": self.workflow_plan_id,
                    "plan_hash": self.plan_hash,
                    "resume_hash": self.resume_hash,
                    "state": self.state,
                    "steps": self.steps,
                    "scheduler_node_ids": self.scheduler_node_ids,
                    "checks": self.checks,
                    "schema_version": self.schema_version,
                }
            ),
        )

    @property
    def complete(self) -> bool:
        """Return whether every logical workflow step is scientifically satisfied."""

        return self.state is WorkflowAcceptanceState.COMPLETE


def validate_v06_workflow_acceptance(
    *,
    store: ProjectStore,
    plan: ScientificWorkflowPlan,
    gates: WorkflowScientificGateEvaluation,
    recovery: WorkflowRecoveryPolicyEvaluation,
    orchestration: WorkflowOrchestrationEvaluation,
) -> WorkflowAcceptanceReport:
    """Validate the complete v0.6 workflow stack without performing side effects."""

    checks: list[str] = []
    _validate_canonical_plan(plan)
    checks.append("canonical workflow recipe")

    bundle = store.open()
    _validate_unique_persisted_plan(bundle.workflow_plans, plan)
    resume = reopen_workflow_resume_state(store=store, workflow_plan_id=plan.id)
    if resume.plan != plan or resume.plan.plan_hash != plan.plan_hash:
        raise WorkflowAcceptanceError(
            "durable workflow plan does not match the supplied acceptance plan"
        )
    checks.append("durable workflow plan identity")

    durable_selections = resolve_workflow_binding_generations(
        plan=plan,
        bindings=resume.bindings,
        calculations=resume.calculations,
    )
    if durable_selections != gates.binding_selections:
        raise WorkflowAcceptanceError(
            "workflow gate projection does not match reopened current binding generations"
        )
    checks.append("durable current binding generations")

    _validate_projection_shapes(
        plan=plan,
        gates=gates,
        recovery=recovery,
        orchestration=orchestration,
    )
    checks.append("canonical cross-layer projection shapes")

    _validate_current_generation_alignment(
        selections=durable_selections,
        gates=gates,
        recovery=recovery,
        orchestration=orchestration,
    )
    checks.append("current generation cross-layer alignment")

    _validate_accepted_structure_edges(
        plan=plan,
        gates=gates,
        selections=durable_selections,
        persisted_snapshot_ids={item.id for item in bundle.structure_snapshots},
    )
    checks.append("accepted-structure persistence and currentness")

    scheduler_node_ids = _validate_scheduler_handoff(
        selections=durable_selections,
        orchestration=orchestration,
    )
    checks.append("scheduler handoff current-generation identity")

    _validate_attempt_lineage(resume)
    checks.append("execution-attempt recovery lineage")

    state = _classify_acceptance_state(
        gates=gates,
        orchestration=orchestration,
    )
    if state is WorkflowAcceptanceState.COMPLETE:
        _validate_complete_terminal_state(
            gates=gates,
            orchestration=orchestration,
        )
        checks.append("complete workflow terminal state")

    step_reports = tuple(
        _step_report(
            selection=selection,
            gates=gates,
            recovery=recovery,
            orchestration=orchestration,
        )
        for selection in durable_selections
    )
    return WorkflowAcceptanceReport(
        workflow_plan_id=plan.id,
        plan_hash=plan.plan_hash,
        resume_hash=resume.resume_hash,
        state=state,
        steps=step_reports,
        scheduler_node_ids=scheduler_node_ids,
        checks=tuple(checks),
    )


def _validate_canonical_plan(plan: ScientificWorkflowPlan) -> None:
    try:
        validate_workflow_plan_recipe_contract(plan)
    except WorkflowRecipeContractError as error:
        raise WorkflowAcceptanceError(str(error)) from error


def _validate_unique_persisted_plan(
    persisted_plans: tuple[ScientificWorkflowPlan, ...],
    plan: ScientificWorkflowPlan,
) -> None:
    exact = tuple(item for item in persisted_plans if item.id == plan.id)
    if len(exact) != 1 or exact[0] != plan:
        raise WorkflowAcceptanceError(
            "workflow acceptance requires the exact supplied plan to be durably persisted once"
        )
    same_hash = tuple(item for item in persisted_plans if item.plan_hash == plan.plan_hash)
    if len(same_hash) != 1:
        raise WorkflowAcceptanceError(
            "workflow acceptance requires a unique persisted scientific plan_hash"
        )


def _validate_projection_shapes(
    *,
    plan: ScientificWorkflowPlan,
    gates: WorkflowScientificGateEvaluation,
    recovery: WorkflowRecoveryPolicyEvaluation,
    orchestration: WorkflowOrchestrationEvaluation,
) -> None:
    if (
        gates.workflow_plan_id != plan.id
        or recovery.workflow_plan_id != plan.id
        or orchestration.workflow_plan_id != plan.id
    ):
        raise WorkflowAcceptanceError(
            "workflow acceptance projections must belong to the same persisted plan"
        )
    expected_keys = tuple(step.key for step in plan.steps)
    if tuple(item.step_key for item in gates.binding_selections) != expected_keys:
        raise WorkflowAcceptanceError(
            "workflow gate binding selections do not match canonical plan order"
        )
    if tuple(item.step_key for item in gates.step_gates) != expected_keys:
        raise WorkflowAcceptanceError(
            "workflow step gates do not match canonical plan order"
        )
    if tuple(item.step_key for item in recovery.step_policies) != expected_keys:
        raise WorkflowAcceptanceError(
            "workflow recovery policies do not match canonical plan order"
        )
    if tuple(item.step_key for item in orchestration.step_handoffs) != expected_keys:
        raise WorkflowAcceptanceError(
            "workflow orchestration handoffs do not match canonical plan order"
        )
    expected_edges = {
        (item.upstream_step_key, item.downstream_step_key, item.role)
        for item in plan.edges
    }
    observed_edges = {
        (item.upstream_step_key, item.downstream_step_key, item.role)
        for item in gates.edge_gates
    }
    if observed_edges != expected_edges:
        raise WorkflowAcceptanceError(
            "workflow edge gates do not match canonical plan edge set"
        )


def _validate_current_generation_alignment(
    *,
    selections: tuple[WorkflowBindingSelection, ...],
    gates: WorkflowScientificGateEvaluation,
    recovery: WorkflowRecoveryPolicyEvaluation,
    orchestration: WorkflowOrchestrationEvaluation,
) -> None:
    for selection in selections:
        gate = gates.step(selection.step_key)
        policy = recovery.step(selection.step_key)
        handoff = orchestration.step(selection.step_key)
        binding_id = (
            None if selection.current_binding is None else selection.current_binding.id
        )
        calculation_id = (
            None if selection.current_calculation is None else selection.current_calculation.id
        )
        if gate.current_binding_id != binding_id or gate.calculation_id != calculation_id:
            raise WorkflowAcceptanceError(
                "workflow step gate does not reference the durable current generation"
            )
        if (
            policy.current_binding_id != binding_id
            or policy.calculation_id != calculation_id
        ):
            raise WorkflowAcceptanceError(
                "workflow recovery policy does not reference the durable current generation"
            )
        if (
            handoff.current_binding_id != binding_id
            or handoff.calculation_id != calculation_id
        ):
            raise WorkflowAcceptanceError(
                "workflow orchestration handoff does not reference the durable current generation"
            )
        if (
            handoff.previous_binding_id is not None
            and handoff.previous_binding_id != binding_id
        ):
            raise WorkflowAcceptanceError(
                "workflow rematerialization handoff does not supersede the durable current binding"
            )


def _validate_accepted_structure_edges(
    *,
    plan: ScientificWorkflowPlan,
    gates: WorkflowScientificGateEvaluation,
    selections: tuple[WorkflowBindingSelection, ...],
    persisted_snapshot_ids: set[StructureSnapshotId],
) -> None:
    selection_by_step = {item.step_key: item for item in selections}
    for edge in gates.edge_gates:
        if edge.role != WORKFLOW_EDGE_ACCEPTED_STRUCTURE:
            continue
        if edge.verdict is not WorkflowEdgeGateVerdict.OPEN:
            continue
        upstream = selection_by_step[edge.upstream_step_key]
        upstream_binding = upstream.current_binding
        if upstream_binding is None or edge.source_binding_id != upstream_binding.id:
            raise WorkflowAcceptanceError(
                "open accepted-structure edge does not reference the current upstream binding"
            )
        snapshot_id = edge.accepted_structure_snapshot_id
        if snapshot_id is None or snapshot_id not in persisted_snapshot_ids:
            raise WorkflowAcceptanceError(
                "open accepted-structure edge references a non-durable StructureSnapshot"
            )
        downstream = selection_by_step[edge.downstream_step_key]
        downstream_binding = downstream.current_binding
        if (
            downstream_binding is not None
            and downstream_binding.resolved_input_structure_snapshot_id != snapshot_id
        ):
            raise WorkflowAcceptanceError(
                "durable downstream binding does not consume the current accepted structure"
            )


def _validate_scheduler_handoff(
    *,
    selections: tuple[WorkflowBindingSelection, ...],
    orchestration: WorkflowOrchestrationEvaluation,
) -> tuple[str, ...]:
    if orchestration.scheduler_dag is None:
        if orchestration.scheduler_recoveries:
            raise WorkflowAcceptanceError(
                "workflow scheduler recoveries cannot exist without SchedulerDag"
            )
        return ()

    selection_by_step = {item.step_key: item for item in selections}
    node_by_id = {item.node_id: item for item in orchestration.scheduler_dag.nodes}
    if len(node_by_id) != len(orchestration.scheduler_dag.nodes):
        raise WorkflowAcceptanceError("workflow SchedulerDag node ids must be unique")
    scheduler_node_ids: list[str] = []
    for handoff in orchestration.step_handoffs:
        if handoff.scheduler_node_id is None:
            continue
        node = node_by_id.get(handoff.scheduler_node_id)
        if node is None:
            raise WorkflowAcceptanceError(
                "workflow orchestration references a missing SchedulerDag node"
            )
        selection = selection_by_step[handoff.step_key]
        if selection.current_calculation is None or selection.current_binding is None:
            raise WorkflowAcceptanceError(
                "scheduler handoff requires a durable current workflow generation"
            )
        if node.calculation.id != selection.current_calculation.id:
            raise WorkflowAcceptanceError(
                "SchedulerDag node references a superseded workflow Calculation"
            )
        if handoff.execution_plan_hash != node.plan.plan_hash:
            raise WorkflowAcceptanceError(
                "workflow execution handoff does not pin its SchedulerDag ExecutionPlan"
            )
        scheduler_node_ids.append(node.node_id)

    if set(scheduler_node_ids) != set(node_by_id):
        raise WorkflowAcceptanceError(
            "workflow SchedulerDag nodes do not match current orchestration handoffs"
        )
    recovery_by_node = {item.node_id: item for item in orchestration.scheduler_recoveries}
    if len(recovery_by_node) != len(orchestration.scheduler_recoveries):
        raise WorkflowAcceptanceError(
            "workflow scheduler recovery node ids must be unique"
        )
    for node_id, recovery_handoff in recovery_by_node.items():
        step_handoff = next(
            (
                item
                for item in orchestration.step_handoffs
                if item.scheduler_node_id == node_id
            ),
            None,
        )
        if step_handoff is None:
            raise WorkflowAcceptanceError(
                "workflow scheduler recovery lacks its current step handoff"
            )
        if step_handoff.recovery_decision_hash != recovery_handoff.decision.decision_hash:
            raise WorkflowAcceptanceError(
                "workflow scheduler recovery decision hash does not match orchestration"
            )
    return tuple(sorted(scheduler_node_ids))


def _validate_attempt_lineage(resume: WorkflowResumeState) -> None:
    attempts_by_id = {item.id: item for item in resume.execution_attempts}
    children_by_parent: dict[ExecutionAttemptId, list[ExecutionAttemptId]] = defaultdict(list)
    for attempt in resume.execution_attempts:
        parent_id = attempt.previous_attempt_id
        if parent_id is None:
            continue
        parent = attempts_by_id.get(parent_id)
        if parent is None:
            raise WorkflowAcceptanceError(
                "workflow recovery attempt lineage references a missing parent attempt"
            )
        if parent.calculation_id != attempt.calculation_id:
            raise WorkflowAcceptanceError(
                "workflow recovery attempt lineage crosses Calculation identity"
            )
        if attempt.attempt_number != parent.attempt_number + 1:
            raise WorkflowAcceptanceError(
                "workflow recovery attempt lineage has a non-contiguous attempt number"
            )
        children_by_parent[parent_id].append(attempt.id)
    if any(len(children) > 1 for children in children_by_parent.values()):
        raise WorkflowAcceptanceError(
            "workflow recovery attempt lineage contains multiple direct successors"
        )


def _classify_acceptance_state(
    *,
    gates: WorkflowScientificGateEvaluation,
    orchestration: WorkflowOrchestrationEvaluation,
) -> WorkflowAcceptanceState:
    if all(
        gate.scientific_state is WorkflowStepScientificState.PASSED
        and gate.readiness is WorkflowStepReadiness.SATISFIED
        for gate in gates.step_gates
    ) and all(
        item.action is WorkflowOrchestrationAction.SATISFIED
        for item in orchestration.step_handoffs
    ):
        return WorkflowAcceptanceState.COMPLETE

    attention_actions = {
        WorkflowOrchestrationAction.RECOVERY_DECISION_REQUIRED,
        WorkflowOrchestrationAction.NEW_WORKFLOW_PLAN_REQUIRED,
        WorkflowOrchestrationAction.MANUAL_REVIEW_REQUIRED,
    }
    if any(item.action in attention_actions for item in orchestration.step_handoffs):
        return WorkflowAcceptanceState.ACTION_REQUIRED
    return WorkflowAcceptanceState.RESUMABLE


def _validate_complete_terminal_state(
    *,
    gates: WorkflowScientificGateEvaluation,
    orchestration: WorkflowOrchestrationEvaluation,
) -> None:
    if orchestration.scheduler_dag is not None or orchestration.scheduler_recoveries:
        raise WorkflowAcceptanceError(
            "complete workflow acceptance cannot retain scheduler dispatch work"
        )
    for gate in gates.step_gates:
        if (
            gate.scientific_state is not WorkflowStepScientificState.PASSED
            or gate.readiness is not WorkflowStepReadiness.SATISFIED
        ):
            raise WorkflowAcceptanceError(
                "complete workflow acceptance requires every step scientifically satisfied"
            )


def _step_report(
    *,
    selection: WorkflowBindingSelection,
    gates: WorkflowScientificGateEvaluation,
    recovery: WorkflowRecoveryPolicyEvaluation,
    orchestration: WorkflowOrchestrationEvaluation,
) -> WorkflowStepAcceptance:
    gate = gates.step(selection.step_key)
    policy = recovery.step(selection.step_key)
    handoff = orchestration.step(selection.step_key)
    return WorkflowStepAcceptance(
        step_key=selection.step_key,
        current_generation=(
            None if selection.current_binding is None else selection.current_binding.generation
        ),
        binding_id=(None if selection.current_binding is None else selection.current_binding.id),
        calculation_id=(
            None if selection.current_calculation is None else selection.current_calculation.id
        ),
        scientific_state=gate.scientific_state,
        readiness=gate.readiness,
        recovery_action=policy.action,
        orchestration_action=handoff.action,
    )

"""Deterministic workflow reconciliation and execution/materialization handoff for v0.6 Block 7."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from enum import StrEnum

from ecatvasp.domain import (
    Calculation,
    CalculationScientificStatus,
    ScientificWorkflowPlan,
    WorkflowStepBinding,
    canonical_sha256,
)
from ecatvasp.domain.ids import (
    CalculationId,
    StructureSnapshotId,
    WorkflowPlanId,
    WorkflowStepBindingId,
)
from ecatvasp.execution.batch import SchedulerDag, SchedulerDagNode
from ecatvasp.execution.recovery import RecoveryAction, RecoveryDecision
from ecatvasp.vasp.execution_plan import ExecutionPlan
from ecatvasp.workflow.gates import (
    WorkflowBindingSelection,
    WorkflowEdgeGate,
    WorkflowEdgeGateVerdict,
    WorkflowScientificGateEvaluation,
    WorkflowStepReadiness,
    WorkflowStepScientificState,
)
from ecatvasp.workflow.recipes import (
    WORKFLOW_EDGE_ACCEPTED_STRUCTURE,
    WorkflowRecipeContractError,
    validate_workflow_plan_recipe_contract,
)
from ecatvasp.workflow.recovery import (
    WorkflowRecoveryAction,
    WorkflowRecoveryPolicyEvaluation,
    WorkflowStepRecoveryPolicy,
)


class WorkflowOrchestrationError(ValueError):
    """Raised when a workflow handoff cannot be reconciled without guessing."""


class WorkflowOrchestrationAction(StrEnum):
    """Exact next handoff selected for one logical workflow step."""

    WAIT = "wait"
    MATERIALIZE_STEP = "materialize_step"
    EXECUTION_PLAN_REQUIRED = "execution_plan_required"
    EXECUTION_READY = "execution_ready"
    EXECUTION_IN_FLIGHT = "execution_in_flight"
    RETRY_SAME_ATTEMPT = "retry_same_attempt"
    RESUBMIT_SAME_ATTEMPT = "resubmit_same_attempt"
    EXECUTION_RECOVERY_READY = "execution_recovery_ready"
    RECOVERY_DECISION_REQUIRED = "recovery_decision_required"
    NEW_WORKFLOW_PLAN_REQUIRED = "new_workflow_plan_required"
    MANUAL_REVIEW_REQUIRED = "manual_review_required"
    SATISFIED = "satisfied"


@dataclass(frozen=True, slots=True)
class WorkflowExecutionSource:
    """Caller-supplied exact ExecutionPlan for one current workflow generation."""

    step_key: str
    binding: WorkflowStepBinding
    calculation: Calculation
    plan: ExecutionPlan
    recovery_decision: RecoveryDecision | None = None

    def __post_init__(self) -> None:
        if not self.step_key.strip():
            raise WorkflowOrchestrationError("workflow execution source requires a step_key")
        if self.binding.step_key != self.step_key:
            raise WorkflowOrchestrationError(
                "workflow execution source binding belongs to another step"
            )
        if self.binding.calculation_id != self.calculation.id:
            raise WorkflowOrchestrationError(
                "workflow execution source binding does not reference its Calculation"
            )
        if (
            self.binding.resolved_input_structure_snapshot_id
            != self.calculation.input_structure_snapshot_id
        ):
            raise WorkflowOrchestrationError(
                "workflow execution source binding input does not match Calculation input"
            )
        if self.plan.calculation_id != self.calculation.id:
            raise WorkflowOrchestrationError(
                "workflow execution source ExecutionPlan belongs to another Calculation"
            )
        if self.plan.recipe_id != self.calculation.recipe_id:
            raise WorkflowOrchestrationError(
                "workflow execution source ExecutionPlan recipe does not match Calculation"
            )


@dataclass(frozen=True, slots=True)
class WorkflowSchedulerRecoveryHandoff:
    """Recovery authorization passed to the existing v0.4 scheduler-DAG layer."""

    node_id: str
    decision: RecoveryDecision

    def __post_init__(self) -> None:
        if not self.node_id.strip():
            raise WorkflowOrchestrationError("scheduler recovery handoff requires node_id")
        if self.decision.action is not RecoveryAction.NEW_EXECUTION_ATTEMPT:
            raise WorkflowOrchestrationError(
                "scheduler recovery handoff supports only NEW_EXECUTION_ATTEMPT"
            )


@dataclass(frozen=True, slots=True)
class WorkflowStepOrchestration:
    """Auditable orchestration handoff for one current logical workflow step."""

    step_key: str
    action: WorkflowOrchestrationAction
    current_binding_id: WorkflowStepBindingId | None = None
    calculation_id: CalculationId | None = None
    previous_binding_id: WorkflowStepBindingId | None = None
    target_input_structure_snapshot_id: StructureSnapshotId | None = None
    source_binding_id: WorkflowStepBindingId | None = None
    materialization_reason: str | None = None
    execution_plan_hash: str | None = None
    scheduler_node_id: str | None = None
    execution_recovery_action: RecoveryAction | None = None
    recovery_decision_hash: str | None = None
    reason_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.step_key.strip():
            raise WorkflowOrchestrationError("workflow orchestration step_key must not be blank")
        if not self.reason_codes:
            raise WorkflowOrchestrationError(
                "workflow orchestration handoff requires at least one reason code"
            )
        if self.action is WorkflowOrchestrationAction.MATERIALIZE_STEP:
            if self.target_input_structure_snapshot_id is None:
                raise WorkflowOrchestrationError(
                    "materialization handoff requires an exact target StructureSnapshot"
                )
        elif any(
            value is not None
            for value in (
                self.previous_binding_id,
                self.target_input_structure_snapshot_id,
                self.source_binding_id,
                self.materialization_reason,
            )
        ):
            raise WorkflowOrchestrationError(
                "non-materialization handoff cannot carry materialization fields"
            )

        execution_actions = {
            WorkflowOrchestrationAction.EXECUTION_READY,
            WorkflowOrchestrationAction.RETRY_SAME_ATTEMPT,
            WorkflowOrchestrationAction.RESUBMIT_SAME_ATTEMPT,
            WorkflowOrchestrationAction.EXECUTION_RECOVERY_READY,
        }
        if self.action in execution_actions:
            if self.execution_plan_hash is None:
                raise WorkflowOrchestrationError(
                    "execution handoff requires an exact ExecutionPlan hash"
                )
        elif self.execution_plan_hash is not None or self.scheduler_node_id is not None:
            raise WorkflowOrchestrationError(
                "non-execution-ready handoff cannot carry execution dispatch fields"
            )

        if self.action is WorkflowOrchestrationAction.EXECUTION_RECOVERY_READY:
            if (
                self.execution_recovery_action is not RecoveryAction.NEW_EXECUTION_ATTEMPT
                or self.recovery_decision_hash is None
                or self.scheduler_node_id is None
            ):
                raise WorkflowOrchestrationError(
                    "execution recovery dispatch requires NEW_EXECUTION_ATTEMPT provenance"
                )
        elif self.action in {
            WorkflowOrchestrationAction.RETRY_SAME_ATTEMPT,
            WorkflowOrchestrationAction.RESUBMIT_SAME_ATTEMPT,
        }:
            if self.execution_recovery_action is None or self.recovery_decision_hash is None:
                raise WorkflowOrchestrationError(
                    "same-attempt recovery handoff requires RecoveryDecision provenance"
                )
        elif self.execution_recovery_action is not None or self.recovery_decision_hash is not None:
            raise WorkflowOrchestrationError(
                "non-recovery handoff cannot carry RecoveryDecision provenance"
            )


@dataclass(frozen=True, slots=True)
class WorkflowOrchestrationEvaluation:
    """Pure Block 7 projection plus exact v0.4 scheduler handoff values."""

    workflow_plan_id: WorkflowPlanId
    step_handoffs: tuple[WorkflowStepOrchestration, ...]
    scheduler_dag: SchedulerDag | None = None
    scheduler_recoveries: tuple[WorkflowSchedulerRecoveryHandoff, ...] = ()
    orchestration_hash: str = field(init=False)

    def __post_init__(self) -> None:
        step_keys = tuple(item.step_key for item in self.step_handoffs)
        if len(step_keys) != len(set(step_keys)):
            raise WorkflowOrchestrationError(
                "workflow orchestration step handoffs must have unique step keys"
            )
        recovery_node_ids = tuple(item.node_id for item in self.scheduler_recoveries)
        if len(recovery_node_ids) != len(set(recovery_node_ids)):
            raise WorkflowOrchestrationError(
                "scheduler recovery handoffs must have unique node ids"
            )
        if self.scheduler_dag is None:
            if self.scheduler_recoveries:
                raise WorkflowOrchestrationError(
                    "scheduler recovery handoff requires a SchedulerDag"
                )
            scheduler_node_ids: set[str] = set()
        else:
            scheduler_node_ids = {item.node_id for item in self.scheduler_dag.nodes}
        if not set(recovery_node_ids).issubset(scheduler_node_ids):
            raise WorkflowOrchestrationError(
                "scheduler recovery handoff references a node outside SchedulerDag"
            )
        object.__setattr__(
            self,
            "orchestration_hash",
            canonical_sha256(
                {
                    "workflow_plan_id": self.workflow_plan_id,
                    "step_handoffs": self.step_handoffs,
                    "scheduler_dag_hash": (
                        None if self.scheduler_dag is None else self.scheduler_dag.dag_hash
                    ),
                    "scheduler_recoveries": tuple(
                        (item.node_id, item.decision.decision_hash)
                        for item in self.scheduler_recoveries
                    ),
                }
            ),
        )

    def step(self, step_key: str) -> WorkflowStepOrchestration:
        """Resolve one orchestration handoff by logical workflow step key."""

        for item in self.step_handoffs:
            if item.step_key == step_key:
                return item
        raise KeyError(step_key)

    @property
    def scheduler_recovery_decisions(self) -> dict[str, RecoveryDecision]:
        """Return the mapping consumed by v0.4 ``reconcile_batch_dispatch``."""

        return {item.node_id: item.decision for item in self.scheduler_recoveries}


def reconcile_workflow_orchestration(
    *,
    plan: ScientificWorkflowPlan,
    gates: WorkflowScientificGateEvaluation,
    recovery: WorkflowRecoveryPolicyEvaluation,
    execution_sources: tuple[WorkflowExecutionSource, ...] = (),
) -> WorkflowOrchestrationEvaluation:
    """Reconcile workflow state into exact side-effect-free materialization/execution handoffs."""

    _validate_projections(plan=plan, gates=gates, recovery=recovery)
    selections = {item.step_key: item for item in gates.binding_selections}
    sources = _validate_execution_sources(
        plan=plan,
        gates=gates,
        selections=selections,
        execution_sources=execution_sources,
    )
    incoming: dict[str, list[WorkflowEdgeGate]] = defaultdict(list)
    for edge in gates.edge_gates:
        incoming[edge.downstream_step_key].append(edge)

    handoffs: list[WorkflowStepOrchestration] = []
    scheduler_nodes: list[SchedulerDagNode] = []
    scheduler_recoveries: list[WorkflowSchedulerRecoveryHandoff] = []
    for step in plan.steps:
        handoff, node, scheduler_recovery = _reconcile_step(
            plan=plan,
            gate=gates.step(step.key),
            selection=selections[step.key],
            policy=recovery.step(step.key),
            incoming_edges=tuple(incoming[step.key]),
            execution_source=sources.get(step.key),
        )
        handoffs.append(handoff)
        if node is not None:
            scheduler_nodes.append(node)
        if scheduler_recovery is not None:
            scheduler_recoveries.append(scheduler_recovery)

    scheduler_dag = SchedulerDag(nodes=tuple(scheduler_nodes)) if scheduler_nodes else None
    return WorkflowOrchestrationEvaluation(
        workflow_plan_id=plan.id,
        step_handoffs=tuple(handoffs),
        scheduler_dag=scheduler_dag,
        scheduler_recoveries=tuple(scheduler_recoveries),
    )


def _validate_projections(
    *,
    plan: ScientificWorkflowPlan,
    gates: WorkflowScientificGateEvaluation,
    recovery: WorkflowRecoveryPolicyEvaluation,
) -> None:
    try:
        validate_workflow_plan_recipe_contract(plan)
    except WorkflowRecipeContractError as error:
        raise WorkflowOrchestrationError(str(error)) from error
    if gates.workflow_plan_id != plan.id or recovery.workflow_plan_id != plan.id:
        raise WorkflowOrchestrationError(
            "workflow gate/recovery projections must belong to the requested plan"
        )
    expected = tuple(step.key for step in plan.steps)
    if tuple(item.step_key for item in gates.binding_selections) != expected:
        raise WorkflowOrchestrationError(
            "workflow binding selections do not match canonical plan step order"
        )
    if tuple(item.step_key for item in gates.step_gates) != expected:
        raise WorkflowOrchestrationError(
            "workflow step gates do not match canonical plan step order"
        )
    if tuple(item.step_key for item in recovery.step_policies) != expected:
        raise WorkflowOrchestrationError(
            "workflow recovery policies do not match canonical plan step order"
        )


def _validate_execution_sources(
    *,
    plan: ScientificWorkflowPlan,
    gates: WorkflowScientificGateEvaluation,
    selections: dict[str, WorkflowBindingSelection],
    execution_sources: tuple[WorkflowExecutionSource, ...],
) -> dict[str, WorkflowExecutionSource]:
    result: dict[str, WorkflowExecutionSource] = {}
    for source in execution_sources:
        if source.step_key in result:
            raise WorkflowOrchestrationError(
                "workflow execution sources must be unique by step_key"
            )
        try:
            plan.step(source.step_key)
        except KeyError as error:
            raise WorkflowOrchestrationError(
                f"unknown workflow execution step: {source.step_key}"
            ) from error
        selection = selections[source.step_key]
        gate = gates.step(source.step_key)
        if source.binding.workflow_plan_id != plan.id:
            raise WorkflowOrchestrationError(
                "workflow execution source binding belongs to another plan"
            )
        if selection.current_binding is None or selection.current_calculation is None:
            raise WorkflowOrchestrationError(
                "workflow execution source requires a current binding generation"
            )
        if source.binding.id != selection.current_binding.id:
            raise WorkflowOrchestrationError(
                "workflow execution source is bound to a superseded generation"
            )
        if source.calculation.id != selection.current_calculation.id:
            raise WorkflowOrchestrationError(
                "workflow execution source Calculation is not the current generation"
            )
        if (
            gate.current_binding_id != source.binding.id
            or gate.calculation_id != source.calculation.id
        ):
            raise WorkflowOrchestrationError(
                "workflow execution source does not match the exact Block 5 gate"
            )
        result[source.step_key] = source
    return result


def _reconcile_step(
    *,
    plan: ScientificWorkflowPlan,
    gate: object,
    selection: WorkflowBindingSelection,
    policy: WorkflowStepRecoveryPolicy,
    incoming_edges: tuple[WorkflowEdgeGate, ...],
    execution_source: WorkflowExecutionSource | None,
) -> tuple[
    WorkflowStepOrchestration,
    SchedulerDagNode | None,
    WorkflowSchedulerRecoveryHandoff | None,
]:
    # Kept local to avoid exposing a second public gate type hierarchy.
    workflow_gate = gate
    if not hasattr(workflow_gate, "step_key"):
        raise WorkflowOrchestrationError("invalid workflow step gate")
    step_key = workflow_gate.step_key
    state = workflow_gate.scientific_state
    readiness = workflow_gate.readiness

    if policy.step_key != step_key:
        raise WorkflowOrchestrationError("recovery policy step does not match workflow gate")

    if policy.action is WorkflowRecoveryAction.NONE:
        if state is WorkflowStepScientificState.UNMATERIALIZED:
            _forbid_execution_source(execution_source, "unmaterialized step")
            if readiness is not WorkflowStepReadiness.READY:
                raise WorkflowOrchestrationError(
                    "unmaterialized NONE policy requires Block 5 READY state"
                )
            target, source_binding_id = _materialization_target(
                plan=plan,
                incoming_edges=incoming_edges,
            )
            return (
                _handoff(
                    step_key=step_key,
                    action=WorkflowOrchestrationAction.MATERIALIZE_STEP,
                    current_binding_id=None,
                    calculation_id=None,
                    target_input_structure_snapshot_id=target,
                    source_binding_id=source_binding_id,
                    reason_codes=("ordinary_ready_step_materialization",),
                ),
                None,
                None,
            )
        if state is WorkflowStepScientificState.PASSED:
            _forbid_execution_source(execution_source, "satisfied step")
            return (
                _handoff(
                    step_key=step_key,
                    action=WorkflowOrchestrationAction.SATISFIED,
                    current_binding_id=workflow_gate.current_binding_id,
                    calculation_id=workflow_gate.calculation_id,
                    reason_codes=("current_generation_scientifically_satisfied",),
                ),
                None,
                None,
            )
        raise WorkflowOrchestrationError(
            "NONE recovery policy is incompatible with current workflow scientific state"
        )

    if policy.action is WorkflowRecoveryAction.WAIT_FOR_PREREQUISITE:
        if state is WorkflowStepScientificState.IN_PROGRESS:
            calculation = _require_current_calculation(selection)
            if calculation.status in {
                CalculationScientificStatus.DRAFT,
                CalculationScientificStatus.READY,
            }:
                if execution_source is None:
                    return (
                        _handoff(
                            step_key=step_key,
                            action=WorkflowOrchestrationAction.EXECUTION_PLAN_REQUIRED,
                            current_binding_id=workflow_gate.current_binding_id,
                            calculation_id=workflow_gate.calculation_id,
                            reason_codes=("current_generation_requires_execution_plan",),
                        ),
                        None,
                        None,
                    )
                _validate_ordinary_execution_source(execution_source)
                node = _scheduler_node(selection=selection, source=execution_source)
                return (
                    _handoff(
                        step_key=step_key,
                        action=WorkflowOrchestrationAction.EXECUTION_READY,
                        current_binding_id=workflow_gate.current_binding_id,
                        calculation_id=workflow_gate.calculation_id,
                        execution_plan_hash=execution_source.plan.plan_hash,
                        scheduler_node_id=node.node_id,
                        reason_codes=("current_generation_ready_for_execution_handoff",),
                    ),
                    node,
                    None,
                )
            if calculation.status in {
                CalculationScientificStatus.SUBMITTED,
                CalculationScientificStatus.RUNNING,
                CalculationScientificStatus.PARSING,
            }:
                _forbid_execution_source(execution_source, "already in-flight step")
                return (
                    _handoff(
                        step_key=step_key,
                        action=WorkflowOrchestrationAction.EXECUTION_IN_FLIGHT,
                        current_binding_id=workflow_gate.current_binding_id,
                        calculation_id=workflow_gate.calculation_id,
                        reason_codes=(f"calculation_{calculation.status.value}_already_in_flight",),
                    ),
                    None,
                    None,
                )
        _forbid_execution_source(execution_source, "waiting step")
        return (
            _handoff(
                step_key=step_key,
                action=WorkflowOrchestrationAction.WAIT,
                current_binding_id=workflow_gate.current_binding_id,
                calculation_id=workflow_gate.calculation_id,
                reason_codes=policy.reason_codes or ("workflow_prerequisite_not_ready",),
            ),
            None,
            None,
        )

    if policy.action is WorkflowRecoveryAction.REMATERIALIZE_STEP:
        _forbid_execution_source(execution_source, "rematerialization step")
        if policy.previous_binding_id is None or policy.target_input_structure_snapshot_id is None:
            raise WorkflowOrchestrationError(
                "rematerialization policy lacks previous binding or target structure"
            )
        target, source_binding_id = _materialization_target(
            plan=plan,
            incoming_edges=incoming_edges,
        )
        if target != policy.target_input_structure_snapshot_id:
            raise WorkflowOrchestrationError(
                "recovery materialization target does not match current scientific lineage"
            )
        return (
            _handoff(
                step_key=step_key,
                action=WorkflowOrchestrationAction.MATERIALIZE_STEP,
                current_binding_id=workflow_gate.current_binding_id,
                calculation_id=workflow_gate.calculation_id,
                previous_binding_id=policy.previous_binding_id,
                target_input_structure_snapshot_id=target,
                source_binding_id=source_binding_id,
                materialization_reason=policy.materialization_reason,
                reason_codes=policy.reason_codes or ("workflow_step_rematerialization",),
            ),
            None,
            None,
        )

    if policy.action is WorkflowRecoveryAction.EXECUTION_RECOVERY:
        if execution_source is None:
            raise WorkflowOrchestrationError(
                "execution recovery policy requires an exact WorkflowExecutionSource"
            )
        decision = _validate_recovery_execution_source(
            policy=policy,
            source=execution_source,
        )
        if decision.action is RecoveryAction.RETRY_SAME_ATTEMPT:
            return (
                _recovery_handoff(
                    step_key=step_key,
                    workflow_gate=workflow_gate,
                    action=WorkflowOrchestrationAction.RETRY_SAME_ATTEMPT,
                    source=execution_source,
                    decision=decision,
                ),
                None,
                None,
            )
        if decision.action is RecoveryAction.RESUBMIT_SAME_ATTEMPT:
            return (
                _recovery_handoff(
                    step_key=step_key,
                    workflow_gate=workflow_gate,
                    action=WorkflowOrchestrationAction.RESUBMIT_SAME_ATTEMPT,
                    source=execution_source,
                    decision=decision,
                ),
                None,
                None,
            )
        if decision.action is RecoveryAction.NEW_EXECUTION_ATTEMPT:
            node = _scheduler_node(selection=selection, source=execution_source)
            scheduler_recovery = WorkflowSchedulerRecoveryHandoff(
                node_id=node.node_id,
                decision=decision,
            )
            return (
                _recovery_handoff(
                    step_key=step_key,
                    workflow_gate=workflow_gate,
                    action=WorkflowOrchestrationAction.EXECUTION_RECOVERY_READY,
                    source=execution_source,
                    decision=decision,
                    scheduler_node_id=node.node_id,
                ),
                node,
                scheduler_recovery,
            )
        raise WorkflowOrchestrationError(
            "Block 7 execution recovery received a non-execution RecoveryAction"
        )

    _forbid_execution_source(execution_source, "non-execution policy")
    action_map = {
        WorkflowRecoveryAction.RECOVERY_DECISION_REQUIRED: (
            WorkflowOrchestrationAction.RECOVERY_DECISION_REQUIRED
        ),
        WorkflowRecoveryAction.NEW_WORKFLOW_PLAN_REQUIRED: (
            WorkflowOrchestrationAction.NEW_WORKFLOW_PLAN_REQUIRED
        ),
        WorkflowRecoveryAction.MANUAL_REVIEW_REQUIRED: (
            WorkflowOrchestrationAction.MANUAL_REVIEW_REQUIRED
        ),
    }
    try:
        action = action_map[policy.action]
    except KeyError as error:
        raise WorkflowOrchestrationError(
            f"unsupported WorkflowRecoveryAction: {policy.action.value}"
        ) from error
    return (
        _handoff(
            step_key=step_key,
            action=action,
            current_binding_id=workflow_gate.current_binding_id,
            calculation_id=workflow_gate.calculation_id,
            reason_codes=policy.reason_codes or (policy.action.value,),
        ),
        None,
        None,
    )


def _materialization_target(
    *,
    plan: ScientificWorkflowPlan,
    incoming_edges: tuple[WorkflowEdgeGate, ...],
) -> tuple[StructureSnapshotId, WorkflowStepBindingId | None]:
    if not incoming_edges:
        return plan.root_structure_snapshot_id, None
    if len(incoming_edges) != 1:
        raise WorkflowOrchestrationError(
            "Block 7 supports exactly one incoming scientific edge per downstream step"
        )
    edge = incoming_edges[0]
    if edge.role != WORKFLOW_EDGE_ACCEPTED_STRUCTURE:
        raise WorkflowOrchestrationError(
            "Block 7 supports only accepted_structure materialization edges"
        )
    if edge.verdict is not WorkflowEdgeGateVerdict.OPEN:
        raise WorkflowOrchestrationError(
            "materialization handoff requires an open accepted_structure edge"
        )
    if edge.accepted_structure_snapshot_id is None or edge.source_binding_id is None:
        raise WorkflowOrchestrationError(
            "open accepted_structure edge lacks exact source binding/StructureSnapshot identity"
        )
    return edge.accepted_structure_snapshot_id, edge.source_binding_id


def _validate_ordinary_execution_source(source: WorkflowExecutionSource) -> None:
    if source.recovery_decision is not None:
        raise WorkflowOrchestrationError(
            "ordinary execution handoff cannot carry a RecoveryDecision"
        )


def _validate_recovery_execution_source(
    *,
    policy: WorkflowStepRecoveryPolicy,
    source: WorkflowExecutionSource,
) -> RecoveryDecision:
    decision = source.recovery_decision
    if decision is None:
        raise WorkflowOrchestrationError(
            "execution recovery source requires the exact RecoveryDecision"
        )
    if policy.recovery_decision_hash != decision.decision_hash:
        raise WorkflowOrchestrationError(
            "execution recovery decision does not match Block 6 policy"
        )
    if policy.execution_recovery_action is not decision.action:
        raise WorkflowOrchestrationError(
            "execution recovery action does not match Block 6 policy"
        )
    if policy.source_execution_plan_hash != decision.source_plan_hash:
        raise WorkflowOrchestrationError(
            "Block 6 source ExecutionPlan hash does not match RecoveryDecision"
        )
    if decision.requires_new_execution_plan:
        if decision.target_execution_hash is None:
            raise WorkflowOrchestrationError(
                "recovery requiring a new ExecutionPlan lacks target execution hash"
            )
        if source.plan.plan_hash == decision.source_plan_hash:
            raise WorkflowOrchestrationError(
                "execution-tuning recovery requires a distinct dispatch ExecutionPlan"
            )
        if source.plan.execution_settings.execution_hash != decision.target_execution_hash:
            raise WorkflowOrchestrationError(
                "dispatch ExecutionPlan does not match recovery target execution hash"
            )
    elif source.plan.plan_hash != decision.source_plan_hash:
        raise WorkflowOrchestrationError(
            "same-plan recovery must hand off the exact source ExecutionPlan"
        )
    return decision


def _scheduler_node(
    *,
    selection: WorkflowBindingSelection,
    source: WorkflowExecutionSource,
) -> SchedulerDagNode:
    binding = selection.current_binding
    if binding is None:
        raise WorkflowOrchestrationError(
            "scheduler handoff requires a current workflow binding"
        )
    node_id = f"workflow-{source.step_key}-g{binding.generation}"
    return SchedulerDagNode(
        node_id=node_id,
        calculation=source.calculation,
        plan=source.plan,
        depends_on=(),
    )


def _recovery_handoff(
    *,
    step_key: str,
    workflow_gate: object,
    action: WorkflowOrchestrationAction,
    source: WorkflowExecutionSource,
    decision: RecoveryDecision,
    scheduler_node_id: str | None = None,
) -> WorkflowStepOrchestration:
    return _handoff(
        step_key=step_key,
        action=action,
        current_binding_id=workflow_gate.current_binding_id,
        calculation_id=workflow_gate.calculation_id,
        execution_plan_hash=source.plan.plan_hash,
        scheduler_node_id=scheduler_node_id,
        execution_recovery_action=decision.action,
        recovery_decision_hash=decision.decision_hash,
        reason_codes=("v04_recovery_execution_handoff",),
    )


def _require_current_calculation(selection: WorkflowBindingSelection) -> Calculation:
    if selection.current_binding is None or selection.current_calculation is None:
        raise WorkflowOrchestrationError(
            "workflow execution handoff requires a current binding generation"
        )
    return selection.current_calculation


def _forbid_execution_source(
    source: WorkflowExecutionSource | None,
    context: str,
) -> None:
    if source is not None:
        raise WorkflowOrchestrationError(
            f"{context} cannot consume an execution dispatch source"
        )


def _handoff(
    *,
    step_key: str,
    action: WorkflowOrchestrationAction,
    current_binding_id: WorkflowStepBindingId | None,
    calculation_id: CalculationId | None,
    previous_binding_id: WorkflowStepBindingId | None = None,
    target_input_structure_snapshot_id: StructureSnapshotId | None = None,
    source_binding_id: WorkflowStepBindingId | None = None,
    materialization_reason: str | None = None,
    execution_plan_hash: str | None = None,
    scheduler_node_id: str | None = None,
    execution_recovery_action: RecoveryAction | None = None,
    recovery_decision_hash: str | None = None,
    reason_codes: tuple[str, ...],
) -> WorkflowStepOrchestration:
    return WorkflowStepOrchestration(
        step_key=step_key,
        action=action,
        current_binding_id=current_binding_id,
        calculation_id=calculation_id,
        previous_binding_id=previous_binding_id,
        target_input_structure_snapshot_id=target_input_structure_snapshot_id,
        source_binding_id=source_binding_id,
        materialization_reason=materialization_reason,
        execution_plan_hash=execution_plan_hash,
        scheduler_node_id=scheduler_node_id,
        execution_recovery_action=execution_recovery_action,
        recovery_decision_hash=recovery_decision_hash,
        reason_codes=reason_codes,
    )

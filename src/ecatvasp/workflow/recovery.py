"""Workflow-level recovery and continuation policy for v0.6 Block 6."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from enum import StrEnum

from ecatvasp.domain import Calculation, ScientificWorkflowPlan, WorkflowStepBinding
from ecatvasp.domain.ids import (
    CalculationId,
    StructureSnapshotId,
    WorkflowPlanId,
    WorkflowStepBindingId,
)
from ecatvasp.execution import RecoveryAction, RecoveryDecision
from ecatvasp.vasp.execution_plan import ExecutionPlan
from ecatvasp.workflow.gates import (
    WorkflowBindingSelection,
    WorkflowEdgeGate,
    WorkflowEdgeGateVerdict,
    WorkflowScientificGateEvaluation,
    WorkflowStepGate,
    WorkflowStepReadiness,
    WorkflowStepScientificState,
)
from ecatvasp.workflow.recipes import (
    WORKFLOW_EDGE_ACCEPTED_STRUCTURE,
    WorkflowRecipeContractError,
    validate_workflow_plan_recipe_contract,
)

WORKFLOW_RECOVERY_REASON_PREFIX = "workflow recovery"
WORKFLOW_UPSTREAM_SUPERSESSION_REASON = (
    "workflow recovery: rematerialize from current accepted upstream structure"
)


class WorkflowRecoveryPolicyError(ValueError):
    """Raised when a workflow recovery policy cannot be derived without guessing."""


class WorkflowRecoveryAction(StrEnum):
    """Pure workflow-level disposition for one logical step."""

    NONE = "none"
    WAIT_FOR_PREREQUISITE = "wait_for_prerequisite"
    RECOVERY_DECISION_REQUIRED = "recovery_decision_required"
    EXECUTION_RECOVERY = "execution_recovery"
    REMATERIALIZE_STEP = "rematerialize_step"
    NEW_WORKFLOW_PLAN_REQUIRED = "new_workflow_plan_required"
    MANUAL_REVIEW_REQUIRED = "manual_review_required"


@dataclass(frozen=True, slots=True)
class WorkflowRecoverySource:
    """Exact current workflow generation plus one v0.4 execution-layer recovery decision."""

    step_key: str
    binding: WorkflowStepBinding
    calculation: Calculation
    execution_plan: ExecutionPlan
    decision: RecoveryDecision

    def __post_init__(self) -> None:
        if not self.step_key.strip():
            raise WorkflowRecoveryPolicyError("workflow recovery source requires a step_key")
        if self.binding.step_key != self.step_key:
            raise WorkflowRecoveryPolicyError(
                "workflow recovery source binding belongs to another step"
            )
        if self.binding.calculation_id != self.calculation.id:
            raise WorkflowRecoveryPolicyError(
                "workflow recovery source binding does not reference its Calculation"
            )
        if (
            self.binding.resolved_input_structure_snapshot_id
            != self.calculation.input_structure_snapshot_id
        ):
            raise WorkflowRecoveryPolicyError(
                "workflow recovery source binding input does not match Calculation input"
            )
        if self.execution_plan.calculation_id != self.calculation.id:
            raise WorkflowRecoveryPolicyError(
                "workflow recovery ExecutionPlan belongs to another Calculation"
            )
        if self.execution_plan.recipe_id != self.calculation.recipe_id:
            raise WorkflowRecoveryPolicyError(
                "workflow recovery ExecutionPlan recipe does not match Calculation"
            )
        if self.decision.source_plan_hash != self.execution_plan.plan_hash:
            raise WorkflowRecoveryPolicyError(
                "RecoveryDecision is not bound to the supplied source ExecutionPlan"
            )
        if (
            self.decision.source_execution_hash
            != self.execution_plan.execution_settings.execution_hash
        ):
            raise WorkflowRecoveryPolicyError(
                "RecoveryDecision source execution hash does not match ExecutionPlan"
            )
        _validate_recovery_decision_shape(self.decision)


@dataclass(frozen=True, slots=True)
class WorkflowStepRecoveryPolicy:
    """Auditable recovery/continuation disposition for one current workflow step."""

    step_key: str
    action: WorkflowRecoveryAction
    current_binding_id: WorkflowStepBindingId | None = None
    calculation_id: CalculationId | None = None
    previous_binding_id: WorkflowStepBindingId | None = None
    target_input_structure_snapshot_id: StructureSnapshotId | None = None
    source_execution_plan_hash: str | None = None
    execution_recovery_action: RecoveryAction | None = None
    recovery_decision_hash: str | None = None
    materialization_reason: str | None = None
    reason_codes: tuple[str, ...] = ()

    @property
    def requires_new_binding_generation(self) -> bool:
        return self.action is WorkflowRecoveryAction.REMATERIALIZE_STEP

    @property
    def requires_new_workflow_plan(self) -> bool:
        return self.action is WorkflowRecoveryAction.NEW_WORKFLOW_PLAN_REQUIRED

    @property
    def preserves_current_calculation(self) -> bool:
        return self.action is WorkflowRecoveryAction.EXECUTION_RECOVERY


@dataclass(frozen=True, slots=True)
class WorkflowRecoveryPolicyEvaluation:
    """Pure workflow recovery projection derived from Block 5 gates and v0.4 decisions."""

    workflow_plan_id: WorkflowPlanId
    step_policies: tuple[WorkflowStepRecoveryPolicy, ...]

    def step(self, step_key: str) -> WorkflowStepRecoveryPolicy:
        for item in self.step_policies:
            if item.step_key == step_key:
                return item
        raise KeyError(step_key)


def evaluate_workflow_recovery_policy(
    *,
    plan: ScientificWorkflowPlan,
    gates: WorkflowScientificGateEvaluation,
    recovery_sources: tuple[WorkflowRecoverySource, ...] = (),
) -> WorkflowRecoveryPolicyEvaluation:
    """Derive fail-closed workflow recovery policy without mutating or executing anything."""

    _validate_gate_projection(plan=plan, gates=gates)
    selection_by_step = {item.step_key: item for item in gates.binding_selections}
    edge_gates_by_downstream: dict[str, list[WorkflowEdgeGate]] = defaultdict(list)
    for edge_gate in gates.edge_gates:
        edge_gates_by_downstream[edge_gate.downstream_step_key].append(edge_gate)
    source_by_step = _validate_recovery_sources(
        plan=plan,
        gates=gates,
        selections=selection_by_step,
        recovery_sources=recovery_sources,
    )

    policies = tuple(
        _policy_for_step(
            plan=plan,
            gate=gates.step(step.key),
            selection=selection_by_step[step.key],
            incoming_edge_gates=tuple(edge_gates_by_downstream[step.key]),
            recovery_source=source_by_step.get(step.key),
        )
        for step in plan.steps
    )
    return WorkflowRecoveryPolicyEvaluation(
        workflow_plan_id=plan.id,
        step_policies=policies,
    )


def _validate_gate_projection(
    *,
    plan: ScientificWorkflowPlan,
    gates: WorkflowScientificGateEvaluation,
) -> None:
    try:
        validate_workflow_plan_recipe_contract(plan)
    except WorkflowRecipeContractError as error:
        raise WorkflowRecoveryPolicyError(str(error)) from error
    if gates.workflow_plan_id != plan.id:
        raise WorkflowRecoveryPolicyError(
            "workflow scientific gate evaluation belongs to another plan"
        )
    expected_keys = tuple(step.key for step in plan.steps)
    selection_keys = tuple(item.step_key for item in gates.binding_selections)
    gate_keys = tuple(item.step_key for item in gates.step_gates)
    if selection_keys != expected_keys or gate_keys != expected_keys:
        raise WorkflowRecoveryPolicyError(
            "workflow gate projection does not contain the canonical ordered step set"
        )
    expected_edges = {
        (edge.upstream_step_key, edge.downstream_step_key, edge.role)
        for edge in plan.edges
    }
    actual_edges = {
        (edge.upstream_step_key, edge.downstream_step_key, edge.role)
        for edge in gates.edge_gates
    }
    if actual_edges != expected_edges:
        raise WorkflowRecoveryPolicyError(
            "workflow gate projection does not contain the canonical edge set"
        )


def _validate_recovery_sources(
    *,
    plan: ScientificWorkflowPlan,
    gates: WorkflowScientificGateEvaluation,
    selections: dict[str, WorkflowBindingSelection],
    recovery_sources: tuple[WorkflowRecoverySource, ...],
) -> dict[str, WorkflowRecoverySource]:
    result: dict[str, WorkflowRecoverySource] = {}
    for source in recovery_sources:
        if source.step_key in result:
            raise WorkflowRecoveryPolicyError(
                "workflow recovery sources must be unique by step_key"
            )
        try:
            plan.step(source.step_key)
        except KeyError as error:
            raise WorkflowRecoveryPolicyError(
                f"unknown workflow recovery step: {source.step_key}"
            ) from error
        selection = selections[source.step_key]
        gate = gates.step(source.step_key)
        if source.binding.workflow_plan_id != plan.id:
            raise WorkflowRecoveryPolicyError(
                "workflow recovery source binding belongs to another plan"
            )
        if selection.current_binding is None or selection.current_calculation is None:
            raise WorkflowRecoveryPolicyError(
                "workflow recovery source requires a current binding generation"
            )
        if source.binding.id != selection.current_binding.id:
            raise WorkflowRecoveryPolicyError(
                "workflow recovery source is bound to a superseded workflow generation"
            )
        if source.calculation.id != selection.current_calculation.id:
            raise WorkflowRecoveryPolicyError(
                "workflow recovery source Calculation is not the current generation"
            )
        if gate.current_binding_id != source.binding.id or gate.calculation_id != source.calculation.id:
            raise WorkflowRecoveryPolicyError(
                "workflow recovery source does not match the exact Block 5 step gate"
            )
        result[source.step_key] = source
    return result


def _policy_for_step(
    *,
    plan: ScientificWorkflowPlan,
    gate: WorkflowStepGate,
    selection: WorkflowBindingSelection,
    incoming_edge_gates: tuple[WorkflowEdgeGate, ...],
    recovery_source: WorkflowRecoverySource | None,
) -> WorkflowStepRecoveryPolicy:
    state = gate.scientific_state

    if state is WorkflowStepScientificState.UNMATERIALIZED:
        if recovery_source is not None:
            raise WorkflowRecoveryPolicyError(
                "unmaterialized workflow step cannot consume a recovery decision"
            )
        if gate.readiness is WorkflowStepReadiness.READY:
            return _policy(
                gate=gate,
                action=WorkflowRecoveryAction.NONE,
                reason_codes=("ordinary_materialization_not_recovery_scope",),
            )
        return _policy(
            gate=gate,
            action=WorkflowRecoveryAction.WAIT_FOR_PREREQUISITE,
            reason_codes=("unmaterialized_step_waits_for_upstream_science",),
        )

    if state is WorkflowStepScientificState.IN_PROGRESS:
        if recovery_source is not None:
            raise WorkflowRecoveryPolicyError(
                "in-progress workflow step cannot consume a recovery decision before it blocks"
            )
        return _policy(
            gate=gate,
            action=WorkflowRecoveryAction.WAIT_FOR_PREREQUISITE,
            reason_codes=("current_generation_still_in_progress",),
        )

    if state is WorkflowStepScientificState.PASSED:
        if recovery_source is not None:
            raise WorkflowRecoveryPolicyError(
                "scientifically satisfied workflow step does not require recovery"
            )
        return _policy(
            gate=gate,
            action=WorkflowRecoveryAction.NONE,
            reason_codes=("current_generation_scientifically_satisfied",),
        )

    if "accepted_structure_binding_superseded" in gate.reason_codes:
        if recovery_source is not None:
            raise WorkflowRecoveryPolicyError(
                "workflow lineage supersession recovery does not consume an execution decision"
            )
        target = _open_downstream_target(incoming_edge_gates)
        if target is None:
            return _policy(
                gate=gate,
                action=WorkflowRecoveryAction.WAIT_FOR_PREREQUISITE,
                reason_codes=("current_upstream_accepted_structure_not_ready",),
            )
        binding = _require_current_binding(selection)
        return _policy(
            gate=gate,
            action=WorkflowRecoveryAction.REMATERIALIZE_STEP,
            previous_binding_id=binding.id,
            target_input_structure_snapshot_id=target,
            materialization_reason=WORKFLOW_UPSTREAM_SUPERSESSION_REASON,
            reason_codes=("accepted_structure_binding_superseded",),
        )

    if state is WorkflowStepScientificState.INVALID:
        return _manual_policy(
            gate=gate,
            recovery_source=recovery_source,
            reason="invalid_scientific_state_cannot_be_auto_recovered",
        )

    if state is WorkflowStepScientificState.SUPERSEDED:
        return _manual_policy(
            gate=gate,
            recovery_source=recovery_source,
            reason="current_generation_marked_superseded_requires_manual_resolution",
        )

    if state is WorkflowStepScientificState.STALE:
        return _manual_policy(
            gate=gate,
            recovery_source=recovery_source,
            reason="generic_scientific_staleness_requires_explicit_review",
        )

    if state is not WorkflowStepScientificState.BLOCKED:
        raise WorkflowRecoveryPolicyError(
            f"unsupported workflow scientific state: {state.value}"
        )

    if incoming_edge_gates and _open_downstream_target(incoming_edge_gates) is None:
        return _policy(
            gate=gate,
            action=WorkflowRecoveryAction.WAIT_FOR_PREREQUISITE,
            reason_codes=("current_scientific_input_is_not_available_from_upstream",),
        )

    if recovery_source is None:
        return _policy(
            gate=gate,
            action=WorkflowRecoveryAction.RECOVERY_DECISION_REQUIRED,
            reason_codes=("blocked_current_generation_requires_explicit_recovery_decision",),
        )

    return _map_recovery_decision(
        plan=plan,
        gate=gate,
        selection=selection,
        incoming_edge_gates=incoming_edge_gates,
        source=recovery_source,
    )


def _map_recovery_decision(
    *,
    plan: ScientificWorkflowPlan,
    gate: WorkflowStepGate,
    selection: WorkflowBindingSelection,
    incoming_edge_gates: tuple[WorkflowEdgeGate, ...],
    source: WorkflowRecoverySource,
) -> WorkflowStepRecoveryPolicy:
    decision = source.decision
    execution_actions = {
        RecoveryAction.RETRY_SAME_ATTEMPT,
        RecoveryAction.RESUBMIT_SAME_ATTEMPT,
        RecoveryAction.NEW_EXECUTION_ATTEMPT,
    }
    if decision.action in execution_actions:
        return _policy(
            gate=gate,
            action=WorkflowRecoveryAction.EXECUTION_RECOVERY,
            source_execution_plan_hash=source.execution_plan.plan_hash,
            execution_recovery_action=decision.action,
            recovery_decision_hash=decision.decision_hash,
            reason_codes=("v04_recovery_preserves_current_calculation",),
        )

    if decision.action is RecoveryAction.MANUAL_REVIEW_REQUIRED:
        return _manual_policy(
            gate=gate,
            recovery_source=source,
            reason="v04_recovery_decision_requires_manual_review",
        )

    if decision.action is RecoveryAction.NEW_STRUCTURE_AND_CALCULATION:
        return _policy(
            gate=gate,
            action=WorkflowRecoveryAction.NEW_WORKFLOW_PLAN_REQUIRED,
            source_execution_plan_hash=source.execution_plan.plan_hash,
            execution_recovery_action=decision.action,
            recovery_decision_hash=decision.decision_hash,
            reason_codes=(
                "structure_continuation_changes_workflow_scientific_input",
                "current_plan_cannot_accept_arbitrary_continuation_structure",
            ),
        )

    if decision.action is RecoveryAction.NEW_CALCULATION:
        binding = _require_current_binding(selection)
        target = _materialization_target(
            plan=plan,
            selection=selection,
            incoming_edge_gates=incoming_edge_gates,
        )
        if target is None:
            return _policy(
                gate=gate,
                action=WorkflowRecoveryAction.WAIT_FOR_PREREQUISITE,
                source_execution_plan_hash=source.execution_plan.plan_hash,
                execution_recovery_action=decision.action,
                recovery_decision_hash=decision.decision_hash,
                reason_codes=("new_calculation_waits_for_current_scientific_input",),
            )
        return _policy(
            gate=gate,
            action=WorkflowRecoveryAction.REMATERIALIZE_STEP,
            previous_binding_id=binding.id,
            target_input_structure_snapshot_id=target,
            source_execution_plan_hash=source.execution_plan.plan_hash,
            execution_recovery_action=decision.action,
            recovery_decision_hash=decision.decision_hash,
            materialization_reason=(
                f"{WORKFLOW_RECOVERY_REASON_PREFIX}: {decision.change_layer.value}"
            ),
            reason_codes=("v04_recovery_requires_new_scientific_calculation",),
        )

    raise WorkflowRecoveryPolicyError(
        f"unsupported v0.4 RecoveryAction: {decision.action.value}"
    )


def _materialization_target(
    *,
    plan: ScientificWorkflowPlan,
    selection: WorkflowBindingSelection,
    incoming_edge_gates: tuple[WorkflowEdgeGate, ...],
) -> StructureSnapshotId | None:
    if not incoming_edge_gates:
        return plan.root_structure_snapshot_id
    target = _open_downstream_target(incoming_edge_gates)
    if target is None:
        return None
    binding = _require_current_binding(selection)
    if binding.resolved_input_structure_snapshot_id != target:
        return target
    return binding.resolved_input_structure_snapshot_id


def _open_downstream_target(
    incoming_edge_gates: tuple[WorkflowEdgeGate, ...],
) -> StructureSnapshotId | None:
    if not incoming_edge_gates:
        return None
    if len(incoming_edge_gates) != 1:
        raise WorkflowRecoveryPolicyError(
            "Block 6 supports exactly one incoming scientific edge per downstream step"
        )
    edge = incoming_edge_gates[0]
    if edge.role != WORKFLOW_EDGE_ACCEPTED_STRUCTURE:
        raise WorkflowRecoveryPolicyError(
            "Block 6 supports only accepted_structure downstream recovery"
        )
    if edge.verdict is not WorkflowEdgeGateVerdict.OPEN:
        return None
    if edge.accepted_structure_snapshot_id is None:
        raise WorkflowRecoveryPolicyError(
            "open accepted_structure edge lacks its exact StructureSnapshot id"
        )
    return edge.accepted_structure_snapshot_id


def _require_current_binding(selection: WorkflowBindingSelection) -> WorkflowStepBinding:
    if selection.current_binding is None or selection.current_calculation is None:
        raise WorkflowRecoveryPolicyError(
            "workflow recovery requires a current binding generation"
        )
    return selection.current_binding


def _manual_policy(
    *,
    gate: WorkflowStepGate,
    recovery_source: WorkflowRecoverySource | None,
    reason: str,
) -> WorkflowStepRecoveryPolicy:
    return _policy(
        gate=gate,
        action=WorkflowRecoveryAction.MANUAL_REVIEW_REQUIRED,
        source_execution_plan_hash=(
            None if recovery_source is None else recovery_source.execution_plan.plan_hash
        ),
        execution_recovery_action=(
            None if recovery_source is None else recovery_source.decision.action
        ),
        recovery_decision_hash=(
            None if recovery_source is None else recovery_source.decision.decision_hash
        ),
        reason_codes=(reason,),
    )


def _policy(
    *,
    gate: WorkflowStepGate,
    action: WorkflowRecoveryAction,
    previous_binding_id: WorkflowStepBindingId | None = None,
    target_input_structure_snapshot_id: StructureSnapshotId | None = None,
    source_execution_plan_hash: str | None = None,
    execution_recovery_action: RecoveryAction | None = None,
    recovery_decision_hash: str | None = None,
    materialization_reason: str | None = None,
    reason_codes: tuple[str, ...],
) -> WorkflowStepRecoveryPolicy:
    return WorkflowStepRecoveryPolicy(
        step_key=gate.step_key,
        action=action,
        current_binding_id=gate.current_binding_id,
        calculation_id=gate.calculation_id,
        previous_binding_id=previous_binding_id,
        target_input_structure_snapshot_id=target_input_structure_snapshot_id,
        source_execution_plan_hash=source_execution_plan_hash,
        execution_recovery_action=execution_recovery_action,
        recovery_decision_hash=recovery_decision_hash,
        materialization_reason=materialization_reason,
        reason_codes=reason_codes,
    )


def _validate_recovery_decision_shape(decision: RecoveryDecision) -> None:
    if decision.requires_new_structure_snapshot and not decision.requires_new_calculation:
        raise WorkflowRecoveryPolicyError(
            "RecoveryDecision cannot require a new structure without a new Calculation"
        )
    if decision.requires_new_execution_plan and decision.target_execution_hash is None:
        raise WorkflowRecoveryPolicyError(
            "RecoveryDecision requiring a new ExecutionPlan lacks target execution hash"
        )

    if decision.action in {
        RecoveryAction.RETRY_SAME_ATTEMPT,
        RecoveryAction.RESUBMIT_SAME_ATTEMPT,
        RecoveryAction.NEW_EXECUTION_ATTEMPT,
    }:
        if (
            not decision.scientific_identity_preserved
            or decision.requires_new_calculation
            or decision.requires_new_structure_snapshot
        ):
            raise WorkflowRecoveryPolicyError(
                "execution recovery action must preserve scientific identity"
            )
        if (
            decision.action is RecoveryAction.NEW_EXECUTION_ATTEMPT
            and not decision.requires_new_execution_attempt
        ):
            raise WorkflowRecoveryPolicyError(
                "NEW_EXECUTION_ATTEMPT decision must require a new attempt"
            )
        if (
            decision.action
            in {RecoveryAction.RETRY_SAME_ATTEMPT, RecoveryAction.RESUBMIT_SAME_ATTEMPT}
            and decision.requires_new_execution_attempt
        ):
            raise WorkflowRecoveryPolicyError(
                "same-attempt recovery action cannot require a new attempt"
            )
        return

    if decision.action is RecoveryAction.NEW_CALCULATION:
        if (
            decision.scientific_identity_preserved
            or not decision.requires_new_calculation
            or decision.requires_new_structure_snapshot
        ):
            raise WorkflowRecoveryPolicyError(
                "NEW_CALCULATION decision has inconsistent scientific identity flags"
            )
        return

    if decision.action is RecoveryAction.NEW_STRUCTURE_AND_CALCULATION:
        if (
            decision.scientific_identity_preserved
            or not decision.requires_new_calculation
            or not decision.requires_new_structure_snapshot
        ):
            raise WorkflowRecoveryPolicyError(
                "structure continuation decision has inconsistent identity flags"
            )
        return

    if decision.action is RecoveryAction.MANUAL_REVIEW_REQUIRED:
        return

    raise WorkflowRecoveryPolicyError(
        f"unsupported RecoveryDecision action: {decision.action.value}"
    )

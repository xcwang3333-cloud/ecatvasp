from __future__ import annotations

from dataclasses import replace

import pytest

from ecatvasp.domain import (
    Calculation,
    CalculationScientificStatus,
    ExecutionSettings,
    WorkflowRecipeIdentity,
    WorkflowStepBinding,
    new_method_fingerprint_id,
)
from ecatvasp.domain.ids import (
    new_artifact_id,
    new_project_id,
    new_structure_snapshot_id,
)
from ecatvasp.execution import (
    ExecutionEvidence,
    RecoveryAction,
    RecoveryCause,
    RecoveryRequest,
    classify_recovery,
    derive_execution_recovery_plan,
)
from ecatvasp.provenance import FreshnessState
from ecatvasp.vasp.contracts import VaspSystemContext, VaspSystemKind
from ecatvasp.vasp.execution_plan import (
    ExecutionPlan,
    PotcarResolutionEntry,
    PotcarResolutionRequest,
    VaspRuntimeConstraints,
)
from ecatvasp.workflow import (
    WORKFLOW_EDGE_ACCEPTED_STRUCTURE,
    WORKFLOW_RECIPE_SLAB_SCIENTIFIC_PREPARATION,
    WorkflowBindingSelection,
    WorkflowEdgeGate,
    WorkflowEdgeGateVerdict,
    WorkflowOrchestrationAction,
    WorkflowOrchestrationError,
    WorkflowRecoveryAction,
    WorkflowRecoveryPolicyEvaluation,
    WorkflowScientificGateEvaluation,
    WorkflowStepGate,
    WorkflowStepReadiness,
    WorkflowStepRecoveryPolicy,
    WorkflowStepScientificState,
    WorkflowExecutionSource,
    plan_scientific_workflow,
    reconcile_workflow_orchestration,
)


def _plan():
    return plan_scientific_workflow(
        project_id=new_project_id(),
        workflow_recipe=WorkflowRecipeIdentity(
            WORKFLOW_RECIPE_SLAB_SCIENTIFIC_PREPARATION
        ),
        root_structure_snapshot_id=new_structure_snapshot_id(),
    ).plan


def _calculation(plan, step_key: str, *, status: CalculationScientificStatus) -> Calculation:
    step = plan.step(step_key)
    input_snapshot_id = plan.root_structure_snapshot_id
    return Calculation(
        project_id=plan.project_id,
        calculation_type=step.calculation_type,
        input_structure_snapshot_id=input_snapshot_id,
        recipe_id=step.recipe_id,
        method_fingerprint_id=new_method_fingerprint_id(),
        status=status,
    )


def _binding(plan, step_key: str, calculation: Calculation, *, generation: int = 1):
    return WorkflowStepBinding(
        workflow_plan_id=plan.id,
        step_key=step_key,
        generation=generation,
        calculation_id=calculation.id,
        resolved_input_structure_snapshot_id=calculation.input_structure_snapshot_id,
        materialization_reason="test workflow binding",
    )


def _execution_plan(calculation: Calculation, settings: ExecutionSettings | None = None):
    return ExecutionPlan(
        calculation_id=calculation.id,
        recipe_id=calculation.recipe_id,
        system_context=VaspSystemContext(VaspSystemKind.PERIODIC_3D),
        input_manifest_artifact_id=new_artifact_id(),
        input_manifest_sha256="a" * 64,
        preparation_hash="b" * 64,
        staging_inputs=(),
        potcar_resolution=PotcarResolutionRequest(
            family="PBE_54",
            core_method_hash="c" * 64,
            metadata_hash="d" * 64,
            entries=(PotcarResolutionEntry("C", "C", "e" * 64),),
        ),
        expected_outputs=(),
        runtime_constraints=VaspRuntimeConstraints(),
        execution_settings=settings or ExecutionSettings(executable="vasp_std"),
    )


def _projections(
    plan,
    *,
    selections: dict[str, WorkflowBindingSelection] | None = None,
    step_gates: dict[str, WorkflowStepGate] | None = None,
    edge_gates: dict[tuple[str, str, str], WorkflowEdgeGate] | None = None,
    policies: dict[str, WorkflowStepRecoveryPolicy] | None = None,
):
    selection_values = selections or {}
    gate_values = step_gates or {}
    policy_values = policies or {}
    ordered_selections = []
    ordered_gates = []
    ordered_policies = []
    for step in plan.steps:
        selection = selection_values.get(
            step.key,
            WorkflowBindingSelection(
                step_key=step.key,
                current_binding=None,
                current_calculation=None,
            ),
        )
        gate = gate_values.get(
            step.key,
            WorkflowStepGate(
                step_key=step.key,
                scientific_state=WorkflowStepScientificState.UNMATERIALIZED,
                readiness=WorkflowStepReadiness.WAITING,
                reason_codes=("default_waiting",),
            ),
        )
        policy = policy_values.get(
            step.key,
            WorkflowStepRecoveryPolicy(
                step_key=step.key,
                action=WorkflowRecoveryAction.WAIT_FOR_PREREQUISITE,
                current_binding_id=gate.current_binding_id,
                calculation_id=gate.calculation_id,
                reason_codes=("default_waiting",),
            ),
        )
        ordered_selections.append(selection)
        ordered_gates.append(gate)
        ordered_policies.append(policy)

    edge_values = edge_gates or {}
    ordered_edges = []
    for edge in plan.edges:
        key = (edge.upstream_step_key, edge.downstream_step_key, edge.role)
        ordered_edges.append(
            edge_values.get(
                key,
                WorkflowEdgeGate(
                    upstream_step_key=edge.upstream_step_key,
                    downstream_step_key=edge.downstream_step_key,
                    role=edge.role,
                    verdict=WorkflowEdgeGateVerdict.WAITING,
                    reason_codes=("default_waiting",),
                ),
            )
        )

    gates = WorkflowScientificGateEvaluation(
        workflow_plan_id=plan.id,
        binding_selections=tuple(ordered_selections),
        step_gates=tuple(ordered_gates),
        edge_gates=tuple(ordered_edges),
    )
    recovery = WorkflowRecoveryPolicyEvaluation(
        workflow_plan_id=plan.id,
        step_policies=tuple(ordered_policies),
    )
    return gates, recovery


def _current_projection(plan, calculation: Calculation, binding: WorkflowStepBinding):
    selection = WorkflowBindingSelection(
        step_key=binding.step_key,
        current_binding=binding,
        current_calculation=calculation,
    )
    gate = WorkflowStepGate(
        step_key=binding.step_key,
        scientific_state=WorkflowStepScientificState.IN_PROGRESS,
        readiness=WorkflowStepReadiness.WAITING,
        current_binding_id=binding.id,
        calculation_id=calculation.id,
        freshness_state=FreshnessState.FRESH,
        reason_codes=(f"calculation_{calculation.status.value}",),
    )
    policy = WorkflowStepRecoveryPolicy(
        step_key=binding.step_key,
        action=WorkflowRecoveryAction.WAIT_FOR_PREREQUISITE,
        current_binding_id=binding.id,
        calculation_id=calculation.id,
        reason_codes=("current_generation_still_in_progress",),
    )
    return selection, gate, policy


def test_ready_unmaterialized_root_becomes_exact_materialization_handoff() -> None:
    plan = _plan()
    relax_gate = WorkflowStepGate(
        step_key="relax",
        scientific_state=WorkflowStepScientificState.UNMATERIALIZED,
        readiness=WorkflowStepReadiness.READY,
        reason_codes=("no_current_binding",),
    )
    relax_policy = WorkflowStepRecoveryPolicy(
        step_key="relax",
        action=WorkflowRecoveryAction.NONE,
        reason_codes=("ordinary_materialization_not_recovery_scope",),
    )
    gates, recovery = _projections(
        plan,
        step_gates={"relax": relax_gate},
        policies={"relax": relax_policy},
    )

    result = reconcile_workflow_orchestration(plan=plan, gates=gates, recovery=recovery)

    handoff = result.step("relax")
    assert handoff.action is WorkflowOrchestrationAction.MATERIALIZE_STEP
    assert handoff.target_input_structure_snapshot_id == plan.root_structure_snapshot_id
    assert handoff.source_binding_id is None
    assert result.scheduler_dag is None


def test_ready_current_generation_requires_plan_then_enters_scheduler_handoff() -> None:
    plan = _plan()
    calculation = _calculation(plan, "relax", status=CalculationScientificStatus.READY)
    binding = _binding(plan, "relax", calculation)
    selection, gate, policy = _current_projection(plan, calculation, binding)
    gates, recovery = _projections(
        plan,
        selections={"relax": selection},
        step_gates={"relax": gate},
        policies={"relax": policy},
    )

    without_plan = reconcile_workflow_orchestration(
        plan=plan,
        gates=gates,
        recovery=recovery,
    )
    assert (
        without_plan.step("relax").action
        is WorkflowOrchestrationAction.EXECUTION_PLAN_REQUIRED
    )

    execution_plan = _execution_plan(calculation)
    with_plan = reconcile_workflow_orchestration(
        plan=plan,
        gates=gates,
        recovery=recovery,
        execution_sources=(
            WorkflowExecutionSource(
                step_key="relax",
                binding=binding,
                calculation=calculation,
                plan=execution_plan,
            ),
        ),
    )
    handoff = with_plan.step("relax")
    assert handoff.action is WorkflowOrchestrationAction.EXECUTION_READY
    assert handoff.execution_plan_hash == execution_plan.plan_hash
    assert with_plan.scheduler_dag is not None
    node = with_plan.scheduler_dag.nodes[0]
    assert node.calculation.id == calculation.id
    assert node.depends_on == ()
    assert with_plan.scheduler_recovery_decisions == {}


def test_running_generation_is_observational_and_cannot_be_dispatched_again() -> None:
    plan = _plan()
    calculation = _calculation(plan, "relax", status=CalculationScientificStatus.RUNNING)
    binding = _binding(plan, "relax", calculation)
    selection, gate, policy = _current_projection(plan, calculation, binding)
    gates, recovery = _projections(
        plan,
        selections={"relax": selection},
        step_gates={"relax": gate},
        policies={"relax": policy},
    )

    result = reconcile_workflow_orchestration(plan=plan, gates=gates, recovery=recovery)
    assert result.step("relax").action is WorkflowOrchestrationAction.EXECUTION_IN_FLIGHT

    with pytest.raises(WorkflowOrchestrationError, match="already in-flight"):
        reconcile_workflow_orchestration(
            plan=plan,
            gates=gates,
            recovery=recovery,
            execution_sources=(
                WorkflowExecutionSource(
                    step_key="relax",
                    binding=binding,
                    calculation=calculation,
                    plan=_execution_plan(calculation),
                ),
            ),
        )


def test_upstream_supersession_rematerialization_handoff_pins_current_edge() -> None:
    plan = _plan()
    relax_calculation = _calculation(
        plan,
        "relax",
        status=CalculationScientificStatus.CONVERGED,
    )
    relax_binding = _binding(plan, "relax", relax_calculation)
    old_snapshot = new_structure_snapshot_id()
    static_step = plan.step("static")
    static_calculation = Calculation(
        project_id=plan.project_id,
        calculation_type=static_step.calculation_type,
        input_structure_snapshot_id=old_snapshot,
        recipe_id=static_step.recipe_id,
        method_fingerprint_id=new_method_fingerprint_id(),
        status=CalculationScientificStatus.CONVERGED,
    )
    static_binding = _binding(plan, "static", static_calculation)
    new_snapshot = new_structure_snapshot_id()

    selections = {
        "relax": WorkflowBindingSelection(
            step_key="relax",
            current_binding=relax_binding,
            current_calculation=relax_calculation,
        ),
        "static": WorkflowBindingSelection(
            step_key="static",
            current_binding=static_binding,
            current_calculation=static_calculation,
        ),
    }
    relax_gate = WorkflowStepGate(
        step_key="relax",
        scientific_state=WorkflowStepScientificState.PASSED,
        readiness=WorkflowStepReadiness.SATISFIED,
        current_binding_id=relax_binding.id,
        calculation_id=relax_calculation.id,
        freshness_state=FreshnessState.FRESH,
        reason_codes=("calculation_converged_and_fresh",),
    )
    static_gate = WorkflowStepGate(
        step_key="static",
        scientific_state=WorkflowStepScientificState.STALE,
        readiness=WorkflowStepReadiness.BLOCKED,
        current_binding_id=static_binding.id,
        calculation_id=static_calculation.id,
        freshness_state=FreshnessState.FRESH,
        reason_codes=("accepted_structure_binding_superseded",),
    )
    edge_key = ("relax", "static", WORKFLOW_EDGE_ACCEPTED_STRUCTURE)
    open_edge = WorkflowEdgeGate(
        upstream_step_key="relax",
        downstream_step_key="static",
        role=WORKFLOW_EDGE_ACCEPTED_STRUCTURE,
        verdict=WorkflowEdgeGateVerdict.OPEN,
        source_binding_id=relax_binding.id,
        accepted_structure_snapshot_id=new_snapshot,
        reason_codes=("accepted_structure_current_and_fresh",),
    )
    policies = {
        "relax": WorkflowStepRecoveryPolicy(
            step_key="relax",
            action=WorkflowRecoveryAction.NONE,
            current_binding_id=relax_binding.id,
            calculation_id=relax_calculation.id,
            reason_codes=("current_generation_scientifically_satisfied",),
        ),
        "static": WorkflowStepRecoveryPolicy(
            step_key="static",
            action=WorkflowRecoveryAction.REMATERIALIZE_STEP,
            current_binding_id=static_binding.id,
            calculation_id=static_calculation.id,
            previous_binding_id=static_binding.id,
            target_input_structure_snapshot_id=new_snapshot,
            materialization_reason="workflow recovery: current accepted structure",
            reason_codes=("accepted_structure_binding_superseded",),
        ),
    }
    gates, recovery = _projections(
        plan,
        selections=selections,
        step_gates={"relax": relax_gate, "static": static_gate},
        edge_gates={edge_key: open_edge},
        policies=policies,
    )

    result = reconcile_workflow_orchestration(plan=plan, gates=gates, recovery=recovery)
    handoff = result.step("static")
    assert handoff.action is WorkflowOrchestrationAction.MATERIALIZE_STEP
    assert handoff.previous_binding_id == static_binding.id
    assert handoff.target_input_structure_snapshot_id == new_snapshot
    assert handoff.source_binding_id == relax_binding.id


def test_new_execution_attempt_recovery_enters_existing_scheduler_dag_contract() -> None:
    plan = _plan()
    calculation = _calculation(plan, "relax", status=CalculationScientificStatus.FAILED)
    binding = _binding(plan, "relax", calculation)
    source_plan = _execution_plan(calculation)
    decision = classify_recovery(
        plan=source_plan,
        request=RecoveryRequest(
            cause=RecoveryCause.VASP_FAILURE,
            evidence=ExecutionEvidence.VASP_LAUNCH_CONFIRMED,
        ),
    )
    selection = WorkflowBindingSelection(
        step_key="relax",
        current_binding=binding,
        current_calculation=calculation,
    )
    gate = WorkflowStepGate(
        step_key="relax",
        scientific_state=WorkflowStepScientificState.BLOCKED,
        readiness=WorkflowStepReadiness.BLOCKED,
        current_binding_id=binding.id,
        calculation_id=calculation.id,
        freshness_state=FreshnessState.FRESH,
        reason_codes=("calculation_failed",),
    )
    policy = WorkflowStepRecoveryPolicy(
        step_key="relax",
        action=WorkflowRecoveryAction.EXECUTION_RECOVERY,
        current_binding_id=binding.id,
        calculation_id=calculation.id,
        source_execution_plan_hash=source_plan.plan_hash,
        execution_recovery_action=decision.action,
        recovery_decision_hash=decision.decision_hash,
        reason_codes=("v04_recovery_preserves_current_calculation",),
    )
    gates, recovery = _projections(
        plan,
        selections={"relax": selection},
        step_gates={"relax": gate},
        policies={"relax": policy},
    )

    result = reconcile_workflow_orchestration(
        plan=plan,
        gates=gates,
        recovery=recovery,
        execution_sources=(
            WorkflowExecutionSource(
                step_key="relax",
                binding=binding,
                calculation=calculation,
                plan=source_plan,
                recovery_decision=decision,
            ),
        ),
    )

    handoff = result.step("relax")
    assert handoff.action is WorkflowOrchestrationAction.EXECUTION_RECOVERY_READY
    assert handoff.execution_recovery_action is RecoveryAction.NEW_EXECUTION_ATTEMPT
    assert result.scheduler_dag is not None
    assert result.scheduler_recovery_decisions[handoff.scheduler_node_id] == decision


def test_execution_tuning_recovery_requires_exact_derived_target_plan() -> None:
    plan = _plan()
    calculation = _calculation(plan, "relax", status=CalculationScientificStatus.FAILED)
    binding = _binding(plan, "relax", calculation)
    source_plan = _execution_plan(calculation)
    proposed = replace(source_plan.execution_settings, walltime_seconds=7200)
    decision = classify_recovery(
        plan=source_plan,
        request=RecoveryRequest(
            cause=RecoveryCause.EXECUTION_TUNING,
            evidence=ExecutionEvidence.VASP_LAUNCH_CONFIRMED,
            proposed_execution_settings=proposed,
        ),
    )
    target_plan = derive_execution_recovery_plan(
        plan=source_plan,
        execution_settings=proposed,
    )
    selection = WorkflowBindingSelection(
        step_key="relax",
        current_binding=binding,
        current_calculation=calculation,
    )
    gate = WorkflowStepGate(
        step_key="relax",
        scientific_state=WorkflowStepScientificState.BLOCKED,
        readiness=WorkflowStepReadiness.BLOCKED,
        current_binding_id=binding.id,
        calculation_id=calculation.id,
        freshness_state=FreshnessState.FRESH,
        reason_codes=("calculation_failed",),
    )
    policy = WorkflowStepRecoveryPolicy(
        step_key="relax",
        action=WorkflowRecoveryAction.EXECUTION_RECOVERY,
        current_binding_id=binding.id,
        calculation_id=calculation.id,
        source_execution_plan_hash=source_plan.plan_hash,
        execution_recovery_action=decision.action,
        recovery_decision_hash=decision.decision_hash,
        reason_codes=("v04_recovery_preserves_current_calculation",),
    )
    gates, recovery = _projections(
        plan,
        selections={"relax": selection},
        step_gates={"relax": gate},
        policies={"relax": policy},
    )

    with pytest.raises(WorkflowOrchestrationError, match="distinct dispatch"):
        reconcile_workflow_orchestration(
            plan=plan,
            gates=gates,
            recovery=recovery,
            execution_sources=(
                WorkflowExecutionSource(
                    step_key="relax",
                    binding=binding,
                    calculation=calculation,
                    plan=source_plan,
                    recovery_decision=decision,
                ),
            ),
        )

    result = reconcile_workflow_orchestration(
        plan=plan,
        gates=gates,
        recovery=recovery,
        execution_sources=(
            WorkflowExecutionSource(
                step_key="relax",
                binding=binding,
                calculation=calculation,
                plan=target_plan,
                recovery_decision=decision,
            ),
        ),
    )
    assert result.step("relax").execution_plan_hash == target_plan.plan_hash


def test_same_attempt_retry_is_not_misrouted_into_scheduler_new_attempt_dag() -> None:
    plan = _plan()
    calculation = _calculation(plan, "relax", status=CalculationScientificStatus.FAILED)
    binding = _binding(plan, "relax", calculation)
    execution_plan = _execution_plan(calculation)
    decision = classify_recovery(
        plan=execution_plan,
        request=RecoveryRequest(
            cause=RecoveryCause.TRANSPORT_FAILURE,
            evidence=ExecutionEvidence.NO_REMOTE_SIDE_EFFECT_CONFIRMED,
        ),
    )
    selection = WorkflowBindingSelection(
        step_key="relax",
        current_binding=binding,
        current_calculation=calculation,
    )
    gate = WorkflowStepGate(
        step_key="relax",
        scientific_state=WorkflowStepScientificState.BLOCKED,
        readiness=WorkflowStepReadiness.BLOCKED,
        current_binding_id=binding.id,
        calculation_id=calculation.id,
        freshness_state=FreshnessState.FRESH,
        reason_codes=("calculation_failed",),
    )
    policy = WorkflowStepRecoveryPolicy(
        step_key="relax",
        action=WorkflowRecoveryAction.EXECUTION_RECOVERY,
        current_binding_id=binding.id,
        calculation_id=calculation.id,
        source_execution_plan_hash=execution_plan.plan_hash,
        execution_recovery_action=decision.action,
        recovery_decision_hash=decision.decision_hash,
        reason_codes=("v04_recovery_preserves_current_calculation",),
    )
    gates, recovery = _projections(
        plan,
        selections={"relax": selection},
        step_gates={"relax": gate},
        policies={"relax": policy},
    )

    result = reconcile_workflow_orchestration(
        plan=plan,
        gates=gates,
        recovery=recovery,
        execution_sources=(
            WorkflowExecutionSource(
                step_key="relax",
                binding=binding,
                calculation=calculation,
                plan=execution_plan,
                recovery_decision=decision,
            ),
        ),
    )
    assert result.step("relax").action is WorkflowOrchestrationAction.RETRY_SAME_ATTEMPT
    assert result.scheduler_dag is None
    assert result.scheduler_recovery_decisions == {}


def test_execution_source_from_superseded_binding_fails_closed() -> None:
    plan = _plan()
    calculation = _calculation(plan, "relax", status=CalculationScientificStatus.READY)
    current = _binding(plan, "relax", calculation)
    old_calculation = _calculation(plan, "relax", status=CalculationScientificStatus.READY)
    old = _binding(plan, "relax", old_calculation)
    selection, gate, policy = _current_projection(plan, calculation, current)
    gates, recovery = _projections(
        plan,
        selections={"relax": selection},
        step_gates={"relax": gate},
        policies={"relax": policy},
    )

    with pytest.raises(WorkflowOrchestrationError, match="superseded generation"):
        reconcile_workflow_orchestration(
            plan=plan,
            gates=gates,
            recovery=recovery,
            execution_sources=(
                WorkflowExecutionSource(
                    step_key="relax",
                    binding=old,
                    calculation=old_calculation,
                    plan=_execution_plan(old_calculation),
                ),
            ),
        )

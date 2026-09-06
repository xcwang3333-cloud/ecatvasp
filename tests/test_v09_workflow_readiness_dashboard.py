from __future__ import annotations

from dataclasses import replace

import pytest

from ecatvasp.domain import (
    Calculation,
    CalculationScientificStatus,
    CalculationType,
    ExecutionAttempt,
    ExecutionAttemptStatus,
    KPointPolicy,
    KPointPolicyKind,
    Lattice,
    MethodDefinition,
    MethodFingerprint,
    PotcarIdentity,
    Project,
    ProtocolDefinition,
    RecipeIdentity,
    RemoteJob,
    SchedulerState,
    SchedulerType,
    ScientificWorkflowPlan,
    StructureSite,
    StructureSnapshot,
    WorkflowRecipeIdentity,
    WorkflowStepBinding,
    new_atom_uid,
)
from ecatvasp.provenance import FreshnessState
from ecatvasp.schema.version import SCHEMA_VERSION
from ecatvasp.storage import ProjectBundle
from ecatvasp.vasp.recipes import RECIPE_SLAB_RELAX
from ecatvasp.workflow import (
    WORKFLOW_RECIPE_SLAB_SCIENTIFIC_PREPARATION,
    WorkflowEdgeGate,
    WorkflowEdgeGateVerdict,
    WorkflowOrchestrationAction,
    WorkflowOrchestrationEvaluation,
    WorkflowScientificGateEvaluation,
    WorkflowStepGate,
    WorkflowStepOrchestration,
    WorkflowStepReadiness,
    WorkflowStepScientificState,
    get_workflow_recipe_spec,
    resolve_workflow_binding_generations,
)
from ecatvasp.workspace import (
    WorkspaceReadinessError,
    build_workflow_readiness_dashboard,
)


def _snapshot() -> StructureSnapshot:
    return StructureSnapshot(
        label="workflow root",
        lattice=Lattice(
            vectors=((3.0, 0.0, 0.0), (0.0, 3.0, 0.0), (0.0, 0.0, 15.0))
        ),
        sites=(
            StructureSite(
                atom_uid=new_atom_uid(),
                element="C",
                fractional_coords=(0.0, 0.0, 0.5),
            ),
        ),
    )


def _fingerprint() -> MethodFingerprint:
    return MethodFingerprint(
        method=MethodDefinition(
            xc_functional="PBE",
            potcar_family="PBE_54",
            potcars=(
                PotcarIdentity(
                    element="C",
                    symbol="C",
                    sha256="a" * 64,
                ),
            ),
        ),
        protocol=ProtocolDefinition(
            encut_ev=500.0,
            kpoints=KPointPolicy(KPointPolicyKind.GAMMA_ONLY),
        ),
        recipe=RecipeIdentity(recipe_id=RECIPE_SLAB_RELAX),
    )


def _plan(project: Project, root: StructureSnapshot) -> ScientificWorkflowPlan:
    identity = WorkflowRecipeIdentity(WORKFLOW_RECIPE_SLAB_SCIENTIFIC_PREPARATION)
    spec = get_workflow_recipe_spec(identity)
    return ScientificWorkflowPlan(
        project_id=project.id,
        workflow_recipe=identity,
        root_structure_snapshot_id=root.id,
        steps=spec.steps,
        edges=spec.edges,
    )


def _case() -> tuple[
    ProjectBundle,
    WorkflowScientificGateEvaluation,
    WorkflowOrchestrationEvaluation,
    Calculation,
    Calculation,
    WorkflowStepBinding,
    WorkflowStepBinding,
    ExecutionAttempt,
    ExecutionAttempt,
    RemoteJob,
]:
    project = Project(name="Readiness dashboard", slug="readiness-dashboard")
    root = _snapshot()
    fingerprint = _fingerprint()
    plan = _plan(project, root)

    historical = Calculation(
        project_id=project.id,
        calculation_type=CalculationType.RELAX,
        input_structure_snapshot_id=root.id,
        recipe_id=RECIPE_SLAB_RELAX,
        method_fingerprint_id=fingerprint.id,
        status=CalculationScientificStatus.STALE,
        slug="relax-generation-1",
    )
    current = Calculation(
        project_id=project.id,
        calculation_type=CalculationType.RELAX,
        input_structure_snapshot_id=root.id,
        recipe_id=RECIPE_SLAB_RELAX,
        method_fingerprint_id=fingerprint.id,
        status=CalculationScientificStatus.RUNNING,
        slug="relax-generation-2",
    )
    binding_1 = WorkflowStepBinding(
        workflow_plan_id=plan.id,
        step_key="relax",
        generation=1,
        calculation_id=historical.id,
        resolved_input_structure_snapshot_id=root.id,
        materialization_reason="initial root structure",
    )
    binding_2 = WorkflowStepBinding(
        workflow_plan_id=plan.id,
        step_key="relax",
        generation=2,
        calculation_id=current.id,
        resolved_input_structure_snapshot_id=root.id,
        materialization_reason="new scientific generation",
        supersedes_binding_id=binding_1.id,
    )

    historical_attempt = ExecutionAttempt(
        calculation_id=historical.id,
        attempt_number=1,
        status=ExecutionAttemptStatus.PARSED,
    )
    current_attempt = ExecutionAttempt(
        calculation_id=current.id,
        attempt_number=1,
        status=ExecutionAttemptStatus.EXITED,
    )
    historical_job = RemoteJob(
        execution_attempt_id=historical_attempt.id,
        scheduler=SchedulerType.SLURM,
        scheduler_job_id="1001",
        remote_directory="/work/historical",
        state=SchedulerState.COMPLETED,
    )
    current_job = RemoteJob(
        execution_attempt_id=current_attempt.id,
        scheduler=SchedulerType.SLURM,
        scheduler_job_id="1002",
        remote_directory="/work/current",
        state=SchedulerState.COMPLETED,
    )

    bundle = ProjectBundle(
        project=project,
        structure_snapshots=(root,),
        method_fingerprints=(fingerprint,),
        workflow_plans=(plan,),
        calculations=(historical, current),
        workflow_step_bindings=(binding_1, binding_2),
        execution_attempts=(historical_attempt, current_attempt),
        remote_jobs=(historical_job, current_job),
    )
    bundle.validate()

    selections = resolve_workflow_binding_generations(
        plan=plan,
        bindings=bundle.workflow_step_bindings,
        calculations=bundle.calculations,
    )
    step_gates = tuple(
        WorkflowStepGate(
            step_key=selection.step_key,
            scientific_state=(
                WorkflowStepScientificState.IN_PROGRESS
                if selection.step_key == "relax"
                else WorkflowStepScientificState.UNMATERIALIZED
            ),
            readiness=(
                WorkflowStepReadiness.WAITING
                if selection.step_key == "relax"
                else WorkflowStepReadiness.BLOCKED
            ),
            current_binding_id=(
                None if selection.current_binding is None else selection.current_binding.id
            ),
            calculation_id=(
                None
                if selection.current_calculation is None
                else selection.current_calculation.id
            ),
            freshness_state=(
                FreshnessState.FRESH if selection.step_key == "relax" else None
            ),
            reason_codes=(
                ("calculation_in_progress",)
                if selection.step_key == "relax"
                else ("upstream_step_not_satisfied",)
            ),
        )
        for selection in selections
    )
    edge_gates = tuple(
        WorkflowEdgeGate(
            upstream_step_key=edge.upstream_step_key,
            downstream_step_key=edge.downstream_step_key,
            role=edge.role,
            verdict=WorkflowEdgeGateVerdict.WAITING,
            reason_codes=("upstream_step_not_satisfied",),
        )
        for edge in plan.edges
    )
    gates = WorkflowScientificGateEvaluation(
        workflow_plan_id=plan.id,
        binding_selections=selections,
        step_gates=step_gates,
        edge_gates=edge_gates,
        superseded_calculation_ids=(historical.id,),
    )

    handoffs = tuple(
        WorkflowStepOrchestration(
            step_key=selection.step_key,
            action=(
                WorkflowOrchestrationAction.EXECUTION_IN_FLIGHT
                if selection.step_key == "relax"
                else WorkflowOrchestrationAction.WAIT
            ),
            current_binding_id=(
                None if selection.current_binding is None else selection.current_binding.id
            ),
            calculation_id=(
                None
                if selection.current_calculation is None
                else selection.current_calculation.id
            ),
            reason_codes=(
                ("execution_in_flight",)
                if selection.step_key == "relax"
                else ("workflow_gate_waiting",)
            ),
        )
        for selection in selections
    )
    orchestration = WorkflowOrchestrationEvaluation(
        workflow_plan_id=plan.id,
        step_handoffs=handoffs,
    )
    return (
        bundle,
        gates,
        orchestration,
        historical,
        current,
        binding_1,
        binding_2,
        historical_attempt,
        current_attempt,
        current_job,
    )


def test_dashboard_preserves_science_execution_scheduler_and_generation_boundaries() -> None:
    (
        bundle,
        gates,
        orchestration,
        historical,
        current,
        binding_1,
        binding_2,
        historical_attempt,
        current_attempt,
        current_job,
    ) = _case()

    dashboard = build_workflow_readiness_dashboard(
        bundle,
        workflow_plan_id=gates.workflow_plan_id,
        gates=gates,
        orchestration=orchestration,
    )
    relax = dashboard.step("relax")

    assert relax.current_binding_id == binding_2.id
    assert relax.current_generation == 2
    assert relax.calculation_id == current.id
    assert relax.calculation_status == "running"
    assert relax.scientific_state is WorkflowStepScientificState.IN_PROGRESS
    assert relax.readiness is WorkflowStepReadiness.WAITING
    assert relax.gate_reason_codes == ("calculation_in_progress",)
    assert relax.orchestration_action is WorkflowOrchestrationAction.EXECUTION_IN_FLIGHT
    assert relax.orchestration_reason_codes == ("execution_in_flight",)
    assert relax.superseded_binding_ids == (binding_1.id,)
    assert relax.superseded_calculation_ids == (historical.id,)

    assert tuple(item.execution_attempt_id for item in relax.execution_attempts) == (
        current_attempt.id,
    )
    assert historical_attempt.id not in {
        item.execution_attempt_id for item in relax.execution_attempts
    }
    assert relax.execution_attempts[0].status == "exited"
    assert relax.execution_attempts[0].remote_jobs[0].remote_job_id == current_job.id
    assert relax.execution_attempts[0].remote_jobs[0].state == "completed"
    assert relax.scientific_state is not WorkflowStepScientificState.PASSED

    assert len(dashboard.edges) == len(bundle.workflow_plans[0].edges)
    assert all(item.verdict is WorkflowEdgeGateVerdict.WAITING for item in dashboard.edges)
    assert all(
        item.reason_codes == ("upstream_step_not_satisfied",)
        for item in dashboard.edges
    )


def test_dashboard_exposes_unmaterialized_steps_without_inventing_execution_state() -> None:
    bundle, gates, _, *_ = _case()

    dashboard = build_workflow_readiness_dashboard(
        bundle,
        workflow_plan_id=gates.workflow_plan_id,
        gates=gates,
    )
    static = dashboard.step("static")

    assert static.current_binding_id is None
    assert static.current_generation is None
    assert static.calculation_id is None
    assert static.calculation_status is None
    assert static.execution_attempts == ()
    assert static.scientific_state is WorkflowStepScientificState.UNMATERIALIZED
    assert static.readiness is WorkflowStepReadiness.BLOCKED
    assert static.gate_reason_codes == ("upstream_step_not_satisfied",)
    assert static.orchestration_action is None


def test_dashboard_rejects_historical_generation_fallback() -> None:
    bundle, gates, _, historical, _, binding_1, _, *_ = _case()
    selections = tuple(
        replace(
            item,
            current_binding=binding_1,
            current_calculation=historical,
            superseded_binding_ids=(),
            superseded_calculation_ids=(),
        )
        if item.step_key == "relax"
        else item
        for item in gates.binding_selections
    )
    step_gates = tuple(
        replace(
            item,
            current_binding_id=binding_1.id,
            calculation_id=historical.id,
        )
        if item.step_key == "relax"
        else item
        for item in gates.step_gates
    )
    stale_projection = replace(
        gates,
        binding_selections=selections,
        step_gates=step_gates,
        superseded_calculation_ids=(),
    )

    with pytest.raises(WorkspaceReadinessError, match="current persisted binding generations"):
        build_workflow_readiness_dashboard(
            bundle,
            workflow_plan_id=gates.workflow_plan_id,
            gates=stale_projection,
        )


def test_dashboard_rejects_orchestration_projection_for_another_current_generation() -> None:
    bundle, gates, orchestration, historical, _, binding_1, *_ = _case()
    handoffs = tuple(
        replace(
            item,
            current_binding_id=binding_1.id,
            calculation_id=historical.id,
        )
        if item.step_key == "relax"
        else item
        for item in orchestration.step_handoffs
    )
    mismatched = WorkflowOrchestrationEvaluation(
        workflow_plan_id=orchestration.workflow_plan_id,
        step_handoffs=handoffs,
    )

    with pytest.raises(WorkspaceReadinessError, match="current binding"):
        build_workflow_readiness_dashboard(
            bundle,
            workflow_plan_id=gates.workflow_plan_id,
            gates=gates,
            orchestration=mismatched,
        )


def test_readiness_dashboard_does_not_advance_project_schema() -> None:
    bundle, gates, _, *_ = _case()

    dashboard = build_workflow_readiness_dashboard(
        bundle,
        workflow_plan_id=gates.workflow_plan_id,
        gates=gates,
    )

    assert SCHEMA_VERSION == 3
    assert bundle.project.schema_version == SCHEMA_VERSION
    assert dashboard.workflow_plan_id == gates.workflow_plan_id

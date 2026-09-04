from __future__ import annotations

from dataclasses import replace

import pytest

from ecatvasp.domain import (
    Calculation,
    CalculationScientificStatus,
    ExecutionSettings,
    KPointPolicy,
    KPointPolicyKind,
    Lattice,
    MethodDefinition,
    MethodFingerprint,
    PotcarIdentity,
    ProtocolDefinition,
    RecipeIdentity,
    ScientificWorkflowPlan,
    StructureOrigin,
    StructureSite,
    StructureSnapshot,
    StructureVariant,
    VariantType,
    WorkflowRecipeIdentity,
    WorkflowStepBinding,
)
from ecatvasp.domain.ids import new_artifact_id, new_atom_uid, new_catalyst_id, new_project_id
from ecatvasp.execution import (
    ExecutionEvidence,
    RecoveryAction,
    RecoveryCause,
    RecoveryRequest,
    classify_recovery,
)
from ecatvasp.provenance import DependencyKind, DependencyRecord, scientific_hash
from ecatvasp.vasp.contracts import (
    LatticeAxis,
    ProjectNumericalLock,
    VaspSystemContext,
    VaspSystemKind,
)
from ecatvasp.vasp.execution_plan import (
    ExecutionPlan,
    PotcarResolutionEntry,
    PotcarResolutionRequest,
    VaspRuntimeConstraints,
)
from ecatvasp.vasp.recipes import RECIPE_GROUND_STATE_STATIC, RECIPE_SLAB_RELAX
from ecatvasp.vasp.results import ConvergenceVerdict, VaspConvergenceAssessment
from ecatvasp.vasp.structure_promotion import VaspStructurePromotionResult
from ecatvasp.workflow import (
    WORKFLOW_RECIPE_SLAB_SCIENTIFIC_PREPARATION,
    AcceptedStructureSource,
    WorkflowRecoveryAction,
    WorkflowRecoveryPolicyError,
    WorkflowRecoverySource,
    evaluate_workflow_freshness,
    evaluate_workflow_recovery_policy,
    evaluate_workflow_scientific_gates,
    materialize_workflow_step,
    plan_scientific_workflow,
)


def _snapshot(
    *,
    parent: StructureSnapshot | None = None,
    z: float = 0.3,
) -> StructureSnapshot:
    atom_uid = new_atom_uid() if parent is None else parent.sites[0].atom_uid
    return StructureSnapshot(
        lattice=Lattice(
            vectors=(
                (3.0, 0.0, 0.0),
                (0.0, 3.0, 0.0),
                (0.0, 0.0, 15.0),
            )
        ),
        sites=(
            StructureSite(
                atom_uid=atom_uid,
                element="C",
                fractional_coords=(0.1, 0.2, z),
            ),
        ),
        origin=StructureOrigin.IMPORTED if parent is None else StructureOrigin.RELAXED,
        parent_snapshot_id=None if parent is None else parent.id,
    )


def _fingerprint(recipe_id: str) -> MethodFingerprint:
    return MethodFingerprint(
        method=MethodDefinition(
            xc_functional="PBE",
            potcar_family="PBE_54",
            potcars=(PotcarIdentity(element="C", symbol="C", sha256="a" * 64),),
        ),
        protocol=ProtocolDefinition(
            encut_ev=500.0,
            kpoints=KPointPolicy(KPointPolicyKind.GAMMA_ONLY),
        ),
        recipe=RecipeIdentity(recipe_id=recipe_id),
    )


def _context() -> VaspSystemContext:
    return VaspSystemContext(
        kind=VaspSystemKind.SLAB_2D,
        vacuum_axis=LatticeAxis.C,
    )


def _lock(
    plan: ScientificWorkflowPlan,
    fingerprint: MethodFingerprint,
) -> ProjectNumericalLock:
    return ProjectNumericalLock(
        project_id=plan.project_id,
        system_kind=VaspSystemKind.SLAB_2D,
        core_method_hash=fingerprint.core_method_hash,
        encut_ev=fingerprint.protocol.encut_ev,
        encut_validation_hash="b" * 64,
        kpoints=fingerprint.protocol.kpoints,
        kpoints_validation_hash="c" * 64,
    )


def _workflow_plan(root: StructureSnapshot) -> ScientificWorkflowPlan:
    return plan_scientific_workflow(
        project_id=new_project_id(),
        workflow_recipe=WorkflowRecipeIdentity(
            WORKFLOW_RECIPE_SLAB_SCIENTIFIC_PREPARATION
        ),
        root_structure_snapshot_id=root.id,
    ).plan


def _relax_generation(
    *,
    plan: ScientificWorkflowPlan,
    root: StructureSnapshot,
    status: CalculationScientificStatus,
    previous_binding: WorkflowStepBinding | None = None,
    z: float = 0.31,
) -> tuple[Calculation, WorkflowStepBinding, AcceptedStructureSource | None]:
    fingerprint = _fingerprint(RECIPE_SLAB_RELAX)
    materialized = materialize_workflow_step(
        plan=plan,
        step_key="relax",
        fingerprint=fingerprint,
        system_context=_context(),
        project_lock=_lock(plan, fingerprint),
        root_snapshot=root,
        previous_binding=previous_binding,
    )
    calculation = replace(materialized.calculation, status=status)
    if status is not CalculationScientificStatus.CONVERGED:
        return calculation, materialized.binding, None

    relaxed = _snapshot(parent=root, z=z)
    variant = StructureVariant(
        catalyst_id=new_catalyst_id(),
        name="accepted relaxed structure",
        variant_type=VariantType.GEOMETRY,
        current_structure_snapshot_id=relaxed.id,
    )
    assessment = VaspConvergenceAssessment(
        calculation_type=calculation.calculation_type,
        electronic=ConvergenceVerdict.CONVERGED,
        ionic=ConvergenceVerdict.CONVERGED,
        overall=ConvergenceVerdict.CONVERGED,
    )
    source = AcceptedStructureSource(
        upstream_binding=materialized.binding,
        upstream_calculation=calculation,
        promotion=VaspStructurePromotionResult(
            updated_variant=variant,
            snapshot=relaxed,
            convergence=assessment,
        ),
    )
    return calculation, materialized.binding, source


def _static_generation(
    *,
    plan: ScientificWorkflowPlan,
    source: AcceptedStructureSource,
) -> tuple[Calculation, WorkflowStepBinding]:
    fingerprint = _fingerprint(RECIPE_GROUND_STATE_STATIC)
    materialized = materialize_workflow_step(
        plan=plan,
        step_key="static",
        fingerprint=fingerprint,
        system_context=_context(),
        project_lock=_lock(plan, fingerprint),
        accepted_structure_source=source,
    )
    return (
        replace(materialized.calculation, status=CalculationScientificStatus.CONVERGED),
        materialized.binding,
    )


def _execution_plan(calculation: Calculation) -> ExecutionPlan:
    return ExecutionPlan(
        calculation_id=calculation.id,
        recipe_id=calculation.recipe_id,
        system_context=_context(),
        input_manifest_artifact_id=new_artifact_id(),
        input_manifest_sha256="d" * 64,
        preparation_hash="e" * 64,
        staging_inputs=(),
        potcar_resolution=PotcarResolutionRequest(
            family="PBE_54",
            core_method_hash="f" * 64,
            metadata_hash="1" * 64,
            entries=(PotcarResolutionEntry("C", "C", "2" * 64),),
        ),
        expected_outputs=(),
        runtime_constraints=VaspRuntimeConstraints(),
        execution_settings=ExecutionSettings(
            ncore=4,
            kpar=1,
            nodes=1,
            cores=8,
            memory_mb=16000,
            walltime_seconds=3600,
            partition="compute",
            mpi_ranks=8,
            omp_threads=1,
            executable="vasp_std",
        ),
    )


def _gates(
    *,
    plan: ScientificWorkflowPlan,
    bindings: tuple[WorkflowStepBinding, ...],
    calculations: tuple[Calculation, ...],
    sources: tuple[AcceptedStructureSource, ...] = (),
    dependencies: tuple[DependencyRecord, ...] = (),
    current_hashes: dict[object, str] | None = None,
):
    freshness = evaluate_workflow_freshness(
        plan=plan,
        bindings=bindings,
        calculations=calculations,
        dependencies=dependencies,
        current_hashes={} if current_hashes is None else current_hashes,
        accepted_structure_sources=sources,
    )
    return evaluate_workflow_scientific_gates(
        plan=plan,
        bindings=bindings,
        calculations=calculations,
        freshness=freshness,
        accepted_structure_sources=sources,
    )


def _recovery_source(
    *,
    binding: WorkflowStepBinding,
    calculation: Calculation,
    request: RecoveryRequest,
) -> WorkflowRecoverySource:
    execution_plan = _execution_plan(calculation)
    return WorkflowRecoverySource(
        step_key=binding.step_key,
        binding=binding,
        calculation=calculation,
        execution_plan=execution_plan,
        decision=classify_recovery(plan=execution_plan, request=request),
    )


def test_blocked_step_without_explicit_recovery_decision_waits_for_policy() -> None:
    root = _snapshot()
    plan = _workflow_plan(root)
    relax, binding, _ = _relax_generation(
        plan=plan,
        root=root,
        status=CalculationScientificStatus.FAILED,
    )
    gates = _gates(plan=plan, bindings=(binding,), calculations=(relax,))

    policy = evaluate_workflow_recovery_policy(
        plan=plan,
        gates=gates,
    ).step("relax")

    assert policy.action is WorkflowRecoveryAction.RECOVERY_DECISION_REQUIRED
    assert policy.current_binding_id == binding.id
    assert policy.calculation_id == relax.id


def test_execution_recovery_preserves_current_workflow_generation() -> None:
    root = _snapshot()
    plan = _workflow_plan(root)
    relax, binding, _ = _relax_generation(
        plan=plan,
        root=root,
        status=CalculationScientificStatus.FAILED,
    )
    gates = _gates(plan=plan, bindings=(binding,), calculations=(relax,))
    source = _recovery_source(
        binding=binding,
        calculation=relax,
        request=RecoveryRequest(
            cause=RecoveryCause.VASP_FAILURE,
            evidence=ExecutionEvidence.VASP_LAUNCH_CONFIRMED,
        ),
    )

    policy = evaluate_workflow_recovery_policy(
        plan=plan,
        gates=gates,
        recovery_sources=(source,),
    ).step("relax")

    assert policy.action is WorkflowRecoveryAction.EXECUTION_RECOVERY
    assert policy.preserves_current_calculation
    assert policy.execution_recovery_action is RecoveryAction.NEW_EXECUTION_ATTEMPT
    assert policy.previous_binding_id is None
    assert policy.target_input_structure_snapshot_id is None
    assert policy.recovery_decision_hash == source.decision.decision_hash


def test_scientific_input_change_authorizes_new_binding_generation() -> None:
    root = _snapshot()
    plan = _workflow_plan(root)
    relax, binding, _ = _relax_generation(
        plan=plan,
        root=root,
        status=CalculationScientificStatus.COMPLETED_UNCONVERGED,
    )
    gates = _gates(plan=plan, bindings=(binding,), calculations=(relax,))
    source = _recovery_source(
        binding=binding,
        calculation=relax,
        request=RecoveryRequest(
            cause=RecoveryCause.SCIENTIFIC_INPUT_CHANGE,
            evidence=ExecutionEvidence.VASP_LAUNCH_CONFIRMED,
            proposed_incar_tags=("ALGO",),
        ),
    )

    policy = evaluate_workflow_recovery_policy(
        plan=plan,
        gates=gates,
        recovery_sources=(source,),
    ).step("relax")

    assert source.decision.action is RecoveryAction.NEW_CALCULATION
    assert policy.action is WorkflowRecoveryAction.REMATERIALIZE_STEP
    assert policy.requires_new_binding_generation
    assert policy.previous_binding_id == binding.id
    assert policy.target_input_structure_snapshot_id == root.id
    assert policy.materialization_reason == "workflow recovery: scientific_input"


def test_contcar_continuation_requires_new_workflow_plan() -> None:
    root = _snapshot()
    plan = _workflow_plan(root)
    relax, binding, _ = _relax_generation(
        plan=plan,
        root=root,
        status=CalculationScientificStatus.COMPLETED_UNCONVERGED,
    )
    gates = _gates(plan=plan, bindings=(binding,), calculations=(relax,))
    source = _recovery_source(
        binding=binding,
        calculation=relax,
        request=RecoveryRequest(
            cause=RecoveryCause.CONTCAR_CONTINUATION,
            evidence=ExecutionEvidence.VASP_LAUNCH_CONFIRMED,
            continue_from_contcar=True,
        ),
    )

    policy = evaluate_workflow_recovery_policy(
        plan=plan,
        gates=gates,
        recovery_sources=(source,),
    ).step("relax")

    assert source.decision.action is RecoveryAction.NEW_STRUCTURE_AND_CALCULATION
    assert policy.action is WorkflowRecoveryAction.NEW_WORKFLOW_PLAN_REQUIRED
    assert policy.requires_new_workflow_plan
    assert not policy.requires_new_binding_generation
    assert policy.target_input_structure_snapshot_id is None


def test_automatic_correction_remains_manual_review() -> None:
    root = _snapshot()
    plan = _workflow_plan(root)
    relax, binding, _ = _relax_generation(
        plan=plan,
        root=root,
        status=CalculationScientificStatus.FAILED,
    )
    gates = _gates(plan=plan, bindings=(binding,), calculations=(relax,))
    source = _recovery_source(
        binding=binding,
        calculation=relax,
        request=RecoveryRequest(
            cause=RecoveryCause.VASP_FAILURE,
            evidence=ExecutionEvidence.VASP_LAUNCH_CONFIRMED,
            proposed_incar_tags=("ALGO",),
            automatic=True,
        ),
    )

    policy = evaluate_workflow_recovery_policy(
        plan=plan,
        gates=gates,
        recovery_sources=(source,),
    ).step("relax")

    assert source.decision.action is RecoveryAction.MANUAL_REVIEW_REQUIRED
    assert policy.action is WorkflowRecoveryAction.MANUAL_REVIEW_REQUIRED


def test_running_and_satisfied_steps_do_not_accept_recovery_decisions() -> None:
    root = _snapshot()
    plan = _workflow_plan(root)
    running, running_binding, _ = _relax_generation(
        plan=plan,
        root=root,
        status=CalculationScientificStatus.RUNNING,
    )
    running_gates = _gates(
        plan=plan,
        bindings=(running_binding,),
        calculations=(running,),
    )
    running_policy = evaluate_workflow_recovery_policy(
        plan=plan,
        gates=running_gates,
    ).step("relax")
    assert running_policy.action is WorkflowRecoveryAction.WAIT_FOR_PREREQUISITE

    running_source = _recovery_source(
        binding=running_binding,
        calculation=running,
        request=RecoveryRequest(
            cause=RecoveryCause.VASP_FAILURE,
            evidence=ExecutionEvidence.VASP_LAUNCH_CONFIRMED,
        ),
    )
    with pytest.raises(WorkflowRecoveryPolicyError, match="in-progress"):
        evaluate_workflow_recovery_policy(
            plan=plan,
            gates=running_gates,
            recovery_sources=(running_source,),
        )

    converged, converged_binding, source = _relax_generation(
        plan=plan,
        root=root,
        status=CalculationScientificStatus.CONVERGED,
    )
    assert source is not None
    converged_gates = _gates(
        plan=plan,
        bindings=(converged_binding,),
        calculations=(converged,),
        sources=(source,),
    )
    assert (
        evaluate_workflow_recovery_policy(plan=plan, gates=converged_gates)
        .step("relax")
        .action
        is WorkflowRecoveryAction.NONE
    )


def test_upstream_generation_change_rematerializes_old_downstream_lineage() -> None:
    root = _snapshot()
    plan = _workflow_plan(root)
    relax1, binding1, source1 = _relax_generation(
        plan=plan,
        root=root,
        status=CalculationScientificStatus.CONVERGED,
        z=0.31,
    )
    assert source1 is not None
    static, static_binding = _static_generation(plan=plan, source=source1)
    relax2, binding2, source2 = _relax_generation(
        plan=plan,
        root=root,
        status=CalculationScientificStatus.CONVERGED,
        previous_binding=binding1,
        z=0.33,
    )
    assert source2 is not None

    gates = _gates(
        plan=plan,
        bindings=(binding1, binding2, static_binding),
        calculations=(relax1, relax2, static),
        sources=(source1, source2),
    )
    policy = evaluate_workflow_recovery_policy(plan=plan, gates=gates).step("static")

    assert policy.action is WorkflowRecoveryAction.REMATERIALIZE_STEP
    assert policy.previous_binding_id == static_binding.id
    assert policy.target_input_structure_snapshot_id == source2.promotion.snapshot.id
    assert policy.materialization_reason is not None
    assert "current accepted upstream structure" in policy.materialization_reason


def test_generic_scientific_staleness_requires_manual_review() -> None:
    root = _snapshot()
    plan = _workflow_plan(root)
    relax, binding, _ = _relax_generation(
        plan=plan,
        root=root,
        status=CalculationScientificStatus.CONVERGED,
    )
    dependency = DependencyRecord(
        upstream_id=root.id,
        downstream_id=relax.id,
        kind=DependencyKind.SCIENTIFIC,
        role="input_structure",
        recorded_hash=scientific_hash(root),
    )
    gates = _gates(
        plan=plan,
        bindings=(binding,),
        calculations=(relax,),
        dependencies=(dependency,),
        current_hashes={root.id: "9" * 64},
    )

    policy = evaluate_workflow_recovery_policy(plan=plan, gates=gates).step("relax")

    assert policy.action is WorkflowRecoveryAction.MANUAL_REVIEW_REQUIRED
    assert "generic_scientific_staleness_requires_explicit_review" in policy.reason_codes


def test_recovery_source_must_bind_current_workflow_generation_and_plan() -> None:
    root = _snapshot()
    plan = _workflow_plan(root)
    relax1, binding1, _ = _relax_generation(
        plan=plan,
        root=root,
        status=CalculationScientificStatus.FAILED,
    )
    relax2, binding2, _ = _relax_generation(
        plan=plan,
        root=root,
        status=CalculationScientificStatus.FAILED,
        previous_binding=binding1,
    )
    gates = _gates(
        plan=plan,
        bindings=(binding1, binding2),
        calculations=(relax1, relax2),
    )
    old_source = _recovery_source(
        binding=binding1,
        calculation=relax1,
        request=RecoveryRequest(
            cause=RecoveryCause.VASP_FAILURE,
            evidence=ExecutionEvidence.VASP_LAUNCH_CONFIRMED,
        ),
    )

    with pytest.raises(WorkflowRecoveryPolicyError, match="superseded workflow generation"):
        evaluate_workflow_recovery_policy(
            plan=plan,
            gates=gates,
            recovery_sources=(old_source,),
        )

    execution_plan = _execution_plan(relax2)
    decision = classify_recovery(
        plan=execution_plan,
        request=RecoveryRequest(
            cause=RecoveryCause.VASP_FAILURE,
            evidence=ExecutionEvidence.VASP_LAUNCH_CONFIRMED,
        ),
    )
    with pytest.raises(WorkflowRecoveryPolicyError, match="source ExecutionPlan"):
        WorkflowRecoverySource(
            step_key="relax",
            binding=binding2,
            calculation=relax2,
            execution_plan=execution_plan,
            decision=replace(decision, source_plan_hash="8" * 64),
        )

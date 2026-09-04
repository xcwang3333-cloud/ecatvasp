from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from ecatvasp.domain import (
    CalculationScientificStatus,
    ExecutionAttemptStatus,
    ExecutionSettings,
    KPointPolicy,
    KPointPolicyKind,
    Lattice,
    MethodDefinition,
    MethodFingerprint,
    PotcarIdentity,
    Project,
    ProtocolDefinition,
    RecipeIdentity,
    StructureOrigin,
    StructureSite,
    StructureSnapshot,
    WorkflowRecipeIdentity,
    new_artifact_id,
    new_atom_uid,
)
from ecatvasp.execution import (
    BatchConcurrencyPolicy,
    BatchDispatchMode,
    ExecutionEvidence,
    RecoveryCause,
    RecoveryRequest,
    classify_recovery,
    create_execution_attempt,
)
from ecatvasp.execution.batch import SchedulerDag, SchedulerDagNode
from ecatvasp.storage import ProjectBundle, ProjectStore
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
from ecatvasp.vasp.recipes import RECIPE_SLAB_RELAX
from ecatvasp.workflow import (
    WORKFLOW_RECIPE_SLAB_SCIENTIFIC_PREPARATION,
    WorkflowOrchestrationAction,
    WorkflowOrchestrationEvaluation,
    WorkflowRecoveryAttemptSource,
    WorkflowSchedulerRecoveryHandoff,
    WorkflowStepOrchestration,
    persist_or_reuse_workflow_materialization,
    persist_or_reuse_workflow_plan,
    persist_workflow_dispatch_wave,
    plan_scientific_workflow,
    reopen_workflow_resume_state,
)


def _snapshot() -> StructureSnapshot:
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
                atom_uid=new_atom_uid(),
                element="C",
                fractional_coords=(0.1, 0.2, 0.3),
            ),
        ),
        origin=StructureOrigin.IMPORTED,
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
        recipe=RecipeIdentity(recipe_id=RECIPE_SLAB_RELAX, version="1"),
    )


def _context() -> VaspSystemContext:
    return VaspSystemContext(
        kind=VaspSystemKind.SLAB_2D,
        vacuum_axis=LatticeAxis.C,
    )


def _lock(project: Project, fingerprint: MethodFingerprint) -> ProjectNumericalLock:
    return ProjectNumericalLock(
        project_id=project.id,
        system_kind=VaspSystemKind.SLAB_2D,
        core_method_hash=fingerprint.core_method_hash,
        encut_ev=fingerprint.protocol.encut_ev,
        encut_validation_hash="b" * 64,
        kpoints=fingerprint.protocol.kpoints,
        kpoints_validation_hash="c" * 64,
    )


def _planning(project: Project, root: StructureSnapshot):
    return plan_scientific_workflow(
        project_id=project.id,
        workflow_recipe=WorkflowRecipeIdentity(
            WORKFLOW_RECIPE_SLAB_SCIENTIFIC_PREPARATION
        ),
        root_structure_snapshot_id=root.id,
    )


def _materialization_orchestration(plan_id, root_id) -> WorkflowOrchestrationEvaluation:
    return WorkflowOrchestrationEvaluation(
        workflow_plan_id=plan_id,
        step_handoffs=(
            WorkflowStepOrchestration(
                step_key="relax",
                action=WorkflowOrchestrationAction.MATERIALIZE_STEP,
                target_input_structure_snapshot_id=root_id,
                reason_codes=("test_ready_materialization",),
            ),
        ),
    )


def _execution_plan(calculation) -> ExecutionPlan:
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
        execution_settings=ExecutionSettings(),
    )


def _execution_orchestration(
    *,
    plan_id,
    binding,
    calculation,
    execution_plan: ExecutionPlan,
) -> WorkflowOrchestrationEvaluation:
    node_id = f"workflow-relax-g{binding.generation}"
    node = SchedulerDagNode(
        node_id=node_id,
        calculation=calculation,
        plan=execution_plan,
    )
    return WorkflowOrchestrationEvaluation(
        workflow_plan_id=plan_id,
        step_handoffs=(
            WorkflowStepOrchestration(
                step_key="relax",
                action=WorkflowOrchestrationAction.EXECUTION_READY,
                current_binding_id=binding.id,
                calculation_id=calculation.id,
                execution_plan_hash=execution_plan.plan_hash,
                scheduler_node_id=node_id,
                reason_codes=("test_execution_ready",),
            ),
        ),
        scheduler_dag=SchedulerDag(nodes=(node,)),
    )


def _recovery_orchestration(
    *,
    plan_id,
    binding,
    calculation,
    execution_plan: ExecutionPlan,
    decision,
) -> WorkflowOrchestrationEvaluation:
    node_id = f"workflow-relax-g{binding.generation}"
    node = SchedulerDagNode(
        node_id=node_id,
        calculation=calculation,
        plan=execution_plan,
    )
    return WorkflowOrchestrationEvaluation(
        workflow_plan_id=plan_id,
        step_handoffs=(
            WorkflowStepOrchestration(
                step_key="relax",
                action=WorkflowOrchestrationAction.EXECUTION_RECOVERY_READY,
                current_binding_id=binding.id,
                calculation_id=calculation.id,
                execution_plan_hash=execution_plan.plan_hash,
                scheduler_node_id=node_id,
                execution_recovery_action=decision.action,
                recovery_decision_hash=decision.decision_hash,
                reason_codes=("test_execution_recovery_ready",),
            ),
        ),
        scheduler_dag=SchedulerDag(nodes=(node,)),
        scheduler_recoveries=(
            WorkflowSchedulerRecoveryHandoff(node_id=node_id, decision=decision),
        ),
    )


def _persist_plan_and_root(tmp_path: Path):
    project = Project(name="Workflow durability", slug="workflow-durability")
    root = _snapshot()
    store = ProjectStore(tmp_path)
    store.save(ProjectBundle(project=project, structure_snapshots=(root,)))
    planning = _planning(project, root)
    persisted = persist_or_reuse_workflow_plan(store=store, planning=planning)
    return project, root, store, planning, persisted.plan


def _persist_root_materialization(tmp_path: Path):
    project, root, store, _, plan = _persist_plan_and_root(tmp_path)
    fingerprint = _fingerprint()
    bundle = store.open()
    store.save(replace(bundle, method_fingerprints=(fingerprint,)))
    result = persist_or_reuse_workflow_materialization(
        store=store,
        plan=plan,
        orchestration=_materialization_orchestration(plan.id, root.id),
        step_key="relax",
        fingerprint=fingerprint,
        system_context=_context(),
        project_lock=_lock(project, fingerprint),
        root_snapshot=root,
    )
    return project, root, store, plan, fingerprint, result.materialization


def test_persisted_plan_is_reused_by_plan_hash_after_reopen(tmp_path: Path) -> None:
    project, root, store, planning, persisted_plan = _persist_plan_and_root(tmp_path)
    repeated = _planning(project, root)

    assert repeated.plan.id != planning.plan.id
    assert repeated.plan.plan_hash == planning.plan.plan_hash
    assert repeated.planning_hash == planning.planning_hash

    reused = persist_or_reuse_workflow_plan(store=store, planning=repeated)
    reopened = store.open()

    assert reused.reused
    assert reused.plan.id == persisted_plan.id
    assert len(reopened.workflow_plans) == 1
    assert reused.resume_state.plan.id == persisted_plan.id


def test_materialization_replay_reuses_generation_without_duplicate_calculation(
    tmp_path: Path,
) -> None:
    project, root, store, _, plan = _persist_plan_and_root(tmp_path)
    fingerprint = _fingerprint()
    bundle = store.open()
    store.save(replace(bundle, method_fingerprints=(fingerprint,)))
    orchestration = _materialization_orchestration(plan.id, root.id)

    first = persist_or_reuse_workflow_materialization(
        store=store,
        plan=plan,
        orchestration=orchestration,
        step_key="relax",
        fingerprint=fingerprint,
        system_context=_context(),
        project_lock=_lock(project, fingerprint),
        root_snapshot=root,
    )
    second = persist_or_reuse_workflow_materialization(
        store=store,
        plan=plan,
        orchestration=orchestration,
        step_key="relax",
        fingerprint=fingerprint,
        system_context=_context(),
        project_lock=_lock(project, fingerprint),
        root_snapshot=root,
    )
    reopened = store.open()

    assert not first.reused
    assert second.reused
    assert second.materialization.binding.id == first.materialization.binding.id
    assert second.materialization.calculation.id == first.materialization.calculation.id
    assert len(reopened.workflow_step_bindings) == 1
    assert len(reopened.calculations) == 1
    assert second.resume_state.resume_hash == reopen_workflow_resume_state(
        store=store,
        workflow_plan_id=plan.id,
    ).resume_hash


def test_materialization_replay_fails_closed_on_scientific_identity_conflict(
    tmp_path: Path,
) -> None:
    project, root, store, _, plan = _persist_plan_and_root(tmp_path)
    first_fingerprint = _fingerprint()
    conflicting_fingerprint = _fingerprint()
    bundle = store.open()
    store.save(
        replace(
            bundle,
            method_fingerprints=(first_fingerprint, conflicting_fingerprint),
        )
    )
    orchestration = _materialization_orchestration(plan.id, root.id)
    persist_or_reuse_workflow_materialization(
        store=store,
        plan=plan,
        orchestration=orchestration,
        step_key="relax",
        fingerprint=first_fingerprint,
        system_context=_context(),
        project_lock=_lock(project, first_fingerprint),
        root_snapshot=root,
    )

    with pytest.raises(
        ValueError,
        match="conflicts with replayed workflow materialization identity",
    ):
        persist_or_reuse_workflow_materialization(
            store=store,
            plan=plan,
            orchestration=orchestration,
            step_key="relax",
            fingerprint=conflicting_fingerprint,
            system_context=_context(),
            project_lock=_lock(project, conflicting_fingerprint),
            root_snapshot=root,
        )


def test_dispatch_persists_created_attempt_before_exposing_wave_and_reuses_it(
    tmp_path: Path,
) -> None:
    _, _, store, plan, _, materialization = _persist_root_materialization(tmp_path)
    calculation = materialization.calculation
    binding = materialization.binding
    execution_plan = _execution_plan(calculation)
    orchestration = _execution_orchestration(
        plan_id=plan.id,
        binding=binding,
        calculation=calculation,
        execution_plan=execution_plan,
    )

    first = persist_workflow_dispatch_wave(
        store=store,
        plan=plan,
        orchestration=orchestration,
        concurrency=BatchConcurrencyPolicy(max_active=1),
    )
    second = persist_workflow_dispatch_wave(
        store=store,
        plan=plan,
        orchestration=orchestration,
        concurrency=BatchConcurrencyPolicy(max_active=1),
    )

    assert len(first.newly_persisted_attempt_ids) == 1
    assert len(first.wave.tickets) == 1
    assert first.wave.tickets[0].mode is BatchDispatchMode.CONTINUE_CREATED_ATTEMPT
    assert first.wave.tickets[0].attempt.id == first.newly_persisted_attempt_ids[0]
    assert second.newly_persisted_attempt_ids == ()
    assert len(second.wave.tickets) == 1
    assert second.wave.tickets[0].attempt.id == first.wave.tickets[0].attempt.id
    assert len(store.open().execution_attempts) == 1


def test_old_recovery_source_cannot_allocate_second_successor_after_reopen(
    tmp_path: Path,
) -> None:
    _, _, store, plan, _, materialization = _persist_root_materialization(tmp_path)
    calculation = replace(
        materialization.calculation,
        status=CalculationScientificStatus.FAILED,
    )
    execution_plan = _execution_plan(calculation)
    source_attempt = replace(
        create_execution_attempt(plan=execution_plan, calculation=calculation),
        status=ExecutionAttemptStatus.FAILED,
    )
    bundle = store.open()
    calculations = tuple(
        calculation if item.id == calculation.id else item for item in bundle.calculations
    )
    store.save(
        replace(
            bundle,
            calculations=calculations,
            execution_attempts=(source_attempt,),
        )
    )
    decision = classify_recovery(
        plan=execution_plan,
        request=RecoveryRequest(
            cause=RecoveryCause.VASP_FAILURE,
            evidence=ExecutionEvidence.VASP_LAUNCH_CONFIRMED,
        ),
    )
    orchestration = _recovery_orchestration(
        plan_id=plan.id,
        binding=materialization.binding,
        calculation=calculation,
        execution_plan=execution_plan,
        decision=decision,
    )
    source = WorkflowRecoveryAttemptSource(
        step_key="relax",
        source_attempt_id=source_attempt.id,
    )

    first = persist_workflow_dispatch_wave(
        store=store,
        plan=plan,
        orchestration=orchestration,
        concurrency=BatchConcurrencyPolicy(max_active=1),
        recovery_attempt_sources=(source,),
    )
    assert len(first.newly_persisted_attempt_ids) == 1
    child_id = first.newly_persisted_attempt_ids[0]

    reopened = store.open()
    failed_child = replace(
        next(item for item in reopened.execution_attempts if item.id == child_id),
        status=ExecutionAttemptStatus.FAILED,
    )
    store.save(
        replace(
            reopened,
            execution_attempts=tuple(
                failed_child if item.id == child_id else item
                for item in reopened.execution_attempts
            ),
        )
    )

    replay = persist_workflow_dispatch_wave(
        store=store,
        plan=plan,
        orchestration=orchestration,
        concurrency=BatchConcurrencyPolicy(max_active=1),
        recovery_attempt_sources=(source,),
    )
    final_bundle = store.open()

    assert replay.newly_persisted_attempt_ids == ()
    assert replay.wave.tickets == ()
    assert len(final_bundle.execution_attempts) == 2
    child = next(item for item in final_bundle.execution_attempts if item.id == child_id)
    assert child.previous_attempt_id == source_attempt.id


def test_recovery_requires_explicit_latest_source_when_decision_is_unconsumed(
    tmp_path: Path,
) -> None:
    _, _, store, plan, _, materialization = _persist_root_materialization(tmp_path)
    calculation = replace(
        materialization.calculation,
        status=CalculationScientificStatus.FAILED,
    )
    execution_plan = _execution_plan(calculation)
    attempt_1 = replace(
        create_execution_attempt(plan=execution_plan, calculation=calculation),
        status=ExecutionAttemptStatus.FAILED,
    )
    attempt_2 = replace(
        create_execution_attempt(
            plan=execution_plan,
            calculation=calculation,
            existing_attempts=(attempt_1,),
        ),
        status=ExecutionAttemptStatus.FAILED,
        previous_attempt_id=None,
    )
    bundle = store.open()
    store.save(
        replace(
            bundle,
            calculations=tuple(
                calculation if item.id == calculation.id else item
                for item in bundle.calculations
            ),
            execution_attempts=(attempt_1, attempt_2),
        )
    )
    decision = classify_recovery(
        plan=execution_plan,
        request=RecoveryRequest(
            cause=RecoveryCause.VASP_FAILURE,
            evidence=ExecutionEvidence.VASP_LAUNCH_CONFIRMED,
        ),
    )
    orchestration = _recovery_orchestration(
        plan_id=plan.id,
        binding=materialization.binding,
        calculation=calculation,
        execution_plan=execution_plan,
        decision=decision,
    )

    with pytest.raises(ValueError, match="latest persisted attempt"):
        persist_workflow_dispatch_wave(
            store=store,
            plan=plan,
            orchestration=orchestration,
            concurrency=BatchConcurrencyPolicy(max_active=1),
            recovery_attempt_sources=(
                WorkflowRecoveryAttemptSource(
                    step_key="relax",
                    source_attempt_id=attempt_1.id,
                ),
            ),
        )

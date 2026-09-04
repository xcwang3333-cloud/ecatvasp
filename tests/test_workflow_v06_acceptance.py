from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from ecatvasp.domain import (
    Calculation,
    CalculationScientificStatus,
    KPointPolicy,
    KPointPolicyKind,
    Lattice,
    MethodDefinition,
    MethodFingerprint,
    PotcarIdentity,
    Project,
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
    new_atom_uid,
)
from ecatvasp.domain.ids import new_catalyst_id
from ecatvasp.storage import ProjectBundle, ProjectStore
from ecatvasp.vasp.contracts import (
    LatticeAxis,
    ProjectNumericalLock,
    VaspSystemContext,
    VaspSystemKind,
)
from ecatvasp.vasp.recipes import (
    RECIPE_CHARGE_DENSITY_STATIC,
    RECIPE_DOS_PREREQUISITE,
    RECIPE_GROUND_STATE_STATIC,
    RECIPE_LOBSTER_PREREQUISITE,
    RECIPE_SLAB_RELAX,
)
from ecatvasp.vasp.results import ConvergenceVerdict, VaspConvergenceAssessment
from ecatvasp.vasp.structure_promotion import VaspStructurePromotionResult
from ecatvasp.workflow import (
    WORKFLOW_RECIPE_SLAB_SCIENTIFIC_PREPARATION,
    AcceptedStructureSource,
    WorkflowAcceptanceError,
    WorkflowAcceptanceState,
    WorkflowOrchestrationAction,
    evaluate_workflow_freshness,
    evaluate_workflow_recovery_policy,
    evaluate_workflow_scientific_gates,
    materialize_workflow_step,
    plan_scientific_workflow,
    reconcile_workflow_orchestration,
    validate_v06_workflow_acceptance,
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
        recipe=RecipeIdentity(recipe_id=recipe_id),
    )


def _context() -> VaspSystemContext:
    return VaspSystemContext(
        kind=VaspSystemKind.SLAB_2D,
        vacuum_axis=LatticeAxis.C,
    )


def _lock(
    project: Project,
    fingerprint: MethodFingerprint,
) -> ProjectNumericalLock:
    return ProjectNumericalLock(
        project_id=project.id,
        system_kind=VaspSystemKind.SLAB_2D,
        core_method_hash=fingerprint.core_method_hash,
        encut_ev=fingerprint.protocol.encut_ev,
        encut_validation_hash="b" * 64,
        kpoints=fingerprint.protocol.kpoints,
        kpoints_validation_hash="c" * 64,
    )


def _plan(project: Project, root: StructureSnapshot) -> ScientificWorkflowPlan:
    return plan_scientific_workflow(
        project_id=project.id,
        workflow_recipe=WorkflowRecipeIdentity(
            WORKFLOW_RECIPE_SLAB_SCIENTIFIC_PREPARATION
        ),
        root_structure_snapshot_id=root.id,
    ).plan


def _accepted_relaxation(
    *,
    project: Project,
    plan: ScientificWorkflowPlan,
    root: StructureSnapshot,
) -> tuple[
    MethodFingerprint,
    Calculation,
    WorkflowStepBinding,
    StructureSnapshot,
    AcceptedStructureSource,
]:
    fingerprint = _fingerprint(RECIPE_SLAB_RELAX)
    materialized = materialize_workflow_step(
        plan=plan,
        step_key="relax",
        fingerprint=fingerprint,
        system_context=_context(),
        project_lock=_lock(project, fingerprint),
        root_snapshot=root,
    )
    calculation = replace(
        materialized.calculation,
        status=CalculationScientificStatus.CONVERGED,
    )
    relaxed = _snapshot(parent=root, z=0.31)
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
    return fingerprint, calculation, materialized.binding, relaxed, source


def _complete_workflow(tmp_path: Path):
    project = Project(name="v0.6 acceptance", slug="v06-acceptance")
    root = _snapshot()
    plan = _plan(project, root)
    relax_fp, relax, relax_binding, relaxed, source = _accepted_relaxation(
        project=project,
        plan=plan,
        root=root,
    )

    fingerprints: list[MethodFingerprint] = [relax_fp]
    calculations: list[Calculation] = [relax]
    bindings: list[WorkflowStepBinding] = [relax_binding]
    recipe_by_step = {
        "static": RECIPE_GROUND_STATE_STATIC,
        "dos": RECIPE_DOS_PREREQUISITE,
        "charge": RECIPE_CHARGE_DENSITY_STATIC,
        "lobster": RECIPE_LOBSTER_PREREQUISITE,
    }
    for step_key, recipe_id in recipe_by_step.items():
        fingerprint = _fingerprint(recipe_id)
        materialized = materialize_workflow_step(
            plan=plan,
            step_key=step_key,
            fingerprint=fingerprint,
            system_context=_context(),
            project_lock=_lock(project, fingerprint),
            accepted_structure_source=source,
        )
        fingerprints.append(fingerprint)
        calculations.append(
            replace(
                materialized.calculation,
                status=CalculationScientificStatus.CONVERGED,
            )
        )
        bindings.append(materialized.binding)

    store = ProjectStore(tmp_path)
    store.save(
        ProjectBundle(
            project=project,
            structure_snapshots=(root, relaxed),
            method_fingerprints=tuple(fingerprints),
            workflow_plans=(plan,),
            calculations=tuple(calculations),
            workflow_step_bindings=tuple(bindings),
        )
    )
    freshness = evaluate_workflow_freshness(
        plan=plan,
        bindings=tuple(bindings),
        calculations=tuple(calculations),
        dependencies=(),
        current_hashes={},
        accepted_structure_sources=(source,),
    )
    gates = evaluate_workflow_scientific_gates(
        plan=plan,
        bindings=tuple(bindings),
        calculations=tuple(calculations),
        freshness=freshness,
        accepted_structure_sources=(source,),
    )
    recovery = evaluate_workflow_recovery_policy(plan=plan, gates=gates)
    orchestration = reconcile_workflow_orchestration(
        plan=plan,
        gates=gates,
        recovery=recovery,
    )
    return store, plan, gates, recovery, orchestration, source


def _initial_workflow(tmp_path: Path):
    project = Project(name="v0.6 initial", slug="v06-initial")
    root = _snapshot()
    plan = _plan(project, root)
    store = ProjectStore(tmp_path)
    store.save(
        ProjectBundle(
            project=project,
            structure_snapshots=(root,),
            workflow_plans=(plan,),
        )
    )
    freshness = evaluate_workflow_freshness(
        plan=plan,
        bindings=(),
        calculations=(),
        dependencies=(),
        current_hashes={},
    )
    gates = evaluate_workflow_scientific_gates(
        plan=plan,
        bindings=(),
        calculations=(),
        freshness=freshness,
    )
    recovery = evaluate_workflow_recovery_policy(plan=plan, gates=gates)
    orchestration = reconcile_workflow_orchestration(
        plan=plan,
        gates=gates,
        recovery=recovery,
    )
    return store, plan, gates, recovery, orchestration


def test_complete_workflow_acceptance_is_stable_across_reopen(tmp_path: Path) -> None:
    store, plan, gates, recovery, orchestration, _ = _complete_workflow(tmp_path)

    first = validate_v06_workflow_acceptance(
        store=store,
        plan=plan,
        gates=gates,
        recovery=recovery,
        orchestration=orchestration,
    )
    second = validate_v06_workflow_acceptance(
        store=ProjectStore(tmp_path),
        plan=plan,
        gates=gates,
        recovery=recovery,
        orchestration=orchestration,
    )

    assert first.state is WorkflowAcceptanceState.COMPLETE
    assert first.complete
    assert first.scheduler_node_ids == ()
    assert len(first.steps) == len(plan.steps)
    assert all(
        item.orchestration_action is WorkflowOrchestrationAction.SATISFIED
        for item in first.steps
    )
    assert first.acceptance_hash == second.acceptance_hash
    assert first.resume_hash == second.resume_hash


def test_initial_canonical_workflow_is_accepted_as_resumable(tmp_path: Path) -> None:
    store, plan, gates, recovery, orchestration = _initial_workflow(tmp_path)

    report = validate_v06_workflow_acceptance(
        store=store,
        plan=plan,
        gates=gates,
        recovery=recovery,
        orchestration=orchestration,
    )

    assert report.state is WorkflowAcceptanceState.RESUMABLE
    assert not report.complete
    assert report.steps[0].step_key == "charge"
    assert any(
        item.orchestration_action is WorkflowOrchestrationAction.MATERIALIZE_STEP
        for item in report.steps
    )


def test_failed_current_generation_is_action_required(tmp_path: Path) -> None:
    project = Project(name="v0.6 failed", slug="v06-failed")
    root = _snapshot()
    plan = _plan(project, root)
    fingerprint = _fingerprint(RECIPE_SLAB_RELAX)
    materialized = materialize_workflow_step(
        plan=plan,
        step_key="relax",
        fingerprint=fingerprint,
        system_context=_context(),
        project_lock=_lock(project, fingerprint),
        root_snapshot=root,
    )
    calculation = replace(
        materialized.calculation,
        status=CalculationScientificStatus.FAILED,
    )
    store = ProjectStore(tmp_path)
    store.save(
        ProjectBundle(
            project=project,
            structure_snapshots=(root,),
            method_fingerprints=(fingerprint,),
            workflow_plans=(plan,),
            calculations=(calculation,),
            workflow_step_bindings=(materialized.binding,),
        )
    )
    freshness = evaluate_workflow_freshness(
        plan=plan,
        bindings=(materialized.binding,),
        calculations=(calculation,),
        dependencies=(),
        current_hashes={},
    )
    gates = evaluate_workflow_scientific_gates(
        plan=plan,
        bindings=(materialized.binding,),
        calculations=(calculation,),
        freshness=freshness,
    )
    recovery = evaluate_workflow_recovery_policy(plan=plan, gates=gates)
    orchestration = reconcile_workflow_orchestration(
        plan=plan,
        gates=gates,
        recovery=recovery,
    )

    report = validate_v06_workflow_acceptance(
        store=store,
        plan=plan,
        gates=gates,
        recovery=recovery,
        orchestration=orchestration,
    )
    assert report.state is WorkflowAcceptanceState.ACTION_REQUIRED
    assert any(
        item.orchestration_action
        is WorkflowOrchestrationAction.RECOVERY_DECISION_REQUIRED
        for item in report.steps
    )


def test_acceptance_rejects_projection_stale_after_durable_calculation_change(
    tmp_path: Path,
) -> None:
    store, plan, gates, recovery, orchestration, _ = _complete_workflow(tmp_path)
    bundle = store.open()
    current = bundle.calculations[0]
    store.save(
        replace(
            bundle,
            calculations=(
                replace(current, status=CalculationScientificStatus.FAILED),
                *bundle.calculations[1:],
            ),
        )
    )

    with pytest.raises(
        WorkflowAcceptanceError,
        match="does not match reopened current binding generations",
    ):
        validate_v06_workflow_acceptance(
            store=store,
            plan=plan,
            gates=gates,
            recovery=recovery,
            orchestration=orchestration,
        )


def test_acceptance_rejects_open_edge_to_non_durable_promoted_snapshot(
    tmp_path: Path,
) -> None:
    project = Project(name="v0.6 missing snapshot", slug="v06-missing-snapshot")
    root = _snapshot()
    plan = _plan(project, root)
    fingerprint, relax, binding, _, source = _accepted_relaxation(
        project=project,
        plan=plan,
        root=root,
    )
    store = ProjectStore(tmp_path)
    store.save(
        ProjectBundle(
            project=project,
            structure_snapshots=(root,),
            method_fingerprints=(fingerprint,),
            workflow_plans=(plan,),
            calculations=(relax,),
            workflow_step_bindings=(binding,),
        )
    )
    freshness = evaluate_workflow_freshness(
        plan=plan,
        bindings=(binding,),
        calculations=(relax,),
        dependencies=(),
        current_hashes={},
        accepted_structure_sources=(source,),
    )
    gates = evaluate_workflow_scientific_gates(
        plan=plan,
        bindings=(binding,),
        calculations=(relax,),
        freshness=freshness,
        accepted_structure_sources=(source,),
    )
    recovery = evaluate_workflow_recovery_policy(plan=plan, gates=gates)
    orchestration = reconcile_workflow_orchestration(
        plan=plan,
        gates=gates,
        recovery=recovery,
    )

    with pytest.raises(
        WorkflowAcceptanceError,
        match="non-durable StructureSnapshot",
    ):
        validate_v06_workflow_acceptance(
            store=store,
            plan=plan,
            gates=gates,
            recovery=recovery,
            orchestration=orchestration,
        )


def test_acceptance_rejects_duplicate_scientific_plan_hash(tmp_path: Path) -> None:
    project = Project(name="v0.6 duplicate plan", slug="v06-duplicate-plan")
    root = _snapshot()
    plan = _plan(project, root)
    duplicate = _plan(project, root)
    assert duplicate.id != plan.id
    assert duplicate.plan_hash == plan.plan_hash

    store = ProjectStore(tmp_path)
    store.save(
        ProjectBundle(
            project=project,
            structure_snapshots=(root,),
            workflow_plans=(plan, duplicate),
        )
    )
    freshness = evaluate_workflow_freshness(
        plan=plan,
        bindings=(),
        calculations=(),
        dependencies=(),
        current_hashes={},
    )
    gates = evaluate_workflow_scientific_gates(
        plan=plan,
        bindings=(),
        calculations=(),
        freshness=freshness,
    )
    recovery = evaluate_workflow_recovery_policy(plan=plan, gates=gates)
    orchestration = reconcile_workflow_orchestration(
        plan=plan,
        gates=gates,
        recovery=recovery,
    )

    with pytest.raises(
        WorkflowAcceptanceError,
        match="unique persisted scientific plan_hash",
    ):
        validate_v06_workflow_acceptance(
            store=store,
            plan=plan,
            gates=gates,
            recovery=recovery,
            orchestration=orchestration,
        )

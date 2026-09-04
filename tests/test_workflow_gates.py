from __future__ import annotations

from dataclasses import replace

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
    ProtocolDefinition,
    RecipeIdentity,
    StructureOrigin,
    StructureSite,
    StructureSnapshot,
    StructureVariant,
    VariantType,
    WorkflowRecipeIdentity,
    WorkflowStepBinding,
)
from ecatvasp.domain.ids import new_atom_uid, new_catalyst_id, new_workflow_step_binding_id
from ecatvasp.provenance import (
    DependencyKind,
    DependencyRecord,
    FreshnessState,
    scientific_hash,
)
from ecatvasp.vasp.contracts import (
    LatticeAxis,
    ProjectNumericalLock,
    VaspSystemContext,
    VaspSystemKind,
)
from ecatvasp.vasp.recipes import RECIPE_GROUND_STATE_STATIC, RECIPE_SLAB_RELAX
from ecatvasp.vasp.results import ConvergenceVerdict, VaspConvergenceAssessment
from ecatvasp.vasp.structure_promotion import VaspStructurePromotionResult
from ecatvasp.workflow import (
    WORKFLOW_EDGE_ACCEPTED_STRUCTURE,
    WORKFLOW_RECIPE_SLAB_SCIENTIFIC_PREPARATION,
    AcceptedStructureSource,
    WorkflowEdgeGateVerdict,
    WorkflowGateError,
    WorkflowStepReadiness,
    WorkflowStepScientificState,
    evaluate_workflow_freshness,
    evaluate_workflow_scientific_gates,
    materialize_workflow_step,
    plan_scientific_workflow,
    resolve_workflow_binding_generations,
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


def _lock(plan, fingerprint: MethodFingerprint) -> ProjectNumericalLock:
    return ProjectNumericalLock(
        project_id=plan.project_id,
        system_kind=VaspSystemKind.SLAB_2D,
        core_method_hash=fingerprint.core_method_hash,
        encut_ev=fingerprint.protocol.encut_ev,
        encut_validation_hash="b" * 64,
        kpoints=fingerprint.protocol.kpoints,
        kpoints_validation_hash="c" * 64,
    )


def _plan(root: StructureSnapshot):
    return plan_scientific_workflow(
        project_id=_lock_project_id(),
        workflow_recipe=WorkflowRecipeIdentity(
            WORKFLOW_RECIPE_SLAB_SCIENTIFIC_PREPARATION
        ),
        root_structure_snapshot_id=root.id,
    ).plan


def _lock_project_id():
    from ecatvasp.domain.ids import new_project_id

    return new_project_id()


def _relax_generation(
    *,
    plan,
    root: StructureSnapshot,
    status: CalculationScientificStatus = CalculationScientificStatus.CONVERGED,
    previous_binding: WorkflowStepBinding | None = None,
    z: float = 0.31,
):
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


def _static_generation(*, plan, source: AcceptedStructureSource):
    fingerprint = _fingerprint(RECIPE_GROUND_STATE_STATIC)
    materialized = materialize_workflow_step(
        plan=plan,
        step_key="static",
        fingerprint=fingerprint,
        system_context=_context(),
        project_lock=_lock(plan, fingerprint),
        accepted_structure_source=source,
    )
    return replace(
        materialized.calculation,
        status=CalculationScientificStatus.CONVERGED,
    ), materialized.binding


def test_converged_fresh_promoted_upstream_opens_downstream_gate() -> None:
    root = _snapshot()
    plan = _plan(root)
    relax, relax_binding, source = _relax_generation(plan=plan, root=root)

    freshness = evaluate_workflow_freshness(
        plan=plan,
        bindings=(relax_binding,),
        calculations=(relax,),
        dependencies=(),
        current_hashes={},
        accepted_structure_sources=(source,),
    )
    gates = evaluate_workflow_scientific_gates(
        plan=plan,
        bindings=(relax_binding,),
        calculations=(relax,),
        freshness=freshness,
        accepted_structure_sources=(source,),
    )

    assert gates.step("relax").scientific_state is WorkflowStepScientificState.PASSED
    assert gates.step("relax").readiness is WorkflowStepReadiness.SATISFIED
    assert gates.step("static").scientific_state is WorkflowStepScientificState.UNMATERIALIZED
    assert gates.step("static").readiness is WorkflowStepReadiness.READY
    edge = gates.edge("relax", "static", WORKFLOW_EDGE_ACCEPTED_STRUCTURE)
    assert edge.verdict is WorkflowEdgeGateVerdict.OPEN
    assert edge.source_binding_id == relax_binding.id
    assert edge.accepted_structure_snapshot_id == source.promotion.snapshot.id


@pytest.mark.parametrize(
    ("status", "expected_verdict"),
    (
        (CalculationScientificStatus.DRAFT, WorkflowEdgeGateVerdict.WAITING),
        (CalculationScientificStatus.RUNNING, WorkflowEdgeGateVerdict.WAITING),
        (CalculationScientificStatus.PARSING, WorkflowEdgeGateVerdict.WAITING),
        (
            CalculationScientificStatus.COMPLETED_UNCONVERGED,
            WorkflowEdgeGateVerdict.BLOCKED,
        ),
        (CalculationScientificStatus.BLOCKED, WorkflowEdgeGateVerdict.BLOCKED),
        (CalculationScientificStatus.FAILED, WorkflowEdgeGateVerdict.BLOCKED),
        (CalculationScientificStatus.CANCELLED, WorkflowEdgeGateVerdict.BLOCKED),
    ),
)
def test_nonpassing_upstream_never_opens_downstream(
    status: CalculationScientificStatus,
    expected_verdict: WorkflowEdgeGateVerdict,
) -> None:
    root = _snapshot()
    plan = _plan(root)
    relax, relax_binding, source = _relax_generation(
        plan=plan,
        root=root,
        status=status,
    )
    freshness = evaluate_workflow_freshness(
        plan=plan,
        bindings=(relax_binding,),
        calculations=(relax,),
        dependencies=(),
        current_hashes={},
        accepted_structure_sources=(source,),
    )
    gates = evaluate_workflow_scientific_gates(
        plan=plan,
        bindings=(relax_binding,),
        calculations=(relax,),
        freshness=freshness,
        accepted_structure_sources=(source,),
    )

    edge = gates.edge("relax", "static", WORKFLOW_EDGE_ACCEPTED_STRUCTURE)
    assert edge.verdict is expected_verdict
    assert gates.step("static").readiness is (
        WorkflowStepReadiness.WAITING
        if expected_verdict is WorkflowEdgeGateVerdict.WAITING
        else WorkflowStepReadiness.BLOCKED
    )


def test_unpromoted_converged_relaxation_keeps_downstream_closed() -> None:
    root = _snapshot()
    plan = _plan(root)
    relax, relax_binding, _ = _relax_generation(plan=plan, root=root)
    freshness = evaluate_workflow_freshness(
        plan=plan,
        bindings=(relax_binding,),
        calculations=(relax,),
        dependencies=(),
        current_hashes={},
    )
    gates = evaluate_workflow_scientific_gates(
        plan=plan,
        bindings=(relax_binding,),
        calculations=(relax,),
        freshness=freshness,
    )

    edge = gates.edge("relax", "static", WORKFLOW_EDGE_ACCEPTED_STRUCTURE)
    assert edge.verdict is WorkflowEdgeGateVerdict.WAITING
    assert "accepted_structure_not_promoted" in edge.reason_codes
    assert gates.step("static").readiness is WorkflowStepReadiness.WAITING


def test_stale_or_invalid_scientific_inputs_fail_closed() -> None:
    root = _snapshot()
    plan = _plan(root)
    relax, relax_binding, source = _relax_generation(plan=plan, root=root)
    dependency = DependencyRecord(
        upstream_id=root.id,
        downstream_id=relax.id,
        kind=DependencyKind.SCIENTIFIC,
        role="input_structure",
        recorded_hash=scientific_hash(root),
    )
    stale = evaluate_workflow_freshness(
        plan=plan,
        bindings=(relax_binding,),
        calculations=(relax,),
        dependencies=(dependency,),
        current_hashes={root.id: "f" * 64},
        accepted_structure_sources=(source,),
    )
    stale_gates = evaluate_workflow_scientific_gates(
        plan=plan,
        bindings=(relax_binding,),
        calculations=(relax,),
        freshness=stale,
        accepted_structure_sources=(source,),
    )
    assert stale.result(relax.id).state is FreshnessState.STALE
    assert stale_gates.step("relax").scientific_state is WorkflowStepScientificState.STALE
    assert (
        stale_gates.edge("relax", "static", WORKFLOW_EDGE_ACCEPTED_STRUCTURE).verdict
        is WorkflowEdgeGateVerdict.BLOCKED
    )

    invalid = evaluate_workflow_freshness(
        plan=plan,
        bindings=(relax_binding,),
        calculations=(relax,),
        dependencies=(),
        current_hashes={},
        accepted_structure_sources=(source,),
        invalid_ids={relax.id},
    )
    invalid_gates = evaluate_workflow_scientific_gates(
        plan=plan,
        bindings=(relax_binding,),
        calculations=(relax,),
        freshness=invalid,
        accepted_structure_sources=(source,),
    )
    assert invalid.result(relax.id).state is FreshnessState.INVALID
    assert invalid_gates.step("relax").scientific_state is WorkflowStepScientificState.INVALID


def test_stale_promoted_snapshot_blocks_accepted_structure_edge() -> None:
    root = _snapshot()
    plan = _plan(root)
    relax, relax_binding, source = _relax_generation(plan=plan, root=root)
    dependency = DependencyRecord(
        upstream_id=root.id,
        downstream_id=source.promotion.snapshot.id,
        kind=DependencyKind.SCIENTIFIC,
        role="reconstruction_input",
        recorded_hash=scientific_hash(root),
    )
    freshness = evaluate_workflow_freshness(
        plan=plan,
        bindings=(relax_binding,),
        calculations=(relax,),
        dependencies=(dependency,),
        current_hashes={root.id: "f" * 64},
        accepted_structure_sources=(source,),
    )
    gates = evaluate_workflow_scientific_gates(
        plan=plan,
        bindings=(relax_binding,),
        calculations=(relax,),
        freshness=freshness,
        accepted_structure_sources=(source,),
    )

    assert freshness.result(source.promotion.snapshot.id).state is FreshnessState.STALE
    edge = gates.edge("relax", "static", WORKFLOW_EDGE_ACCEPTED_STRUCTURE)
    assert edge.verdict is WorkflowEdgeGateVerdict.BLOCKED
    assert "accepted_structure_stale" in edge.reason_codes


def test_new_upstream_generation_supersedes_old_and_stales_old_downstream_binding() -> None:
    root = _snapshot()
    plan = _plan(root)
    relax1, binding1, source1 = _relax_generation(plan=plan, root=root, z=0.31)
    static, static_binding = _static_generation(plan=plan, source=source1)
    relax2, binding2, source2 = _relax_generation(
        plan=plan,
        root=root,
        previous_binding=binding1,
        z=0.33,
    )

    bindings = (binding1, binding2, static_binding)
    calculations = (relax1, relax2, static)
    freshness = evaluate_workflow_freshness(
        plan=plan,
        bindings=bindings,
        calculations=calculations,
        dependencies=(),
        current_hashes={},
        accepted_structure_sources=(source1, source2),
    )
    gates = evaluate_workflow_scientific_gates(
        plan=plan,
        bindings=bindings,
        calculations=calculations,
        freshness=freshness,
        accepted_structure_sources=(source1, source2),
    )

    assert freshness.result(relax1.id).state is FreshnessState.SUPERSEDED
    assert freshness.result(relax2.id).state is FreshnessState.FRESH
    assert gates.superseded_calculation_ids == (relax1.id,)
    assert gates.step("relax").calculation_id == relax2.id
    assert gates.step("static").scientific_state is WorkflowStepScientificState.STALE
    assert gates.step("static").readiness is WorkflowStepReadiness.BLOCKED
    assert "accepted_structure_binding_superseded" in gates.step("static").reason_codes
    edge = gates.edge("relax", "static", WORKFLOW_EDGE_ACCEPTED_STRUCTURE)
    assert edge.verdict is WorkflowEdgeGateVerdict.OPEN
    assert edge.source_binding_id == binding2.id
    assert edge.accepted_structure_snapshot_id == source2.promotion.snapshot.id


def test_binding_selection_rejects_noncontiguous_history() -> None:
    root = _snapshot()
    plan = _plan(root)
    relax, binding1, _ = _relax_generation(plan=plan, root=root)
    invalid_binding = WorkflowStepBinding(
        workflow_plan_id=plan.id,
        step_key="relax",
        generation=2,
        calculation_id=relax.id,
        resolved_input_structure_snapshot_id=root.id,
        materialization_reason="invalid isolated generation",
        id=new_workflow_step_binding_id(),
        supersedes_binding_id=binding1.id,
    )

    with pytest.raises(WorkflowGateError, match="contiguous from 1"):
        resolve_workflow_binding_generations(
            plan=plan,
            bindings=(invalid_binding,),
            calculations=(relax,),
        )


def test_unknown_freshness_override_fails_closed() -> None:
    root = _snapshot()
    plan = _plan(root)
    relax, relax_binding, source = _relax_generation(plan=plan, root=root)

    from uuid import uuid4

    with pytest.raises(WorkflowGateError, match="outside the evaluation graph"):
        evaluate_workflow_freshness(
            plan=plan,
            bindings=(relax_binding,),
            calculations=(relax,),
            dependencies=(),
            current_hashes={},
            accepted_structure_sources=(source,),
            invalid_ids={uuid4()},
        )

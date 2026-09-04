from __future__ import annotations

from dataclasses import replace

import pytest

from ecatvasp.domain import (
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
    StructureVariant,
    VariantType,
    WorkflowRecipeIdentity,
)
from ecatvasp.domain.ids import new_atom_uid, new_catalyst_id, new_project_id
from ecatvasp.storage import ProjectBundle
from ecatvasp.vasp.contracts import (
    LatticeAxis,
    ProjectNumericalLock,
    VaspSystemContext,
    VaspSystemKind,
)
from ecatvasp.vasp.recipes import (
    RECIPE_GROUND_STATE_STATIC,
    RECIPE_SLAB_RELAX,
)
from ecatvasp.vasp.results import ConvergenceVerdict, VaspConvergenceAssessment
from ecatvasp.vasp.structure_promotion import VaspStructurePromotionResult
from ecatvasp.workflow import (
    WORKFLOW_RECIPE_SLAB_SCIENTIFIC_PREPARATION,
    plan_scientific_workflow,
)
from ecatvasp.workflow.materialization import (
    AcceptedStructureSource,
    WorkflowMaterializationError,
    materialize_workflow_step,
)


def _snapshot(*, parent: StructureSnapshot | None = None) -> StructureSnapshot:
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
                fractional_coords=(0.1, 0.2, 0.3 if parent is None else 0.31),
            ),
        ),
        origin=StructureOrigin.IMPORTED if parent is None else StructureOrigin.RELAXED,
        parent_snapshot_id=None if parent is None else parent.id,
    )


def _fingerprint(recipe_id: str, *, version: str = "1") -> MethodFingerprint:
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
        recipe=RecipeIdentity(recipe_id=recipe_id, version=version),
    )


def _context() -> VaspSystemContext:
    return VaspSystemContext(
        kind=VaspSystemKind.SLAB_2D,
        vacuum_axis=LatticeAxis.C,
    )


def _lock(project_id: object, fingerprint: MethodFingerprint) -> ProjectNumericalLock:
    return ProjectNumericalLock(
        project_id=project_id,  # type: ignore[arg-type]
        system_kind=VaspSystemKind.SLAB_2D,
        core_method_hash=fingerprint.core_method_hash,
        encut_ev=fingerprint.protocol.encut_ev,
        encut_validation_hash="b" * 64,
        kpoints=fingerprint.protocol.kpoints,
        kpoints_validation_hash="c" * 64,
    )


def _plan(root: StructureSnapshot):
    project_id = new_project_id()
    return plan_scientific_workflow(
        project_id=project_id,
        workflow_recipe=WorkflowRecipeIdentity(
            WORKFLOW_RECIPE_SLAB_SCIENTIFIC_PREPARATION
        ),
        root_structure_snapshot_id=root.id,
    ).plan


def _promotion_source(*, plan, root: StructureSnapshot):
    relax_fingerprint = _fingerprint(RECIPE_SLAB_RELAX)
    relax = materialize_workflow_step(
        plan=plan,
        step_key="relax",
        fingerprint=relax_fingerprint,
        system_context=_context(),
        project_lock=_lock(plan.project_id, relax_fingerprint),
        root_snapshot=root,
    )
    relaxed = _snapshot(parent=root)
    variant = StructureVariant(
        catalyst_id=new_catalyst_id(),
        name="accepted relaxed structure",
        variant_type=VariantType.GEOMETRY,
        current_structure_snapshot_id=relaxed.id,
    )
    convergence = VaspConvergenceAssessment(
        calculation_type=relax.calculation.calculation_type,
        electronic=ConvergenceVerdict.CONVERGED,
        ionic=ConvergenceVerdict.CONVERGED,
        overall=ConvergenceVerdict.CONVERGED,
    )
    promotion = VaspStructurePromotionResult(
        updated_variant=variant,
        snapshot=relaxed,
        convergence=convergence,
    )
    source = AcceptedStructureSource(
        upstream_binding=relax.binding,
        upstream_calculation=relax.calculation,
        promotion=promotion,
    )
    return relax_fingerprint, relax, relaxed, source


def test_root_step_materializes_exact_plan_snapshot() -> None:
    root = _snapshot()
    plan = _plan(root)
    fingerprint = _fingerprint(RECIPE_SLAB_RELAX)

    result = materialize_workflow_step(
        plan=plan,
        step_key="relax",
        fingerprint=fingerprint,
        system_context=_context(),
        project_lock=_lock(plan.project_id, fingerprint),
        root_snapshot=root,
    )

    assert result.calculation.project_id == plan.project_id
    assert result.calculation.input_structure_snapshot_id == root.id
    assert result.calculation.method_fingerprint_id == fingerprint.id
    assert result.calculation.recipe_id == RECIPE_SLAB_RELAX
    assert result.binding.workflow_plan_id == plan.id
    assert result.binding.step_key == "relax"
    assert result.binding.generation == 1
    assert result.binding.resolved_input_structure_snapshot_id == root.id
    assert result.binding.supersedes_binding_id is None
    assert result.source_binding_id is None


def test_root_step_rejects_wrong_or_missing_root_snapshot() -> None:
    root = _snapshot()
    plan = _plan(root)
    fingerprint = _fingerprint(RECIPE_SLAB_RELAX)
    lock = _lock(plan.project_id, fingerprint)

    with pytest.raises(WorkflowMaterializationError, match="requires root_snapshot"):
        materialize_workflow_step(
            plan=plan,
            step_key="relax",
            fingerprint=fingerprint,
            system_context=_context(),
            project_lock=lock,
        )

    with pytest.raises(WorkflowMaterializationError, match="exact workflow plan root"):
        materialize_workflow_step(
            plan=plan,
            step_key="relax",
            fingerprint=fingerprint,
            system_context=_context(),
            project_lock=lock,
            root_snapshot=_snapshot(),
        )


def test_downstream_step_consumes_only_explicit_promoted_structure() -> None:
    root = _snapshot()
    plan = _plan(root)
    relax_fingerprint, relax, relaxed, source = _promotion_source(plan=plan, root=root)
    static_fingerprint = _fingerprint(RECIPE_GROUND_STATE_STATIC)

    static = materialize_workflow_step(
        plan=plan,
        step_key="static",
        fingerprint=static_fingerprint,
        system_context=_context(),
        project_lock=_lock(plan.project_id, static_fingerprint),
        accepted_structure_source=source,
    )

    assert static.resolved_input_snapshot.id == relaxed.id
    assert static.resolved_input_snapshot.id != root.id
    assert static.calculation.input_structure_snapshot_id == relaxed.id
    assert static.binding.resolved_input_structure_snapshot_id == relaxed.id
    assert static.source_binding_id == relax.binding.id

    project = Project(name="workflow", slug="workflow", id=plan.project_id)
    ProjectBundle(
        project=project,
        structure_snapshots=(root, relaxed),
        method_fingerprints=(relax_fingerprint, static_fingerprint),
        workflow_plans=(plan,),
        calculations=(relax.calculation, static.calculation),
        workflow_step_bindings=(relax.binding, static.binding),
    ).validate()


def test_downstream_step_rejects_bypassing_promotion_or_nonconverged_source() -> None:
    root = _snapshot()
    plan = _plan(root)
    static_fingerprint = _fingerprint(RECIPE_GROUND_STATE_STATIC)
    lock = _lock(plan.project_id, static_fingerprint)

    with pytest.raises(WorkflowMaterializationError, match="explicit accepted source"):
        materialize_workflow_step(
            plan=plan,
            step_key="static",
            fingerprint=static_fingerprint,
            system_context=_context(),
            project_lock=lock,
        )

    with pytest.raises(WorkflowMaterializationError, match="cannot bypass"):
        materialize_workflow_step(
            plan=plan,
            step_key="static",
            fingerprint=static_fingerprint,
            system_context=_context(),
            project_lock=lock,
            root_snapshot=root,
        )

    _, relax, relaxed, source = _promotion_source(plan=plan, root=root)
    nonconverged = replace(
        source.promotion,
        convergence=VaspConvergenceAssessment(
            calculation_type=relax.calculation.calculation_type,
            electronic=ConvergenceVerdict.CONVERGED,
            ionic=ConvergenceVerdict.NOT_CONVERGED,
            overall=ConvergenceVerdict.NOT_CONVERGED,
        ),
    )
    with pytest.raises(WorkflowMaterializationError, match="scientifically converged"):
        AcceptedStructureSource(
            upstream_binding=relax.binding,
            upstream_calculation=relax.calculation,
            promotion=nonconverged,
        )

    wrong_parent = replace(relaxed, parent_snapshot_id=_snapshot().id)
    wrong_promotion = replace(
        source.promotion,
        snapshot=wrong_parent,
        updated_variant=replace(
            source.promotion.updated_variant,
            current_structure_snapshot_id=wrong_parent.id,
        ),
    )
    with pytest.raises(WorkflowMaterializationError, match="direct child"):
        AcceptedStructureSource(
            upstream_binding=relax.binding,
            upstream_calculation=relax.calculation,
            promotion=wrong_promotion,
        )


def test_fingerprint_recipe_identity_and_context_fail_closed() -> None:
    root = _snapshot()
    plan = _plan(root)

    wrong_recipe = _fingerprint(RECIPE_GROUND_STATE_STATIC)
    with pytest.raises(WorkflowMaterializationError, match="recipe does not match"):
        materialize_workflow_step(
            plan=plan,
            step_key="relax",
            fingerprint=wrong_recipe,
            system_context=_context(),
            project_lock=_lock(plan.project_id, wrong_recipe),
            root_snapshot=root,
        )

    wrong_version = _fingerprint(RECIPE_SLAB_RELAX, version="2")
    with pytest.raises(WorkflowMaterializationError, match="version is not canonical"):
        materialize_workflow_step(
            plan=plan,
            step_key="relax",
            fingerprint=wrong_version,
            system_context=_context(),
            project_lock=_lock(plan.project_id, wrong_version),
            root_snapshot=root,
        )

    valid = _fingerprint(RECIPE_SLAB_RELAX)
    molecule_context = VaspSystemContext(kind=VaspSystemKind.MOLECULE_0D)
    with pytest.raises(WorkflowMaterializationError, match="incompatible"):
        materialize_workflow_step(
            plan=plan,
            step_key="relax",
            fingerprint=valid,
            system_context=molecule_context,
            project_lock=None,
            root_snapshot=root,
        )


def test_explicit_rematerialization_creates_contiguous_new_generation() -> None:
    root = _snapshot()
    plan = _plan(root)
    fingerprint = _fingerprint(RECIPE_SLAB_RELAX)
    lock = _lock(plan.project_id, fingerprint)

    first = materialize_workflow_step(
        plan=plan,
        step_key="relax",
        fingerprint=fingerprint,
        system_context=_context(),
        project_lock=lock,
        root_snapshot=root,
    )
    second = materialize_workflow_step(
        plan=plan,
        step_key="relax",
        fingerprint=fingerprint,
        system_context=_context(),
        project_lock=lock,
        root_snapshot=root,
        previous_binding=first.binding,
        materialization_reason="explicit operator rematerialization",
    )

    assert second.calculation.id != first.calculation.id
    assert second.binding.id != first.binding.id
    assert second.binding.generation == 2
    assert second.binding.supersedes_binding_id == first.binding.id
    assert second.binding.materialization_reason == "explicit operator rematerialization"


def test_previous_binding_must_be_same_plan_and_step() -> None:
    root = _snapshot()
    plan = _plan(root)
    fingerprint = _fingerprint(RECIPE_SLAB_RELAX)
    first = materialize_workflow_step(
        plan=plan,
        step_key="relax",
        fingerprint=fingerprint,
        system_context=_context(),
        project_lock=_lock(plan.project_id, fingerprint),
        root_snapshot=root,
    )

    other_root = _snapshot()
    other_plan = _plan(other_root)
    other_fingerprint = _fingerprint(RECIPE_SLAB_RELAX)
    with pytest.raises(WorkflowMaterializationError, match="another workflow plan"):
        materialize_workflow_step(
            plan=other_plan,
            step_key="relax",
            fingerprint=other_fingerprint,
            system_context=_context(),
            project_lock=_lock(other_plan.project_id, other_fingerprint),
            root_snapshot=other_root,
            previous_binding=first.binding,
        )

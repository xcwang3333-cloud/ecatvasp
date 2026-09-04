from __future__ import annotations

from dataclasses import replace
from hashlib import sha256

import pytest

from ecatvasp.domain import (
    Calculation,
    CalculationType,
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
    StructureSite,
    StructureSnapshot,
    WorkflowEdgeSpec,
    WorkflowRecipeIdentity,
    WorkflowStepBinding,
    WorkflowStepSpec,
    new_atom_uid,
)
from ecatvasp.provenance import ProvenanceRecord
from ecatvasp.storage import ProjectBundle, ProjectIntegrityError, ProjectStore, dumps_storage, loads_storage


def _digest(label: str) -> str:
    return sha256(label.encode()).hexdigest()


def _snapshot() -> StructureSnapshot:
    return StructureSnapshot(
        lattice=Lattice(
            vectors=((8.0, 0.0, 0.0), (0.0, 8.0, 0.0), (0.0, 0.0, 15.0))
        ),
        sites=(StructureSite(new_atom_uid(), "C", (0.5, 0.5, 0.5)),),
        label="workflow-root",
    )


def _method(recipe_id: str) -> MethodFingerprint:
    return MethodFingerprint(
        method=MethodDefinition(
            xc_functional="PBE",
            potcar_family="PBE_54",
            potcars=(PotcarIdentity("C", "C", _digest("C-potcar")),),
        ),
        protocol=ProtocolDefinition(
            encut_ev=450.0,
            kpoints=KPointPolicy(KPointPolicyKind.GAMMA_ONLY),
            ediffg_ev_per_angstrom=-0.02,
        ),
        recipe=RecipeIdentity(recipe_id),
    )


def _one_step_bundle() -> ProjectBundle:
    project = Project(name="Workflow", slug="workflow")
    snapshot = _snapshot()
    recipe_id = "ECatVASP.VASP.SlabRelax"
    method = _method(recipe_id)
    calculation = Calculation(
        project_id=project.id,
        calculation_type=CalculationType.RELAX,
        input_structure_snapshot_id=snapshot.id,
        recipe_id=recipe_id,
        method_fingerprint_id=method.id,
    )
    plan = ScientificWorkflowPlan(
        project_id=project.id,
        workflow_recipe=WorkflowRecipeIdentity("ECatVASP.Workflow.Relax"),
        root_structure_snapshot_id=snapshot.id,
        steps=(WorkflowStepSpec("relax", CalculationType.RELAX, recipe_id),),
    )
    binding = WorkflowStepBinding(
        workflow_plan_id=plan.id,
        step_key="relax",
        generation=1,
        calculation_id=calculation.id,
        resolved_input_structure_snapshot_id=snapshot.id,
        materialization_reason="root_input",
    )
    return ProjectBundle(
        project=project,
        structure_snapshots=(snapshot,),
        method_fingerprints=(method,),
        workflow_plans=(plan,),
        calculations=(calculation,),
        workflow_step_bindings=(binding,),
    )


def test_workflow_plan_is_canonical_content_addressed_dag() -> None:
    project = Project(name="DAG", slug="dag")
    snapshot = _snapshot()
    relax = WorkflowStepSpec("relax", CalculationType.RELAX, "ECatVASP.VASP.SlabRelax")
    static = WorkflowStepSpec(
        "static",
        CalculationType.STATIC,
        "ECatVASP.VASP.GroundStateStatic",
    )
    frequency = WorkflowStepSpec(
        "frequency",
        CalculationType.FREQUENCY,
        "ECatVASP.VASP.SelectedAtomFrequency",
    )
    edges = (
        WorkflowEdgeSpec("relax", "static", "accepted_structure"),
        WorkflowEdgeSpec("relax", "frequency", "accepted_structure"),
    )

    first = ScientificWorkflowPlan(
        project_id=project.id,
        workflow_recipe=WorkflowRecipeIdentity("ECatVASP.Workflow.RelaxFanout"),
        root_structure_snapshot_id=snapshot.id,
        steps=(static, relax, frequency),
        edges=tuple(reversed(edges)),
    )
    second = ScientificWorkflowPlan(
        project_id=project.id,
        workflow_recipe=WorkflowRecipeIdentity("ECatVASP.Workflow.RelaxFanout"),
        root_structure_snapshot_id=snapshot.id,
        steps=(frequency, static, relax),
        edges=edges,
    )

    assert tuple(step.key for step in first.steps) == ("frequency", "relax", "static")
    assert first.edges == second.edges
    assert first.plan_hash == second.plan_hash
    assert first.id != second.id


def test_workflow_plan_rejects_cycles_and_unknown_edge_steps() -> None:
    project = Project(name="DAG", slug="dag")
    snapshot = _snapshot()
    relax = WorkflowStepSpec("relax", CalculationType.RELAX, "relax")
    static = WorkflowStepSpec("static", CalculationType.STATIC, "static")

    with pytest.raises(ValueError, match="form a DAG"):
        ScientificWorkflowPlan(
            project_id=project.id,
            workflow_recipe=WorkflowRecipeIdentity("ECatVASP.Workflow.Cycle"),
            root_structure_snapshot_id=snapshot.id,
            steps=(relax, static),
            edges=(WorkflowEdgeSpec("relax", "static"), WorkflowEdgeSpec("static", "relax")),
        )

    with pytest.raises(ValueError, match="reference steps"):
        ScientificWorkflowPlan(
            project_id=project.id,
            workflow_recipe=WorkflowRecipeIdentity("ECatVASP.Workflow.Unknown"),
            root_structure_snapshot_id=snapshot.id,
            steps=(relax,),
            edges=(WorkflowEdgeSpec("relax", "missing"),),
        )


def test_workflow_binding_generation_contract_is_fail_closed() -> None:
    bundle = _one_step_bundle()
    plan = bundle.workflow_plans[0]
    calculation = bundle.calculations[0]
    snapshot = bundle.structure_snapshots[0]

    with pytest.raises(ValueError, match="generation 1"):
        WorkflowStepBinding(
            workflow_plan_id=plan.id,
            step_key="relax",
            generation=1,
            calculation_id=calculation.id,
            resolved_input_structure_snapshot_id=snapshot.id,
            materialization_reason="root_input",
            supersedes_binding_id=bundle.workflow_step_bindings[0].id,
        )

    with pytest.raises(ValueError, match="requires supersedes_binding_id"):
        WorkflowStepBinding(
            workflow_plan_id=plan.id,
            step_key="relax",
            generation=2,
            calculation_id=calculation.id,
            resolved_input_structure_snapshot_id=snapshot.id,
            materialization_reason="supersession",
        )


def test_workflow_contracts_round_trip_through_codec_and_project_store(tmp_path) -> None:
    bundle = _one_step_bundle()
    plan = bundle.workflow_plans[0]
    binding = bundle.workflow_step_bindings[0]

    assert loads_storage(dumps_storage(plan)) == plan
    assert loads_storage(dumps_storage(binding)) == binding

    ProjectStore(tmp_path).save(bundle)
    reopened = ProjectStore(tmp_path).open()

    assert reopened == bundle
    assert reopened.workflow_plans[0].plan_hash == plan.plan_hash
    assert reopened.workflow_step_bindings[0].binding_hash == binding.binding_hash


def test_project_bundle_rejects_workflow_binding_scientific_identity_mismatch() -> None:
    bundle = _one_step_bundle()
    calculation = replace(bundle.calculations[0], recipe_id="ECatVASP.VASP.OtherRelax")
    broken = replace(bundle, calculations=(calculation,))

    with pytest.raises(ProjectIntegrityError, match="recipe does not match"):
        broken.validate()


def test_workflow_orchestration_entities_do_not_become_scientific_provenance_subjects() -> None:
    bundle = _one_step_bundle()
    plan = bundle.workflow_plans[0]
    provenance = ProvenanceRecord(
        subject_id=plan.id,
        tool="ecatvasp-workflow",
        tool_version="0.6.0.dev0",
    )

    with pytest.raises(ProjectIntegrityError, match="ProvenanceRecord subject is missing"):
        replace(bundle, provenance_records=(provenance,)).validate()

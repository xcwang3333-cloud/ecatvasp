from __future__ import annotations

from dataclasses import replace

import pytest

from ecatvasp.domain import (
    CalculationType,
    ScientificWorkflowPlan,
    WorkflowEdgeSpec,
    WorkflowRecipeIdentity,
    WorkflowStepSpec,
)
from ecatvasp.domain.ids import new_project_id, new_structure_snapshot_id
from ecatvasp.vasp.recipes import (
    RECIPE_GROUND_STATE_STATIC,
    RECIPE_SLAB_RELAX,
    get_vasp_recipe_spec,
)
from ecatvasp.workflow import (
    WORKFLOW_EDGE_ACCEPTED_STRUCTURE,
    WORKFLOW_RECIPE_ADSORBATE_SCIENTIFIC_PREPARATION,
    WORKFLOW_RECIPE_GAS_REFERENCE_PREPARATION,
    WORKFLOW_RECIPE_REGISTRY,
    WORKFLOW_RECIPE_SLAB_SCIENTIFIC_PREPARATION,
    WORKFLOW_RECIPE_SPECS,
    WorkflowRecipeContractError,
    WorkflowRecipeSpec,
    get_workflow_recipe_spec,
    list_workflow_recipe_specs,
    validate_workflow_plan_recipe_contract,
)


def _plan_for(spec: WorkflowRecipeSpec, *, parameters_hash: str | None = None) -> ScientificWorkflowPlan:
    return ScientificWorkflowPlan(
        project_id=new_project_id(),
        workflow_recipe=spec.identity,
        root_structure_snapshot_id=new_structure_snapshot_id(),
        steps=spec.steps,
        edges=spec.edges,
        parameters_hash=parameters_hash,
    )


def test_registry_contains_three_stable_product_recipes() -> None:
    assert list_workflow_recipe_specs() == WORKFLOW_RECIPE_SPECS
    assert tuple(spec.recipe_id for spec in WORKFLOW_RECIPE_SPECS) == (
        WORKFLOW_RECIPE_SLAB_SCIENTIFIC_PREPARATION,
        WORKFLOW_RECIPE_ADSORBATE_SCIENTIFIC_PREPARATION,
        WORKFLOW_RECIPE_GAS_REFERENCE_PREPARATION,
    )
    assert len(WORKFLOW_RECIPE_REGISTRY) == 3

    for spec in WORKFLOW_RECIPE_SPECS:
        assert WORKFLOW_RECIPE_REGISTRY[spec.identity] is spec
        assert get_workflow_recipe_spec(spec.identity) is spec
        assert len(spec.definition_hash) == 64


def test_every_workflow_step_matches_the_canonical_vasp_recipe_registry() -> None:
    for workflow_spec in WORKFLOW_RECIPE_SPECS:
        for step in workflow_spec.steps:
            vasp_spec = get_vasp_recipe_spec(step.recipe_id)
            assert step.calculation_type is vasp_spec.calculation_type


def test_recipe_definition_hash_ignores_documentation_but_not_graph_semantics() -> None:
    spec = get_workflow_recipe_spec(
        WorkflowRecipeIdentity(WORKFLOW_RECIPE_SLAB_SCIENTIFIC_PREPARATION)
    )
    redocumented = replace(spec, description="Documentation is not scientific identity.")
    changed = replace(
        spec,
        edges=(
            *spec.edges,
            WorkflowEdgeSpec("static", "dos", "requires_converged"),
        ),
    )

    assert redocumented.definition_hash == spec.definition_hash
    assert changed.definition_hash != spec.definition_hash


def test_adsorbate_frequency_is_a_sibling_not_an_electronic_prerequisite() -> None:
    spec = get_workflow_recipe_spec(
        WorkflowRecipeIdentity(WORKFLOW_RECIPE_ADSORBATE_SCIENTIFIC_PREPARATION)
    )
    accepted_edges = {
        (edge.upstream_step_key, edge.downstream_step_key)
        for edge in spec.edges
        if edge.role == WORKFLOW_EDGE_ACCEPTED_STRUCTURE
    }

    assert ("relax", "frequency") in accepted_edges
    assert ("relax", "dos") in accepted_edges
    assert ("relax", "charge") in accepted_edges
    assert ("relax", "lobster") in accepted_edges
    assert all(edge.upstream_step_key != "frequency" for edge in spec.edges)


def test_canonical_plan_contract_accepts_parameters_hash_as_opaque_plan_identity() -> None:
    spec = get_workflow_recipe_spec(
        WorkflowRecipeIdentity(WORKFLOW_RECIPE_GAS_REFERENCE_PREPARATION)
    )
    without_parameters = _plan_for(spec)
    with_parameters = _plan_for(spec, parameters_hash="a" * 64)

    assert validate_workflow_plan_recipe_contract(without_parameters) is spec
    assert validate_workflow_plan_recipe_contract(with_parameters) is spec
    assert without_parameters.plan_hash != with_parameters.plan_hash


def test_plan_contract_rejects_unknown_recipe_version_and_graph_tampering() -> None:
    spec = get_workflow_recipe_spec(
        WorkflowRecipeIdentity(WORKFLOW_RECIPE_SLAB_SCIENTIFIC_PREPARATION)
    )
    unknown_version = ScientificWorkflowPlan(
        project_id=new_project_id(),
        workflow_recipe=WorkflowRecipeIdentity(spec.recipe_id, version="2"),
        root_structure_snapshot_id=new_structure_snapshot_id(),
        steps=spec.steps,
        edges=spec.edges,
    )
    with pytest.raises(WorkflowRecipeContractError, match="unknown workflow recipe"):
        validate_workflow_plan_recipe_contract(unknown_version)

    tampered_step = WorkflowStepSpec(
        key="static",
        calculation_type=CalculationType.STATIC,
        recipe_id=RECIPE_SLAB_RELAX,
    )
    tampered_steps = tuple(
        tampered_step if step.key == "static" else step for step in spec.steps
    )
    tampered_plan = ScientificWorkflowPlan(
        project_id=new_project_id(),
        workflow_recipe=spec.identity,
        root_structure_snapshot_id=new_structure_snapshot_id(),
        steps=tampered_steps,
        edges=spec.edges,
    )
    with pytest.raises(WorkflowRecipeContractError, match="steps do not match"):
        validate_workflow_plan_recipe_contract(tampered_plan)

    tampered_edges = tuple(
        replace(edge, role="other_role") if edge.downstream_step_key == "static" else edge
        for edge in spec.edges
    )
    tampered_edge_plan = ScientificWorkflowPlan(
        project_id=new_project_id(),
        workflow_recipe=spec.identity,
        root_structure_snapshot_id=new_structure_snapshot_id(),
        steps=spec.steps,
        edges=tampered_edges,
    )
    with pytest.raises(WorkflowRecipeContractError, match="edges do not match"):
        validate_workflow_plan_recipe_contract(tampered_edge_plan)


def test_recipe_spec_rejects_unknown_vasp_recipe_type_mismatch_and_cycles() -> None:
    with pytest.raises(WorkflowRecipeContractError, match="unknown VASP recipe"):
        WorkflowRecipeSpec(
            recipe_id="ECatVASP.Workflow.InvalidUnknownVasp",
            steps=(WorkflowStepSpec("static", CalculationType.STATIC, "unknown"),),
        )

    with pytest.raises(WorkflowRecipeContractError, match="CalculationType"):
        WorkflowRecipeSpec(
            recipe_id="ECatVASP.Workflow.InvalidType",
            steps=(
                WorkflowStepSpec(
                    "static",
                    CalculationType.RELAX,
                    RECIPE_GROUND_STATE_STATIC,
                ),
            ),
        )

    steps = (
        WorkflowStepSpec("relax", CalculationType.RELAX, RECIPE_SLAB_RELAX),
        WorkflowStepSpec("static", CalculationType.STATIC, RECIPE_GROUND_STATE_STATIC),
    )
    with pytest.raises(WorkflowRecipeContractError, match="form a DAG"):
        WorkflowRecipeSpec(
            recipe_id="ECatVASP.Workflow.InvalidCycle",
            steps=steps,
            edges=(
                WorkflowEdgeSpec("relax", "static"),
                WorkflowEdgeSpec("static", "relax"),
            ),
        )

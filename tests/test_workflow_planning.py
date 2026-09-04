from dataclasses import replace

import pytest

from ecatvasp.domain import WorkflowRecipeIdentity
from ecatvasp.domain.ids import new_project_id, new_structure_snapshot_id
from ecatvasp.workflow import (
    WORKFLOW_RECIPE_ADSORBATE_SCIENTIFIC_PREPARATION,
    WORKFLOW_RECIPE_GAS_REFERENCE_PREPARATION,
    WORKFLOW_RECIPE_SLAB_SCIENTIFIC_PREPARATION,
)
from ecatvasp.workflow.planning import (
    WORKFLOW_PLANNER_CONTRACT_VERSION,
    WorkflowPlanningError,
    WorkflowPlanningResult,
    plan_scientific_workflow,
)


def test_planning_is_deterministic_in_scientific_identity_not_storage_uuid() -> None:
    project_id = new_project_id()
    snapshot_id = new_structure_snapshot_id()
    recipe = WorkflowRecipeIdentity(WORKFLOW_RECIPE_SLAB_SCIENTIFIC_PREPARATION)

    first = plan_scientific_workflow(
        project_id=project_id,
        workflow_recipe=recipe,
        root_structure_snapshot_id=snapshot_id,
        parameters_hash="A" * 64,
    )
    second = plan_scientific_workflow(
        project_id=project_id,
        workflow_recipe=recipe,
        root_structure_snapshot_id=snapshot_id,
        parameters_hash="a" * 64,
    )

    assert WORKFLOW_PLANNER_CONTRACT_VERSION == "1"
    assert first.plan.id != second.plan.id
    assert first.plan.parameters_hash == "a" * 64
    assert second.plan.parameters_hash == "a" * 64
    assert first.plan.plan_hash == second.plan.plan_hash
    assert first.recipe_definition_hash == second.recipe_definition_hash
    assert first.topological_step_keys == second.topological_step_keys
    assert first.planning_hash == second.planning_hash


def test_adsorbate_planning_has_stable_lexical_topological_order() -> None:
    result = plan_scientific_workflow(
        project_id=new_project_id(),
        workflow_recipe=WorkflowRecipeIdentity(
            WORKFLOW_RECIPE_ADSORBATE_SCIENTIFIC_PREPARATION
        ),
        root_structure_snapshot_id=new_structure_snapshot_id(),
    )

    assert result.topological_step_keys == (
        "relax",
        "charge",
        "dos",
        "frequency",
        "lobster",
        "static",
    )
    assert all(
        edge.upstream_step_key == "relax"
        for edge in result.plan.edges
        if edge.downstream_step_key != "relax"
    )


def test_parameters_and_exact_root_snapshot_participate_in_planning_identity() -> None:
    project_id = new_project_id()
    recipe = WorkflowRecipeIdentity(WORKFLOW_RECIPE_GAS_REFERENCE_PREPARATION)
    root_a = new_structure_snapshot_id()
    root_b = new_structure_snapshot_id()

    base = plan_scientific_workflow(
        project_id=project_id,
        workflow_recipe=recipe,
        root_structure_snapshot_id=root_a,
    )
    with_parameters = plan_scientific_workflow(
        project_id=project_id,
        workflow_recipe=recipe,
        root_structure_snapshot_id=root_a,
        parameters_hash="b" * 64,
    )
    other_root = plan_scientific_workflow(
        project_id=project_id,
        workflow_recipe=recipe,
        root_structure_snapshot_id=root_b,
    )

    assert base.plan.plan_hash != with_parameters.plan.plan_hash
    assert base.planning_hash != with_parameters.planning_hash
    assert base.plan.plan_hash != other_root.plan.plan_hash
    assert base.planning_hash != other_root.planning_hash


def test_unknown_recipe_and_invalid_parameter_hash_fail_closed() -> None:
    with pytest.raises(WorkflowPlanningError, match="unknown workflow recipe"):
        plan_scientific_workflow(
            project_id=new_project_id(),
            workflow_recipe=WorkflowRecipeIdentity("ECatVASP.Workflow.Unknown"),
            root_structure_snapshot_id=new_structure_snapshot_id(),
        )

    with pytest.raises(WorkflowPlanningError, match="parameters_hash"):
        plan_scientific_workflow(
            project_id=new_project_id(),
            workflow_recipe=WorkflowRecipeIdentity(
                WORKFLOW_RECIPE_SLAB_SCIENTIFIC_PREPARATION
            ),
            root_structure_snapshot_id=new_structure_snapshot_id(),
            parameters_hash="not-a-hash",
        )


def test_planning_result_rejects_recipe_hash_or_order_tampering() -> None:
    result = plan_scientific_workflow(
        project_id=new_project_id(),
        workflow_recipe=WorkflowRecipeIdentity(
            WORKFLOW_RECIPE_SLAB_SCIENTIFIC_PREPARATION
        ),
        root_structure_snapshot_id=new_structure_snapshot_id(),
    )

    with pytest.raises(WorkflowPlanningError, match="recipe_definition_hash"):
        replace(result, recipe_definition_hash="f" * 64)

    with pytest.raises(WorkflowPlanningError, match="topological_step_keys"):
        WorkflowPlanningResult(
            plan=result.plan,
            recipe_definition_hash=result.recipe_definition_hash,
            topological_step_keys=tuple(reversed(result.topological_step_keys)),
        )

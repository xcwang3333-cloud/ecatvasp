"""Pure deterministic planning for canonical v0.6 scientific workflows."""

from __future__ import annotations

import heapq
from collections import defaultdict
from dataclasses import dataclass, field

from ecatvasp.domain import (
    ProjectId,
    ScientificWorkflowPlan,
    StructureSnapshotId,
    WorkflowEdgeSpec,
    WorkflowRecipeIdentity,
    WorkflowStepSpec,
    canonical_sha256,
)
from ecatvasp.workflow.recipes import (
    WorkflowRecipeContractError,
    get_workflow_recipe_spec,
    validate_workflow_plan_recipe_contract,
)

WORKFLOW_PLANNER_CONTRACT_VERSION = "1"


class WorkflowPlanningError(ValueError):
    """Raised when canonical workflow planning cannot be completed safely."""


def _validate_sha256(value: str, field_name: str) -> str:
    normalized = value.lower()
    valid_hex = all(character in "0123456789abcdef" for character in normalized)
    if len(normalized) != 64 or not valid_hex:
        raise WorkflowPlanningError(
            f"{field_name} must be a 64-character hexadecimal SHA-256 digest"
        )
    return normalized


def _topological_step_keys(
    *,
    steps: tuple[WorkflowStepSpec, ...],
    edges: tuple[WorkflowEdgeSpec, ...],
) -> tuple[str, ...]:
    """Return deterministic DAG order with lexical tie-breaking among ready steps."""

    step_keys = tuple(step.key for step in steps)
    known = set(step_keys)
    if len(known) != len(step_keys):
        raise WorkflowPlanningError("workflow planning requires unique step keys")

    adjacency: dict[str, set[str]] = defaultdict(set)
    indegree = {key: 0 for key in step_keys}
    for edge in edges:
        if edge.upstream_step_key not in known or edge.downstream_step_key not in known:
            raise WorkflowPlanningError(
                "workflow planning edges must reference steps in the same plan"
            )
        downstream = adjacency[edge.upstream_step_key]
        if edge.downstream_step_key not in downstream:
            downstream.add(edge.downstream_step_key)
            indegree[edge.downstream_step_key] += 1

    ready = [key for key, degree in indegree.items() if degree == 0]
    heapq.heapify(ready)
    ordered: list[str] = []
    while ready:
        key = heapq.heappop(ready)
        ordered.append(key)
        for downstream in sorted(adjacency[key]):
            indegree[downstream] -= 1
            if indegree[downstream] == 0:
                heapq.heappush(ready, downstream)

    if len(ordered) != len(step_keys):
        raise WorkflowPlanningError("workflow planning graph must be acyclic")
    return tuple(ordered)


@dataclass(frozen=True, slots=True)
class WorkflowPlanningResult:
    """Pure planning result above persistence materialization and execution."""

    plan: ScientificWorkflowPlan
    recipe_definition_hash: str
    topological_step_keys: tuple[str, ...]
    planning_hash: str = field(init=False)

    def __post_init__(self) -> None:
        recipe_definition_hash = _validate_sha256(
            self.recipe_definition_hash,
            "recipe_definition_hash",
        )
        try:
            spec = validate_workflow_plan_recipe_contract(self.plan)
        except WorkflowRecipeContractError as error:
            raise WorkflowPlanningError(str(error)) from error
        if spec.definition_hash != recipe_definition_hash:
            raise WorkflowPlanningError(
                "recipe_definition_hash does not match the canonical workflow recipe"
            )

        expected_order = _topological_step_keys(
            steps=self.plan.steps,
            edges=self.plan.edges,
        )
        if self.topological_step_keys != expected_order:
            raise WorkflowPlanningError(
                "topological_step_keys do not match deterministic workflow order"
            )

        object.__setattr__(self, "recipe_definition_hash", recipe_definition_hash)
        object.__setattr__(
            self,
            "planning_hash",
            canonical_sha256(
                {
                    "planner_contract_version": WORKFLOW_PLANNER_CONTRACT_VERSION,
                    "plan_hash": self.plan.plan_hash,
                    "recipe_definition_hash": recipe_definition_hash,
                    "topological_step_keys": self.topological_step_keys,
                }
            ),
        )


def plan_scientific_workflow(
    *,
    project_id: ProjectId,
    workflow_recipe: WorkflowRecipeIdentity,
    root_structure_snapshot_id: StructureSnapshotId,
    parameters_hash: str | None = None,
) -> WorkflowPlanningResult:
    """Build canonical immutable workflow intent without materializing any step.

    The generated ``ScientificWorkflowPlan.id`` remains a persistence UUID and may differ across
    repeated calls. ``plan_hash`` and ``planning_hash`` are the deterministic scientific planning
    identities for identical inputs.
    """

    try:
        spec = get_workflow_recipe_spec(workflow_recipe)
    except WorkflowRecipeContractError as error:
        raise WorkflowPlanningError(str(error)) from error

    normalized_parameters_hash = (
        None
        if parameters_hash is None
        else _validate_sha256(parameters_hash, "parameters_hash")
    )
    plan = ScientificWorkflowPlan(
        project_id=project_id,
        workflow_recipe=spec.identity,
        root_structure_snapshot_id=root_structure_snapshot_id,
        steps=spec.steps,
        edges=spec.edges,
        parameters_hash=normalized_parameters_hash,
    )
    try:
        validate_workflow_plan_recipe_contract(plan)
    except WorkflowRecipeContractError as error:
        raise WorkflowPlanningError(str(error)) from error

    return WorkflowPlanningResult(
        plan=plan,
        recipe_definition_hash=spec.definition_hash,
        topological_step_keys=_topological_step_keys(
            steps=plan.steps,
            edges=plan.edges,
        ),
    )

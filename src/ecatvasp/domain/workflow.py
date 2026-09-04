"""Persistent scientific-workflow identity contracts for v0.6."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field

from ecatvasp.domain.calculation import CalculationType
from ecatvasp.domain.ids import (
    CalculationId,
    ProjectId,
    StructureSnapshotId,
    WorkflowPlanId,
    WorkflowStepBindingId,
    new_workflow_plan_id,
    new_workflow_step_binding_id,
)
from ecatvasp.domain.method import canonical_sha256


def _require_text(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank")


def _validate_sha256(value: str | None, field_name: str) -> None:
    if value is None:
        return
    normalized = value.lower()
    if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
        raise ValueError(f"{field_name} must be a 64-character hexadecimal SHA-256 digest")


@dataclass(frozen=True, slots=True)
class WorkflowRecipeIdentity:
    """Stable workflow-level recipe identity, distinct from a VASP Calculation recipe."""

    recipe_id: str
    version: str = "1"

    def __post_init__(self) -> None:
        _require_text(self.recipe_id, "recipe_id")
        _require_text(self.version, "version")


@dataclass(frozen=True, slots=True)
class WorkflowStepSpec:
    """One logical workflow step that will later materialize one Calculation identity."""

    key: str
    calculation_type: CalculationType
    recipe_id: str

    def __post_init__(self) -> None:
        _require_text(self.key, "key")
        _require_text(self.recipe_id, "recipe_id")


@dataclass(frozen=True, slots=True)
class WorkflowEdgeSpec:
    """Logical ordering edge between workflow steps; not a scheduler or provenance edge."""

    upstream_step_key: str
    downstream_step_key: str
    role: str = "requires"

    def __post_init__(self) -> None:
        _require_text(self.upstream_step_key, "upstream_step_key")
        _require_text(self.downstream_step_key, "downstream_step_key")
        _require_text(self.role, "role")
        if self.upstream_step_key == self.downstream_step_key:
            raise ValueError("a workflow edge cannot reference the same step on both sides")


@dataclass(frozen=True, slots=True)
class ScientificWorkflowPlan:
    """Immutable persisted DAG intent for a multi-Calculation scientific workflow."""

    project_id: ProjectId
    workflow_recipe: WorkflowRecipeIdentity
    root_structure_snapshot_id: StructureSnapshotId
    steps: tuple[WorkflowStepSpec, ...]
    edges: tuple[WorkflowEdgeSpec, ...] = ()
    id: WorkflowPlanId = field(default_factory=new_workflow_plan_id)
    parameters_hash: str | None = None

    def __post_init__(self) -> None:
        if not self.steps:
            raise ValueError("a ScientificWorkflowPlan requires at least one step")
        _validate_sha256(self.parameters_hash, "parameters_hash")

        ordered_steps = tuple(sorted(self.steps, key=lambda item: item.key))
        step_keys = tuple(item.key for item in ordered_steps)
        if len(step_keys) != len(set(step_keys)):
            raise ValueError("workflow step keys must be unique")

        ordered_edges = tuple(
            sorted(
                self.edges,
                key=lambda item: (
                    item.upstream_step_key,
                    item.downstream_step_key,
                    item.role,
                ),
            )
        )
        edge_keys = tuple(
            (item.upstream_step_key, item.downstream_step_key, item.role)
            for item in ordered_edges
        )
        if len(edge_keys) != len(set(edge_keys)):
            raise ValueError("duplicate workflow edge semantics are not allowed")
        known = set(step_keys)
        if any(
            edge.upstream_step_key not in known or edge.downstream_step_key not in known
            for edge in ordered_edges
        ):
            raise ValueError("workflow edges must reference steps in the same plan")

        object.__setattr__(self, "steps", ordered_steps)
        object.__setattr__(self, "edges", ordered_edges)
        _validate_acyclic(step_keys=step_keys, edges=ordered_edges)

    @property
    def plan_hash(self) -> str:
        """Return deterministic workflow intent identity independent from object UUID."""

        return canonical_sha256(
            {
                "project_id": self.project_id,
                "workflow_recipe": self.workflow_recipe,
                "root_structure_snapshot_id": self.root_structure_snapshot_id,
                "steps": self.steps,
                "edges": self.edges,
                "parameters_hash": self.parameters_hash,
            }
        )

    def step(self, key: str) -> WorkflowStepSpec:
        """Resolve one step by stable key or fail closed."""

        for step in self.steps:
            if step.key == key:
                return step
        raise KeyError(key)


@dataclass(frozen=True, slots=True)
class WorkflowStepBinding:
    """Persisted binding from one logical workflow step generation to one Calculation."""

    workflow_plan_id: WorkflowPlanId
    step_key: str
    generation: int
    calculation_id: CalculationId
    resolved_input_structure_snapshot_id: StructureSnapshotId
    materialization_reason: str
    id: WorkflowStepBindingId = field(default_factory=new_workflow_step_binding_id)
    supersedes_binding_id: WorkflowStepBindingId | None = None

    def __post_init__(self) -> None:
        _require_text(self.step_key, "step_key")
        _require_text(self.materialization_reason, "materialization_reason")
        if self.generation < 1:
            raise ValueError("workflow binding generation must be positive")
        if self.generation == 1 and self.supersedes_binding_id is not None:
            raise ValueError("generation 1 workflow binding cannot supersede another binding")
        if self.generation > 1 and self.supersedes_binding_id is None:
            raise ValueError("workflow binding generation > 1 requires supersedes_binding_id")
        if self.supersedes_binding_id == self.id:
            raise ValueError("a WorkflowStepBinding cannot supersede itself")

    @property
    def binding_hash(self) -> str:
        """Return deterministic resolved-step identity used by later idempotent reconciliation."""

        return canonical_sha256(
            {
                "workflow_plan_id": self.workflow_plan_id,
                "step_key": self.step_key,
                "generation": self.generation,
                "calculation_id": self.calculation_id,
                "resolved_input_structure_snapshot_id": self.resolved_input_structure_snapshot_id,
                "supersedes_binding_id": self.supersedes_binding_id,
            }
        )


def _validate_acyclic(*, step_keys: tuple[str, ...], edges: tuple[WorkflowEdgeSpec, ...]) -> None:
    adjacency: dict[str, set[str]] = defaultdict(set)
    indegree = {key: 0 for key in step_keys}
    for edge in edges:
        if edge.downstream_step_key not in adjacency[edge.upstream_step_key]:
            adjacency[edge.upstream_step_key].add(edge.downstream_step_key)
            indegree[edge.downstream_step_key] += 1

    queue = deque(sorted(key for key, degree in indegree.items() if degree == 0))
    visited = 0
    while queue:
        key = queue.popleft()
        visited += 1
        for downstream in sorted(adjacency[key]):
            indegree[downstream] -= 1
            if indegree[downstream] == 0:
                queue.append(downstream)
    if visited != len(step_keys):
        raise ValueError("scientific workflow steps and edges must form a DAG")

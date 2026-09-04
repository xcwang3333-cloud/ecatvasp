"""Canonical scientific workflow recipe registry for v0.6."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from ecatvasp.domain import (
    CalculationType,
    ScientificWorkflowPlan,
    WorkflowEdgeSpec,
    WorkflowRecipeIdentity,
    WorkflowStepSpec,
    canonical_sha256,
)
from ecatvasp.vasp.recipes import (
    RECIPE_ADSORBATE_RELAX,
    RECIPE_CHARGE_DENSITY_STATIC,
    RECIPE_DOS_PREREQUISITE,
    RECIPE_GAS_FREQUENCY,
    RECIPE_GAS_RELAX,
    RECIPE_GROUND_STATE_STATIC,
    RECIPE_LOBSTER_PREREQUISITE,
    RECIPE_SELECTED_ATOM_FREQUENCY,
    RECIPE_SLAB_RELAX,
    VaspRecipeContractError,
    get_vasp_recipe_spec,
)

WORKFLOW_RECIPE_SLAB_SCIENTIFIC_PREPARATION = (
    "ECatVASP.Workflow.SlabScientificPreparation"
)
WORKFLOW_RECIPE_ADSORBATE_SCIENTIFIC_PREPARATION = (
    "ECatVASP.Workflow.AdsorbateScientificPreparation"
)
WORKFLOW_RECIPE_GAS_REFERENCE_PREPARATION = "ECatVASP.Workflow.GasReferencePreparation"

WORKFLOW_EDGE_ACCEPTED_STRUCTURE = "accepted_structure"


class WorkflowRecipeContractError(ValueError):
    """Raised when a workflow recipe or plan violates the canonical registry contract."""


def _validate_vasp_recipe_contracts(steps: tuple[WorkflowStepSpec, ...]) -> None:
    for step in steps:
        try:
            vasp_spec = get_vasp_recipe_spec(step.recipe_id)
        except VaspRecipeContractError as error:
            raise WorkflowRecipeContractError(
                f"workflow step {step.key} references an unknown VASP recipe"
            ) from error
        if step.calculation_type is not vasp_spec.calculation_type:
            raise WorkflowRecipeContractError(
                f"workflow step {step.key} CalculationType does not match its VASP recipe"
            )


def _validate_graph(
    *,
    steps: tuple[WorkflowStepSpec, ...],
    edges: tuple[WorkflowEdgeSpec, ...],
) -> None:
    step_keys = tuple(step.key for step in steps)
    if len(step_keys) != len(set(step_keys)):
        raise WorkflowRecipeContractError("workflow recipe step keys must be unique")

    edge_keys = tuple(
        (edge.upstream_step_key, edge.downstream_step_key, edge.role)
        for edge in edges
    )
    if len(edge_keys) != len(set(edge_keys)):
        raise WorkflowRecipeContractError(
            "duplicate workflow recipe edge semantics are not allowed"
        )

    known = set(step_keys)
    for edge in edges:
        if edge.upstream_step_key not in known or edge.downstream_step_key not in known:
            raise WorkflowRecipeContractError(
                "workflow recipe edges must reference steps in the same recipe"
            )

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
        raise WorkflowRecipeContractError("workflow recipe steps and edges must form a DAG")


@dataclass(frozen=True, slots=True)
class WorkflowRecipeSpec:
    """Source-defined canonical composition of existing VASP calculation recipes."""

    recipe_id: str
    steps: tuple[WorkflowStepSpec, ...]
    edges: tuple[WorkflowEdgeSpec, ...] = ()
    version: str = "1"
    description: str | None = None

    def __post_init__(self) -> None:
        if not self.recipe_id.strip():
            raise ValueError("recipe_id must not be blank")
        if not self.version.strip():
            raise ValueError("version must not be blank")
        if not self.steps:
            raise ValueError("a WorkflowRecipeSpec requires at least one step")

        ordered_steps = tuple(sorted(self.steps, key=lambda item: item.key))
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
        _validate_graph(steps=ordered_steps, edges=ordered_edges)
        _validate_vasp_recipe_contracts(ordered_steps)
        object.__setattr__(self, "steps", ordered_steps)
        object.__setattr__(self, "edges", ordered_edges)

    @property
    def identity(self) -> WorkflowRecipeIdentity:
        """Return the stable workflow-level recipe identity."""

        return WorkflowRecipeIdentity(recipe_id=self.recipe_id, version=self.version)

    @property
    def definition_hash(self) -> str:
        """Return a deterministic registry fingerprint including VASP recipe versions."""

        referenced_vasp_recipes = tuple(
            (step.key, get_vasp_recipe_spec(step.recipe_id).identity)
            for step in self.steps
        )
        return canonical_sha256(
            {
                "identity": self.identity,
                "steps": self.steps,
                "edges": self.edges,
                "referenced_vasp_recipes": referenced_vasp_recipes,
            }
        )


def _step(
    key: str,
    calculation_type: CalculationType,
    recipe_id: str,
) -> WorkflowStepSpec:
    return WorkflowStepSpec(
        key=key,
        calculation_type=calculation_type,
        recipe_id=recipe_id,
    )


def _accepted_structure(upstream: str, downstream: str) -> WorkflowEdgeSpec:
    return WorkflowEdgeSpec(
        upstream_step_key=upstream,
        downstream_step_key=downstream,
        role=WORKFLOW_EDGE_ACCEPTED_STRUCTURE,
    )


WORKFLOW_RECIPE_SPECS: tuple[WorkflowRecipeSpec, ...] = (
    WorkflowRecipeSpec(
        recipe_id=WORKFLOW_RECIPE_SLAB_SCIENTIFIC_PREPARATION,
        description=(
            "Relax a slab, then fan out the accepted structure to ground-state and "
            "electronic-structure prerequisite calculations."
        ),
        steps=(
            _step("relax", CalculationType.RELAX, RECIPE_SLAB_RELAX),
            _step("static", CalculationType.STATIC, RECIPE_GROUND_STATE_STATIC),
            _step("dos", CalculationType.DOS_STATIC, RECIPE_DOS_PREREQUISITE),
            _step(
                "charge",
                CalculationType.CHARGE_STATIC,
                RECIPE_CHARGE_DENSITY_STATIC,
            ),
            _step(
                "lobster",
                CalculationType.LOBSTER_PREREQUISITE,
                RECIPE_LOBSTER_PREREQUISITE,
            ),
        ),
        edges=(
            _accepted_structure("relax", "static"),
            _accepted_structure("relax", "dos"),
            _accepted_structure("relax", "charge"),
            _accepted_structure("relax", "lobster"),
        ),
    ),
    WorkflowRecipeSpec(
        recipe_id=WORKFLOW_RECIPE_ADSORBATE_SCIENTIFIC_PREPARATION,
        description=(
            "Relax an adsorbate structure, then fan out the accepted geometry to static, "
            "selected-atom frequency, and electronic-structure prerequisite calculations."
        ),
        steps=(
            _step("relax", CalculationType.RELAX, RECIPE_ADSORBATE_RELAX),
            _step("static", CalculationType.STATIC, RECIPE_GROUND_STATE_STATIC),
            _step(
                "frequency",
                CalculationType.FREQUENCY,
                RECIPE_SELECTED_ATOM_FREQUENCY,
            ),
            _step("dos", CalculationType.DOS_STATIC, RECIPE_DOS_PREREQUISITE),
            _step(
                "charge",
                CalculationType.CHARGE_STATIC,
                RECIPE_CHARGE_DENSITY_STATIC,
            ),
            _step(
                "lobster",
                CalculationType.LOBSTER_PREREQUISITE,
                RECIPE_LOBSTER_PREREQUISITE,
            ),
        ),
        edges=(
            _accepted_structure("relax", "static"),
            _accepted_structure("relax", "frequency"),
            _accepted_structure("relax", "dos"),
            _accepted_structure("relax", "charge"),
            _accepted_structure("relax", "lobster"),
        ),
    ),
    WorkflowRecipeSpec(
        recipe_id=WORKFLOW_RECIPE_GAS_REFERENCE_PREPARATION,
        description=(
            "Relax an isolated gas reference and fan out its accepted geometry to static "
            "and gas-frequency calculations."
        ),
        steps=(
            _step("relax", CalculationType.GAS_RELAX, RECIPE_GAS_RELAX),
            _step("static", CalculationType.STATIC, RECIPE_GROUND_STATE_STATIC),
            _step("frequency", CalculationType.GAS_FREQUENCY, RECIPE_GAS_FREQUENCY),
        ),
        edges=(
            _accepted_structure("relax", "static"),
            _accepted_structure("relax", "frequency"),
        ),
    ),
)

WORKFLOW_RECIPE_REGISTRY: Mapping[
    WorkflowRecipeIdentity, WorkflowRecipeSpec
] = MappingProxyType({spec.identity: spec for spec in WORKFLOW_RECIPE_SPECS})

if len(WORKFLOW_RECIPE_REGISTRY) != len(WORKFLOW_RECIPE_SPECS):
    raise RuntimeError("workflow recipe identities must be unique")


def list_workflow_recipe_specs() -> tuple[WorkflowRecipeSpec, ...]:
    """Return canonical workflow recipes in stable source-defined order."""

    return WORKFLOW_RECIPE_SPECS


def get_workflow_recipe_spec(identity: WorkflowRecipeIdentity) -> WorkflowRecipeSpec:
    """Resolve one canonical workflow recipe identity or fail closed."""

    try:
        return WORKFLOW_RECIPE_REGISTRY[identity]
    except KeyError as error:
        raise WorkflowRecipeContractError(
            f"unknown workflow recipe: {identity.recipe_id}@{identity.version}"
        ) from error


def validate_workflow_plan_recipe_contract(
    plan: ScientificWorkflowPlan,
) -> WorkflowRecipeSpec:
    """Require a persisted plan to match its source-defined recipe graph exactly."""

    spec = get_workflow_recipe_spec(plan.workflow_recipe)
    if plan.steps != spec.steps:
        raise WorkflowRecipeContractError(
            "ScientificWorkflowPlan steps do not match the canonical workflow recipe"
        )
    if plan.edges != spec.edges:
        raise WorkflowRecipeContractError(
            "ScientificWorkflowPlan edges do not match the canonical workflow recipe"
        )
    return spec

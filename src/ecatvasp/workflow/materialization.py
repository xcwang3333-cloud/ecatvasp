"""Explicit v0.6 workflow-step materialization and structure binding."""

from __future__ import annotations

from dataclasses import dataclass

from ecatvasp.domain import (
    Calculation,
    CalculationType,
    MethodFingerprint,
    ScientificWorkflowPlan,
    StructureOrigin,
    StructureSnapshot,
    WorkflowStepBinding,
)
from ecatvasp.domain.ids import WorkflowStepBindingId
from ecatvasp.vasp.contracts import ProjectNumericalLock, VaspSystemContext
from ecatvasp.vasp.recipes import (
    VaspRecipeContractError,
    get_vasp_recipe_spec,
    validate_calculation_recipe_contract,
)
from ecatvasp.vasp.results import ConvergenceVerdict
from ecatvasp.vasp.structure_promotion import VaspStructurePromotionResult
from ecatvasp.workflow.recipes import (
    WORKFLOW_EDGE_ACCEPTED_STRUCTURE,
    WorkflowRecipeContractError,
    validate_workflow_plan_recipe_contract,
)

WORKFLOW_ROOT_STRUCTURE_REASON = "workflow root structure"
WORKFLOW_ACCEPTED_STRUCTURE_REASON = "accepted structure from workflow step"

_RELAX_TYPES = frozenset({CalculationType.RELAX, CalculationType.GAS_RELAX})


class WorkflowMaterializationError(ValueError):
    """Raised when a workflow step cannot be materialized without guessing."""


@dataclass(frozen=True, slots=True)
class AcceptedStructureSource:
    """Explicit v0.5-accepted upstream structure consumed by one downstream step."""

    upstream_binding: WorkflowStepBinding
    upstream_calculation: Calculation
    promotion: VaspStructurePromotionResult

    def __post_init__(self) -> None:
        calculation = self.upstream_calculation
        binding = self.upstream_binding
        promotion = self.promotion
        if binding.calculation_id != calculation.id:
            raise WorkflowMaterializationError(
                "accepted-structure binding does not reference the upstream Calculation"
            )
        if binding.resolved_input_structure_snapshot_id != calculation.input_structure_snapshot_id:
            raise WorkflowMaterializationError(
                "accepted-structure binding input does not match the upstream Calculation"
            )
        if calculation.calculation_type not in _RELAX_TYPES:
            raise WorkflowMaterializationError(
                "accepted-structure source requires a relaxation Calculation"
            )
        if promotion.convergence.calculation_type is not calculation.calculation_type:
            raise WorkflowMaterializationError(
                "accepted-structure convergence type does not match upstream Calculation"
            )
        if promotion.convergence.overall is not ConvergenceVerdict.CONVERGED:
            raise WorkflowMaterializationError(
                "accepted-structure source requires a scientifically converged promotion"
            )
        if promotion.snapshot.origin is not StructureOrigin.RELAXED:
            raise WorkflowMaterializationError(
                "accepted-structure snapshot must have RELAXED origin"
            )
        if promotion.snapshot.parent_snapshot_id != calculation.input_structure_snapshot_id:
            raise WorkflowMaterializationError(
                "accepted-structure snapshot is not a direct child of the upstream input"
            )
        if promotion.updated_variant.current_structure_snapshot_id != promotion.snapshot.id:
            raise WorkflowMaterializationError(
                "accepted-structure promotion does not point at its promoted snapshot"
            )


@dataclass(frozen=True, slots=True)
class WorkflowStepMaterialization:
    """One newly created Calculation plus its exact workflow binding."""

    calculation: Calculation
    binding: WorkflowStepBinding
    resolved_input_snapshot: StructureSnapshot
    source_binding_id: WorkflowStepBindingId | None = None

    def __post_init__(self) -> None:
        if self.binding.calculation_id != self.calculation.id:
            raise WorkflowMaterializationError(
                "materialization binding does not reference its Calculation"
            )
        if (
            self.binding.resolved_input_structure_snapshot_id
            != self.resolved_input_snapshot.id
            or self.calculation.input_structure_snapshot_id != self.resolved_input_snapshot.id
        ):
            raise WorkflowMaterializationError(
                "materialization Calculation/binding must use the resolved snapshot"
            )


def materialize_workflow_step(
    *,
    plan: ScientificWorkflowPlan,
    step_key: str,
    fingerprint: MethodFingerprint,
    system_context: VaspSystemContext,
    project_lock: ProjectNumericalLock | None,
    root_snapshot: StructureSnapshot | None = None,
    accepted_structure_source: AcceptedStructureSource | None = None,
    previous_binding: WorkflowStepBinding | None = None,
    materialization_reason: str | None = None,
) -> WorkflowStepMaterialization:
    """Create one Calculation and WorkflowStepBinding from explicit workflow inputs.

    This function never reads ProjectStore, discovers a current structure, promotes CONTCAR,
    reuses an existing Calculation, or starts execution. Re-materialization is explicit through
    ``previous_binding`` and always creates a new Calculation/binding generation.
    """

    try:
        validate_workflow_plan_recipe_contract(plan)
    except WorkflowRecipeContractError as error:
        raise WorkflowMaterializationError(str(error)) from error

    try:
        step = plan.step(step_key)
    except KeyError as error:
        raise WorkflowMaterializationError(f"unknown workflow step: {step_key}") from error

    incoming_edges = tuple(
        edge for edge in plan.edges if edge.downstream_step_key == step_key
    )
    if not incoming_edges:
        resolved_snapshot, source_binding_id, default_reason = _resolve_root_input(
            plan=plan,
            root_snapshot=root_snapshot,
            accepted_structure_source=accepted_structure_source,
        )
    else:
        resolved_snapshot, source_binding_id, default_reason = _resolve_downstream_input(
            plan=plan,
            step_key=step_key,
            incoming_edges=incoming_edges,
            root_snapshot=root_snapshot,
            accepted_structure_source=accepted_structure_source,
        )

    vasp_spec = get_vasp_recipe_spec(step.recipe_id)
    if vasp_spec.calculation_type is not step.calculation_type:
        raise WorkflowMaterializationError(
            "workflow step CalculationType does not match canonical VASP recipe"
        )
    if fingerprint.recipe.recipe_id != step.recipe_id:
        raise WorkflowMaterializationError(
            "MethodFingerprint recipe does not match workflow step recipe"
        )
    if fingerprint.recipe.version != vasp_spec.version:
        raise WorkflowMaterializationError(
            "MethodFingerprint recipe version is not canonical for workflow step"
        )

    generation, supersedes_binding_id = _resolve_generation(
        plan=plan,
        step_key=step_key,
        previous_binding=previous_binding,
    )
    calculation = Calculation(
        project_id=plan.project_id,
        calculation_type=step.calculation_type,
        input_structure_snapshot_id=resolved_snapshot.id,
        recipe_id=step.recipe_id,
        method_fingerprint_id=fingerprint.id,
    )
    try:
        validate_calculation_recipe_contract(
            calculation=calculation,
            system_context=system_context,
            project_lock=project_lock,
        )
    except VaspRecipeContractError as error:
        raise WorkflowMaterializationError(str(error)) from error

    reason = default_reason if materialization_reason is None else materialization_reason
    if not reason.strip():
        raise WorkflowMaterializationError("materialization_reason must not be blank")
    binding = WorkflowStepBinding(
        workflow_plan_id=plan.id,
        step_key=step_key,
        generation=generation,
        calculation_id=calculation.id,
        resolved_input_structure_snapshot_id=resolved_snapshot.id,
        materialization_reason=reason,
        supersedes_binding_id=supersedes_binding_id,
    )
    return WorkflowStepMaterialization(
        calculation=calculation,
        binding=binding,
        resolved_input_snapshot=resolved_snapshot,
        source_binding_id=source_binding_id,
    )


def _resolve_root_input(
    *,
    plan: ScientificWorkflowPlan,
    root_snapshot: StructureSnapshot | None,
    accepted_structure_source: AcceptedStructureSource | None,
) -> tuple[StructureSnapshot, WorkflowStepBindingId | None, str]:
    if accepted_structure_source is not None:
        raise WorkflowMaterializationError(
            "root workflow step cannot consume an accepted-structure source"
        )
    if root_snapshot is None:
        raise WorkflowMaterializationError("root workflow step requires root_snapshot")
    if root_snapshot.id != plan.root_structure_snapshot_id:
        raise WorkflowMaterializationError(
            "root_snapshot does not match the exact workflow plan root snapshot"
        )
    return root_snapshot, None, WORKFLOW_ROOT_STRUCTURE_REASON


def _resolve_downstream_input(
    *,
    plan: ScientificWorkflowPlan,
    step_key: str,
    incoming_edges: tuple[object, ...],
    root_snapshot: StructureSnapshot | None,
    accepted_structure_source: AcceptedStructureSource | None,
) -> tuple[StructureSnapshot, WorkflowStepBindingId, str]:
    if root_snapshot is not None:
        raise WorkflowMaterializationError(
            "downstream workflow step cannot bypass its logical edge with root_snapshot"
        )
    if len(incoming_edges) != 1:
        raise WorkflowMaterializationError(
            "Block 4 supports exactly one incoming edge per materialized downstream step"
        )
    edge = incoming_edges[0]
    role = getattr(edge, "role", None)
    upstream_step_key = getattr(edge, "upstream_step_key", None)
    if role != WORKFLOW_EDGE_ACCEPTED_STRUCTURE or not isinstance(upstream_step_key, str):
        raise WorkflowMaterializationError(
            "Block 4 supports only accepted_structure downstream edges"
        )
    if accepted_structure_source is None:
        raise WorkflowMaterializationError(
            "accepted_structure workflow edge requires an explicit accepted source"
        )

    source = accepted_structure_source
    binding = source.upstream_binding
    calculation = source.upstream_calculation
    if binding.workflow_plan_id != plan.id:
        raise WorkflowMaterializationError(
            "accepted-structure binding belongs to another workflow plan"
        )
    if binding.step_key != upstream_step_key:
        raise WorkflowMaterializationError(
            "accepted-structure binding does not belong to the required upstream step"
        )
    upstream_step = plan.step(upstream_step_key)
    if calculation.project_id != plan.project_id:
        raise WorkflowMaterializationError(
            "accepted-structure Calculation belongs to another Project"
        )
    if calculation.calculation_type is not upstream_step.calculation_type:
        raise WorkflowMaterializationError(
            "accepted-structure CalculationType does not match upstream workflow step"
        )
    if calculation.recipe_id != upstream_step.recipe_id:
        raise WorkflowMaterializationError(
            "accepted-structure Calculation recipe does not match upstream workflow step"
        )

    reason = f"{WORKFLOW_ACCEPTED_STRUCTURE_REASON} {upstream_step_key}"
    return source.promotion.snapshot, binding.id, reason


def _resolve_generation(
    *,
    plan: ScientificWorkflowPlan,
    step_key: str,
    previous_binding: WorkflowStepBinding | None,
) -> tuple[int, WorkflowStepBindingId | None]:
    if previous_binding is None:
        return 1, None
    if previous_binding.workflow_plan_id != plan.id:
        raise WorkflowMaterializationError(
            "previous_binding belongs to another workflow plan"
        )
    if previous_binding.step_key != step_key:
        raise WorkflowMaterializationError(
            "previous_binding belongs to another workflow step"
        )
    return previous_binding.generation + 1, previous_binding.id

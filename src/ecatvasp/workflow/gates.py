"""Scientific gates, freshness, and binding supersession for v0.6 Block 5."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from ecatvasp.domain import (
    Calculation,
    CalculationScientificStatus,
    ScientificWorkflowPlan,
    WorkflowStepBinding,
)
from ecatvasp.domain.ids import (
    CalculationId,
    StructureSnapshotId,
    WorkflowPlanId,
    WorkflowStepBindingId,
)
from ecatvasp.provenance import (
    DependencyRecord,
    FreshnessEngine,
    FreshnessResult,
    FreshnessState,
    ProvenanceIntegrityError,
)
from ecatvasp.workflow.materialization import AcceptedStructureSource
from ecatvasp.workflow.recipes import (
    WORKFLOW_EDGE_ACCEPTED_STRUCTURE,
    WorkflowRecipeContractError,
    validate_workflow_plan_recipe_contract,
)


class WorkflowGateError(ValueError):
    """Raised when workflow scientific readiness cannot be decided exactly."""


class WorkflowStepScientificState(StrEnum):
    """Derived scientific state of the current binding generation for one workflow step."""

    UNMATERIALIZED = "unmaterialized"
    IN_PROGRESS = "in_progress"
    PASSED = "passed"
    BLOCKED = "blocked"
    STALE = "stale"
    INVALID = "invalid"
    SUPERSEDED = "superseded"


class WorkflowStepReadiness(StrEnum):
    """Whether orchestration may materialize or continue one logical workflow step."""

    READY = "ready"
    WAITING = "waiting"
    BLOCKED = "blocked"
    SATISFIED = "satisfied"


class WorkflowEdgeGateVerdict(StrEnum):
    """Scientific gate verdict for one logical workflow edge."""

    OPEN = "open"
    WAITING = "waiting"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class WorkflowBindingSelection:
    """Current append-only binding generation plus its superseded history for one step."""

    step_key: str
    current_binding: WorkflowStepBinding | None
    current_calculation: Calculation | None
    superseded_binding_ids: tuple[WorkflowStepBindingId, ...] = ()
    superseded_calculation_ids: tuple[CalculationId, ...] = ()

    def __post_init__(self) -> None:
        if not self.step_key.strip():
            raise WorkflowGateError("workflow binding selection requires a non-blank step_key")
        if (self.current_binding is None) != (self.current_calculation is None):
            raise WorkflowGateError(
                "current workflow binding and Calculation must either both exist or both be absent"
            )
        if self.current_binding is not None and self.current_calculation is not None:
            if self.current_binding.calculation_id != self.current_calculation.id:
                raise WorkflowGateError(
                    "current workflow binding does not reference its selected Calculation"
                )


@dataclass(frozen=True, slots=True)
class WorkflowFreshnessEvaluation:
    """FreshnessEngine results with workflow binding supersession applied as an override."""

    workflow_plan_id: WorkflowPlanId
    results: tuple[FreshnessResult, ...]
    superseded_calculation_ids: tuple[CalculationId, ...] = ()

    def result(self, subject_id: UUID) -> FreshnessResult:
        """Resolve one freshness result or fail closed for incomplete gate evidence."""

        for item in self.results:
            if item.subject_id == subject_id:
                return item
        raise WorkflowGateError(
            f"freshness result is missing for scientific entity {subject_id}"
        )


@dataclass(frozen=True, slots=True)
class WorkflowStepGate:
    """Derived current scientific state and orchestration readiness for one workflow step."""

    step_key: str
    scientific_state: WorkflowStepScientificState
    readiness: WorkflowStepReadiness
    current_binding_id: WorkflowStepBindingId | None = None
    calculation_id: CalculationId | None = None
    freshness_state: FreshnessState | None = None
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WorkflowEdgeGate:
    """Derived scientific gate for one logical workflow edge."""

    upstream_step_key: str
    downstream_step_key: str
    role: str
    verdict: WorkflowEdgeGateVerdict
    source_binding_id: WorkflowStepBindingId | None = None
    accepted_structure_snapshot_id: StructureSnapshotId | None = None
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WorkflowScientificGateEvaluation:
    """Pure gate/readiness projection for one canonical workflow plan."""

    workflow_plan_id: WorkflowPlanId
    binding_selections: tuple[WorkflowBindingSelection, ...]
    step_gates: tuple[WorkflowStepGate, ...]
    edge_gates: tuple[WorkflowEdgeGate, ...]
    superseded_calculation_ids: tuple[CalculationId, ...] = ()

    def step(self, step_key: str) -> WorkflowStepGate:
        for item in self.step_gates:
            if item.step_key == step_key:
                return item
        raise KeyError(step_key)

    def edge(
        self,
        upstream_step_key: str,
        downstream_step_key: str,
        role: str,
    ) -> WorkflowEdgeGate:
        for item in self.edge_gates:
            if (
                item.upstream_step_key == upstream_step_key
                and item.downstream_step_key == downstream_step_key
                and item.role == role
            ):
                return item
        raise KeyError((upstream_step_key, downstream_step_key, role))


def resolve_workflow_binding_generations(
    *,
    plan: ScientificWorkflowPlan,
    bindings: tuple[WorkflowStepBinding, ...],
    calculations: tuple[Calculation, ...],
) -> tuple[WorkflowBindingSelection, ...]:
    """Select exactly one current generation per step without mutating historical bindings."""

    _validate_plan(plan)
    calculation_by_id = _calculation_index(calculations)
    grouped: dict[str, list[WorkflowStepBinding]] = defaultdict(list)
    for binding in bindings:
        if binding.workflow_plan_id != plan.id:
            continue
        try:
            plan.step(binding.step_key)
        except KeyError as error:
            raise WorkflowGateError(
                "WorkflowStepBinding references a step outside its workflow plan"
            ) from error
        grouped[binding.step_key].append(binding)

    selections: list[WorkflowBindingSelection] = []
    for step in plan.steps:
        ordered = tuple(sorted(grouped[step.key], key=lambda item: item.generation))
        if not ordered:
            selections.append(
                WorkflowBindingSelection(
                    step_key=step.key,
                    current_binding=None,
                    current_calculation=None,
                )
            )
            continue

        generations = tuple(item.generation for item in ordered)
        if generations != tuple(range(1, len(ordered) + 1)):
            raise WorkflowGateError(
                f"workflow step {step.key} binding generations must be contiguous from 1"
            )
        binding_ids = tuple(item.id for item in ordered)
        if len(binding_ids) != len(set(binding_ids)):
            raise WorkflowGateError("workflow binding ids must be unique")
        for index, binding in enumerate(ordered):
            if index == 0:
                if binding.supersedes_binding_id is not None:
                    raise WorkflowGateError(
                        "generation 1 workflow binding cannot supersede another binding"
                    )
            elif binding.supersedes_binding_id != ordered[index - 1].id:
                raise WorkflowGateError(
                    f"workflow step {step.key} binding supersession chain is not contiguous"
                )
            calculation = calculation_by_id.get(binding.calculation_id)
            if calculation is None:
                raise WorkflowGateError(
                    "WorkflowStepBinding references a missing Calculation"
                )
            _validate_binding_calculation(
                plan=plan,
                step_key=step.key,
                binding=binding,
                calculation=calculation,
            )

        current_binding = ordered[-1]
        current_calculation = calculation_by_id[current_binding.calculation_id]
        selections.append(
            WorkflowBindingSelection(
                step_key=step.key,
                current_binding=current_binding,
                current_calculation=current_calculation,
                superseded_binding_ids=tuple(item.id for item in ordered[:-1]),
                superseded_calculation_ids=tuple(
                    item.calculation_id for item in ordered[:-1]
                ),
            )
        )
    return tuple(selections)


def evaluate_workflow_freshness(
    *,
    plan: ScientificWorkflowPlan,
    bindings: tuple[WorkflowStepBinding, ...],
    calculations: tuple[Calculation, ...],
    dependencies: tuple[DependencyRecord, ...],
    current_hashes: Mapping[UUID, str],
    accepted_structure_sources: tuple[AcceptedStructureSource, ...] = (),
    invalid_ids: set[UUID] | None = None,
    superseded_ids: set[UUID] | None = None,
) -> WorkflowFreshnessEvaluation:
    """Reuse the frozen FreshnessEngine and mark historical workflow Calculations superseded."""

    selections = resolve_workflow_binding_generations(
        plan=plan,
        bindings=bindings,
        calculations=calculations,
    )
    plan_binding_ids = {
        binding.id for binding in bindings if binding.workflow_plan_id == plan.id
    }
    source_snapshot_ids: set[UUID] = set()
    for source in accepted_structure_sources:
        if source.upstream_binding.workflow_plan_id != plan.id:
            raise WorkflowGateError(
                "accepted-structure source belongs to another workflow plan"
            )
        if source.upstream_binding.id not in plan_binding_ids:
            raise WorkflowGateError(
                "accepted-structure source references a binding absent from this evaluation"
            )
        source_snapshot_ids.add(source.promotion.snapshot.id)

    workflow_calculation_ids = {
        binding.calculation_id
        for binding in bindings
        if binding.workflow_plan_id == plan.id
    }
    workflow_superseded = {
        calculation_id
        for selection in selections
        for calculation_id in selection.superseded_calculation_ids
    }
    dependency_node_ids = {
        node_id
        for record in dependencies
        for node_id in (record.upstream_id, record.downstream_id)
    }
    node_ids = dependency_node_ids | workflow_calculation_ids | source_snapshot_ids

    invalid = set() if invalid_ids is None else set(invalid_ids)
    explicit_superseded = set() if superseded_ids is None else set(superseded_ids)
    unknown_overrides = (invalid | explicit_superseded) - node_ids
    if unknown_overrides:
        raise WorkflowGateError(
            "workflow freshness override references an entity outside the evaluation graph"
        )

    try:
        results = FreshnessEngine(dependencies).evaluate(
            node_ids=node_ids,
            current_hashes=dict(current_hashes),
            invalid_ids=invalid,
            superseded_ids=explicit_superseded | workflow_superseded,
        )
    except ProvenanceIntegrityError as error:
        raise WorkflowGateError(str(error)) from error

    return WorkflowFreshnessEvaluation(
        workflow_plan_id=plan.id,
        results=tuple(results[node_id] for node_id in sorted(results, key=str)),
        superseded_calculation_ids=tuple(sorted(workflow_superseded, key=str)),
    )


def evaluate_workflow_scientific_gates(
    *,
    plan: ScientificWorkflowPlan,
    bindings: tuple[WorkflowStepBinding, ...],
    calculations: tuple[Calculation, ...],
    freshness: WorkflowFreshnessEvaluation,
    accepted_structure_sources: tuple[AcceptedStructureSource, ...] = (),
) -> WorkflowScientificGateEvaluation:
    """Derive fail-closed step and edge gates without retrying, executing, or persisting anything."""

    _validate_plan(plan)
    if freshness.workflow_plan_id != plan.id:
        raise WorkflowGateError("freshness evaluation belongs to another workflow plan")
    selections = resolve_workflow_binding_generations(
        plan=plan,
        bindings=bindings,
        calculations=calculations,
    )
    superseded_calculation_ids = tuple(
        sorted(
            {
                calculation_id
                for selection in selections
                for calculation_id in selection.superseded_calculation_ids
            },
            key=str,
        )
    )
    if superseded_calculation_ids != freshness.superseded_calculation_ids:
        raise WorkflowGateError(
            "freshness evaluation does not match the current workflow binding generations"
        )

    source_by_binding = _accepted_source_index(
        plan=plan,
        bindings=bindings,
        sources=accepted_structure_sources,
    )
    selection_by_step = {item.step_key: item for item in selections}

    states: dict[str, WorkflowStepScientificState] = {}
    state_reasons: dict[str, tuple[str, ...]] = {}
    state_freshness: dict[str, FreshnessState | None] = {}
    for selection in selections:
        state, reason_codes, freshness_state = _base_step_state(
            selection=selection,
            freshness=freshness,
        )
        states[selection.step_key] = state
        state_reasons[selection.step_key] = reason_codes
        state_freshness[selection.step_key] = freshness_state

    _apply_accepted_structure_currentness(
        plan=plan,
        selections=selection_by_step,
        source_by_binding=source_by_binding,
        freshness=freshness,
        states=states,
        reasons=state_reasons,
    )

    edge_gates = tuple(
        _evaluate_edge_gate(
            edge=edge,
            upstream_selection=selection_by_step[edge.upstream_step_key],
            upstream_state=states[edge.upstream_step_key],
            source_by_binding=source_by_binding,
            freshness=freshness,
        )
        for edge in plan.edges
    )
    incoming_edge_gates: dict[str, list[WorkflowEdgeGate]] = defaultdict(list)
    for edge_gate in edge_gates:
        incoming_edge_gates[edge_gate.downstream_step_key].append(edge_gate)

    step_gates: list[WorkflowStepGate] = []
    for selection in selections:
        state = states[selection.step_key]
        readiness, readiness_reason = _step_readiness(
            selection=selection,
            state=state,
            incoming_edges=tuple(incoming_edge_gates[selection.step_key]),
        )
        binding = selection.current_binding
        calculation = selection.current_calculation
        step_gates.append(
            WorkflowStepGate(
                step_key=selection.step_key,
                scientific_state=state,
                readiness=readiness,
                current_binding_id=None if binding is None else binding.id,
                calculation_id=None if calculation is None else calculation.id,
                freshness_state=state_freshness[selection.step_key],
                reason_codes=_merge_reason_codes(
                    state_reasons[selection.step_key],
                    (readiness_reason,),
                ),
            )
        )

    return WorkflowScientificGateEvaluation(
        workflow_plan_id=plan.id,
        binding_selections=selections,
        step_gates=tuple(step_gates),
        edge_gates=edge_gates,
        superseded_calculation_ids=superseded_calculation_ids,
    )


def _validate_plan(plan: ScientificWorkflowPlan) -> None:
    try:
        validate_workflow_plan_recipe_contract(plan)
    except WorkflowRecipeContractError as error:
        raise WorkflowGateError(str(error)) from error


def _calculation_index(calculations: tuple[Calculation, ...]) -> dict[CalculationId, Calculation]:
    result: dict[CalculationId, Calculation] = {}
    for calculation in calculations:
        if calculation.id in result:
            raise WorkflowGateError("Calculation ids must be unique in workflow gate evaluation")
        result[calculation.id] = calculation
    return result


def _validate_binding_calculation(
    *,
    plan: ScientificWorkflowPlan,
    step_key: str,
    binding: WorkflowStepBinding,
    calculation: Calculation,
) -> None:
    step = plan.step(step_key)
    if calculation.project_id != plan.project_id:
        raise WorkflowGateError("workflow-bound Calculation belongs to another Project")
    if calculation.calculation_type is not step.calculation_type:
        raise WorkflowGateError(
            "workflow-bound CalculationType does not match the workflow step"
        )
    if calculation.recipe_id != step.recipe_id:
        raise WorkflowGateError(
            "workflow-bound Calculation recipe does not match the workflow step"
        )
    if calculation.input_structure_snapshot_id != binding.resolved_input_structure_snapshot_id:
        raise WorkflowGateError(
            "workflow binding resolved snapshot does not match Calculation input"
        )


def _accepted_source_index(
    *,
    plan: ScientificWorkflowPlan,
    bindings: tuple[WorkflowStepBinding, ...],
    sources: tuple[AcceptedStructureSource, ...],
) -> dict[WorkflowStepBindingId, AcceptedStructureSource]:
    binding_ids = {
        binding.id for binding in bindings if binding.workflow_plan_id == plan.id
    }
    result: dict[WorkflowStepBindingId, AcceptedStructureSource] = {}
    for source in sources:
        binding = source.upstream_binding
        if binding.workflow_plan_id != plan.id:
            raise WorkflowGateError(
                "accepted-structure source belongs to another workflow plan"
            )
        if binding.id not in binding_ids:
            raise WorkflowGateError(
                "accepted-structure source references a binding absent from this evaluation"
            )
        if binding.id in result:
            raise WorkflowGateError(
                "accepted-structure source binding ids must be unique"
            )
        result[binding.id] = source
    return result


def _base_step_state(
    *,
    selection: WorkflowBindingSelection,
    freshness: WorkflowFreshnessEvaluation,
) -> tuple[WorkflowStepScientificState, tuple[str, ...], FreshnessState | None]:
    calculation = selection.current_calculation
    if calculation is None:
        return (
            WorkflowStepScientificState.UNMATERIALIZED,
            ("no_current_binding",),
            None,
        )

    freshness_result = freshness.result(calculation.id)
    if freshness_result.state is FreshnessState.INVALID:
        return (
            WorkflowStepScientificState.INVALID,
            ("calculation_freshness_invalid",),
            freshness_result.state,
        )
    if freshness_result.state is FreshnessState.STALE:
        return (
            WorkflowStepScientificState.STALE,
            ("calculation_freshness_stale",),
            freshness_result.state,
        )
    if freshness_result.state is FreshnessState.SUPERSEDED:
        return (
            WorkflowStepScientificState.SUPERSEDED,
            ("current_calculation_explicitly_superseded",),
            freshness_result.state,
        )

    status = calculation.status
    if status is CalculationScientificStatus.CONVERGED:
        return (
            WorkflowStepScientificState.PASSED,
            ("calculation_converged_and_fresh",),
            freshness_result.state,
        )
    if status in {
        CalculationScientificStatus.DRAFT,
        CalculationScientificStatus.READY,
        CalculationScientificStatus.SUBMITTED,
        CalculationScientificStatus.RUNNING,
        CalculationScientificStatus.PARSING,
    }:
        return (
            WorkflowStepScientificState.IN_PROGRESS,
            (f"calculation_{status.value}",),
            freshness_result.state,
        )
    if status is CalculationScientificStatus.STALE:
        return (
            WorkflowStepScientificState.STALE,
            ("calculation_status_stale",),
            freshness_result.state,
        )
    if status is CalculationScientificStatus.INVALID:
        return (
            WorkflowStepScientificState.INVALID,
            ("calculation_status_invalid",),
            freshness_result.state,
        )
    if status in {
        CalculationScientificStatus.BLOCKED,
        CalculationScientificStatus.COMPLETED_UNCONVERGED,
        CalculationScientificStatus.FAILED,
        CalculationScientificStatus.CANCELLED,
    }:
        return (
            WorkflowStepScientificState.BLOCKED,
            (f"calculation_{status.value}",),
            freshness_result.state,
        )
    raise WorkflowGateError(f"unsupported CalculationScientificStatus: {status.value}")


def _apply_accepted_structure_currentness(
    *,
    plan: ScientificWorkflowPlan,
    selections: dict[str, WorkflowBindingSelection],
    source_by_binding: dict[WorkflowStepBindingId, AcceptedStructureSource],
    freshness: WorkflowFreshnessEvaluation,
    states: dict[str, WorkflowStepScientificState],
    reasons: dict[str, tuple[str, ...]],
) -> None:
    for edge in plan.edges:
        if edge.role != WORKFLOW_EDGE_ACCEPTED_STRUCTURE:
            continue
        downstream = selections[edge.downstream_step_key]
        downstream_binding = downstream.current_binding
        if downstream_binding is None:
            continue
        upstream = selections[edge.upstream_step_key]
        upstream_binding = upstream.current_binding
        if upstream_binding is None:
            _promote_step_state(
                step_key=edge.downstream_step_key,
                candidate=WorkflowStepScientificState.INVALID,
                reason="accepted_structure_upstream_binding_missing",
                states=states,
                reasons=reasons,
            )
            continue
        source = source_by_binding.get(upstream_binding.id)
        if source is None:
            _promote_step_state(
                step_key=edge.downstream_step_key,
                candidate=WorkflowStepScientificState.BLOCKED,
                reason="accepted_structure_currentness_unproven",
                states=states,
                reasons=reasons,
            )
            continue
        snapshot_freshness = freshness.result(source.promotion.snapshot.id).state
        if snapshot_freshness is FreshnessState.INVALID:
            _promote_step_state(
                step_key=edge.downstream_step_key,
                candidate=WorkflowStepScientificState.INVALID,
                reason="accepted_structure_invalid",
                states=states,
                reasons=reasons,
            )
            continue
        if snapshot_freshness in {FreshnessState.STALE, FreshnessState.SUPERSEDED}:
            _promote_step_state(
                step_key=edge.downstream_step_key,
                candidate=WorkflowStepScientificState.STALE,
                reason=f"accepted_structure_{snapshot_freshness.value}",
                states=states,
                reasons=reasons,
            )
            continue
        if downstream_binding.resolved_input_structure_snapshot_id != source.promotion.snapshot.id:
            _promote_step_state(
                step_key=edge.downstream_step_key,
                candidate=WorkflowStepScientificState.STALE,
                reason="accepted_structure_binding_superseded",
                states=states,
                reasons=reasons,
            )


def _evaluate_edge_gate(
    *,
    edge: object,
    upstream_selection: WorkflowBindingSelection,
    upstream_state: WorkflowStepScientificState,
    source_by_binding: dict[WorkflowStepBindingId, AcceptedStructureSource],
    freshness: WorkflowFreshnessEvaluation,
) -> WorkflowEdgeGate:
    upstream_key = getattr(edge, "upstream_step_key")
    downstream_key = getattr(edge, "downstream_step_key")
    role = getattr(edge, "role")
    if not isinstance(upstream_key, str) or not isinstance(downstream_key, str) or not isinstance(role, str):
        raise WorkflowGateError("workflow edge is malformed")

    if upstream_state in {
        WorkflowStepScientificState.UNMATERIALIZED,
        WorkflowStepScientificState.IN_PROGRESS,
    }:
        return WorkflowEdgeGate(
            upstream_step_key=upstream_key,
            downstream_step_key=downstream_key,
            role=role,
            verdict=WorkflowEdgeGateVerdict.WAITING,
            reason_codes=(f"upstream_{upstream_state.value}",),
        )
    if upstream_state is not WorkflowStepScientificState.PASSED:
        return WorkflowEdgeGate(
            upstream_step_key=upstream_key,
            downstream_step_key=downstream_key,
            role=role,
            verdict=WorkflowEdgeGateVerdict.BLOCKED,
            reason_codes=(f"upstream_{upstream_state.value}",),
        )
    if role != WORKFLOW_EDGE_ACCEPTED_STRUCTURE:
        return WorkflowEdgeGate(
            upstream_step_key=upstream_key,
            downstream_step_key=downstream_key,
            role=role,
            verdict=WorkflowEdgeGateVerdict.BLOCKED,
            reason_codes=("unsupported_workflow_edge_role",),
        )

    upstream_binding = upstream_selection.current_binding
    upstream_calculation = upstream_selection.current_calculation
    if upstream_binding is None or upstream_calculation is None:
        raise WorkflowGateError("passed workflow step requires a current binding and Calculation")
    source = source_by_binding.get(upstream_binding.id)
    if source is None:
        return WorkflowEdgeGate(
            upstream_step_key=upstream_key,
            downstream_step_key=downstream_key,
            role=role,
            verdict=WorkflowEdgeGateVerdict.WAITING,
            source_binding_id=upstream_binding.id,
            reason_codes=("accepted_structure_not_promoted",),
        )
    if source.upstream_calculation.id != upstream_calculation.id:
        raise WorkflowGateError(
            "accepted-structure source does not reference the current upstream Calculation"
        )
    snapshot_freshness = freshness.result(source.promotion.snapshot.id).state
    if snapshot_freshness is not FreshnessState.FRESH:
        return WorkflowEdgeGate(
            upstream_step_key=upstream_key,
            downstream_step_key=downstream_key,
            role=role,
            verdict=WorkflowEdgeGateVerdict.BLOCKED,
            source_binding_id=upstream_binding.id,
            accepted_structure_snapshot_id=source.promotion.snapshot.id,
            reason_codes=(f"accepted_structure_{snapshot_freshness.value}",),
        )
    return WorkflowEdgeGate(
        upstream_step_key=upstream_key,
        downstream_step_key=downstream_key,
        role=role,
        verdict=WorkflowEdgeGateVerdict.OPEN,
        source_binding_id=upstream_binding.id,
        accepted_structure_snapshot_id=source.promotion.snapshot.id,
        reason_codes=("accepted_structure_converged_fresh_promoted",),
    )


def _step_readiness(
    *,
    selection: WorkflowBindingSelection,
    state: WorkflowStepScientificState,
    incoming_edges: tuple[WorkflowEdgeGate, ...],
) -> tuple[WorkflowStepReadiness, str]:
    if selection.current_binding is not None:
        if state is WorkflowStepScientificState.PASSED:
            return WorkflowStepReadiness.SATISFIED, "current_generation_satisfies_step"
        if state is WorkflowStepScientificState.IN_PROGRESS:
            return WorkflowStepReadiness.WAITING, "current_generation_in_progress"
        return WorkflowStepReadiness.BLOCKED, "current_generation_requires_policy_decision"

    if not incoming_edges:
        return WorkflowStepReadiness.READY, "root_step_ready_to_materialize"
    if any(edge.verdict is WorkflowEdgeGateVerdict.BLOCKED for edge in incoming_edges):
        return WorkflowStepReadiness.BLOCKED, "one_or_more_scientific_gates_blocked"
    if all(edge.verdict is WorkflowEdgeGateVerdict.OPEN for edge in incoming_edges):
        return WorkflowStepReadiness.READY, "all_scientific_gates_open"
    return WorkflowStepReadiness.WAITING, "waiting_for_scientific_gates"


def _promote_step_state(
    *,
    step_key: str,
    candidate: WorkflowStepScientificState,
    reason: str,
    states: dict[str, WorkflowStepScientificState],
    reasons: dict[str, tuple[str, ...]],
) -> None:
    precedence = {
        WorkflowStepScientificState.UNMATERIALIZED: 0,
        WorkflowStepScientificState.IN_PROGRESS: 1,
        WorkflowStepScientificState.PASSED: 2,
        WorkflowStepScientificState.SUPERSEDED: 3,
        WorkflowStepScientificState.BLOCKED: 4,
        WorkflowStepScientificState.STALE: 5,
        WorkflowStepScientificState.INVALID: 6,
    }
    if precedence[candidate] > precedence[states[step_key]]:
        states[step_key] = candidate
    reasons[step_key] = _merge_reason_codes(reasons[step_key], (reason,))


def _merge_reason_codes(*groups: tuple[str, ...]) -> tuple[str, ...]:
    ordered: list[str] = []
    for group in groups:
        for code in group:
            if code not in ordered:
                ordered.append(code)
    return tuple(ordered)

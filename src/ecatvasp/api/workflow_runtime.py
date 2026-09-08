"""Shared application-level reconstruction of current workflow orchestration.

This module composes existing workflow freshness, scientific-gate, recovery and
orchestration authorities.  It is deliberately side-effect free: it does not
persist state, create execution attempts, submit work, or infer scheduler state
as scientific success.
"""

from __future__ import annotations

from uuid import UUID

from ecatvasp.domain import ScientificWorkflowPlan
from ecatvasp.provenance import scientific_hash
from ecatvasp.storage import ProjectBundle
from ecatvasp.workflow import (
    evaluate_workflow_freshness,
    evaluate_workflow_recovery_policy,
    evaluate_workflow_scientific_gates,
    reconcile_workflow_orchestration,
)
from ecatvasp.workflow.orchestration import (
    WorkflowExecutionSource,
    WorkflowOrchestrationEvaluation,
)


def build_current_workflow_orchestration(
    bundle: ProjectBundle,
    plan: ScientificWorkflowPlan,
    *,
    execution_sources: tuple[WorkflowExecutionSource, ...] = (),
) -> WorkflowOrchestrationEvaluation:
    """Reconstruct the exact current orchestration projection for one plan.

    ``execution_sources`` are caller-resolved exact current-generation
    ``ExecutionPlan`` values.  The helper never discovers or fabricates those
    plans; it only feeds them into the already-frozen workflow reconciler after
    rebuilding freshness and scientific gates from the current ProjectBundle.
    """

    current_hashes: dict[UUID, str] = {}
    for variant in bundle.structure_variants:
        current_hashes[variant.id] = scientific_hash(variant)
    for snapshot in bundle.structure_snapshots:
        current_hashes[snapshot.id] = scientific_hash(snapshot)
    for active_site in bundle.active_sites:
        current_hashes[active_site.id] = scientific_hash(active_site)
    for adsorption_state in bundle.adsorption_states:
        current_hashes[adsorption_state.id] = scientific_hash(adsorption_state)
    for conformer in bundle.state_conformers:
        current_hashes[conformer.id] = scientific_hash(conformer)
    for fingerprint in bundle.method_fingerprints:
        current_hashes[fingerprint.id] = scientific_hash(fingerprint)
    for calculation in bundle.calculations:
        current_hashes[calculation.id] = scientific_hash(calculation)
    for artifact in bundle.artifacts:
        current_hashes[artifact.id] = scientific_hash(artifact)
    for analysis in bundle.analyses:
        current_hashes[analysis.id] = scientific_hash(analysis)

    freshness = evaluate_workflow_freshness(
        plan=plan,
        bindings=bundle.workflow_step_bindings,
        calculations=bundle.calculations,
        dependencies=bundle.dependency_records,
        current_hashes=current_hashes,
    )
    gates = evaluate_workflow_scientific_gates(
        plan=plan,
        bindings=bundle.workflow_step_bindings,
        calculations=bundle.calculations,
        freshness=freshness,
    )
    recovery = evaluate_workflow_recovery_policy(plan=plan, gates=gates)
    return reconcile_workflow_orchestration(
        plan=plan,
        gates=gates,
        recovery=recovery,
        execution_sources=execution_sources,
    )

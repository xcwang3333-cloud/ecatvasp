from __future__ import annotations

import io
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ecatvasp.api import open_project
from ecatvasp.cli import main as cli_main
from ecatvasp.domain import (
    Analysis,
    AnalysisStatus,
    AnalysisType,
    Lattice,
    Project,
    ScientificWorkflowPlan,
    StructureSite,
    StructureSnapshot,
    WorkflowRecipeIdentity,
    new_atom_uid,
)
from ecatvasp.frontend import render_frontend_handoff_json
from ecatvasp.provenance import (
    DependencyKind,
    DependencyRecord,
    FreshnessState,
    ProvenanceRecord,
    scientific_hash,
)
from ecatvasp.reporting import ScientificReportError
from ecatvasp.schema.version import SCHEMA_VERSION
from ecatvasp.storage import ProjectBundle, ProjectStore
from ecatvasp.visualization import build_structure_presentation
from ecatvasp.workflow import (
    WORKFLOW_RECIPE_SLAB_SCIENTIFIC_PREPARATION,
    WorkflowEdgeGate,
    WorkflowEdgeGateVerdict,
    WorkflowScientificGateEvaluation,
    WorkflowStepGate,
    WorkflowStepReadiness,
    WorkflowStepScientificState,
    get_workflow_recipe_spec,
    resolve_workflow_binding_generations,
)
from ecatvasp.workspace import build_workflow_readiness_dashboard


def _snapshot() -> StructureSnapshot:
    return StructureSnapshot(
        label="v0.9 final root",
        lattice=Lattice(
            vectors=((3.0, 0.0, 0.0), (0.0, 3.0, 0.0), (0.0, 0.0, 15.0))
        ),
        sites=(
            StructureSite(
                atom_uid=new_atom_uid(),
                element="C",
                fractional_coords=(0.0, 0.0, 0.5),
            ),
        ),
    )


def _workflow_plan(project: Project, snapshot: StructureSnapshot) -> ScientificWorkflowPlan:
    identity = WorkflowRecipeIdentity(WORKFLOW_RECIPE_SLAB_SCIENTIFIC_PREPARATION)
    spec = get_workflow_recipe_spec(identity)
    return ScientificWorkflowPlan(
        project_id=project.id,
        workflow_recipe=identity,
        root_structure_snapshot_id=snapshot.id,
        steps=spec.steps,
        edges=spec.edges,
    )


def _initial_bundle() -> tuple[ProjectBundle, StructureSnapshot, Analysis, ScientificWorkflowPlan]:
    project = Project(
        name="v0.9 final acceptance",
        slug="v09-final-acceptance",
        schema_version=SCHEMA_VERSION,
        created_at=datetime(2026, 9, 7, 1, 5, tzinfo=UTC),
    )
    snapshot = _snapshot()
    analysis = Analysis(
        project_id=project.id,
        analysis_type=AnalysisType.GEOMETRY,
        input_artifact_ids=(),
        status=AnalysisStatus.COMPLETED,
        tool="ecatvasp.geometry",
        tool_version="0.9",
    )
    provenance = ProvenanceRecord(
        subject_id=analysis.id,
        tool="ecatvasp.geometry",
        tool_version="0.9",
        parameters_hash="a" * 64,
    )
    dependency = DependencyRecord(
        upstream_id=snapshot.id,
        downstream_id=analysis.id,
        kind=DependencyKind.SCIENTIFIC,
        role="input_structure",
        recorded_hash=scientific_hash(snapshot),
    )
    plan = _workflow_plan(project, snapshot)
    bundle = ProjectBundle(
        project=project,
        structure_snapshots=(snapshot,),
        workflow_plans=(plan,),
        analyses=(analysis,),
        provenance_records=(provenance,),
        dependency_records=(dependency,),
    )
    bundle.validate()
    return bundle, snapshot, analysis, plan


def _blocked_readiness(bundle: ProjectBundle, plan: ScientificWorkflowPlan):
    selections = resolve_workflow_binding_generations(
        plan=plan,
        bindings=bundle.workflow_step_bindings,
        calculations=bundle.calculations,
    )
    gates = WorkflowScientificGateEvaluation(
        workflow_plan_id=plan.id,
        binding_selections=selections,
        step_gates=tuple(
            WorkflowStepGate(
                step_key=selection.step_key,
                scientific_state=WorkflowStepScientificState.UNMATERIALIZED,
                readiness=WorkflowStepReadiness.BLOCKED,
                current_binding_id=None,
                calculation_id=None,
                freshness_state=None,
                reason_codes=("not_materialized",),
            )
            for selection in selections
        ),
        edge_gates=tuple(
            WorkflowEdgeGate(
                upstream_step_key=edge.upstream_step_key,
                downstream_step_key=edge.downstream_step_key,
                role=edge.role,
                verdict=WorkflowEdgeGateVerdict.WAITING,
                reason_codes=("upstream_step_not_satisfied",),
            )
            for edge in plan.edges
        ),
        superseded_calculation_ids=(),
    )
    return build_workflow_readiness_dashboard(
        bundle,
        workflow_plan_id=plan.id,
        gates=gates,
    )


def test_v09_projectstore_workspace_frontend_cli_and_drift_fail_closed(tmp_path: Path) -> None:
    bundle, snapshot, analysis, plan = _initial_bundle()
    store = ProjectStore(tmp_path / "v09-project")
    store.save(bundle)

    facade = open_project(store.root)
    inspection = facade.inspect()
    readiness = _blocked_readiness(store.open(), plan)
    presentation = build_structure_presentation(snapshot)
    handoff = facade.frontend_handoff(
        inventory=inspection.inventory,
        readiness=(readiness,),
        presentations=(presentation,),
    )
    payload = json.loads(render_frontend_handoff_json(handoff))

    assert inspection.projection.project_id == bundle.project.id
    assert inspection.inventory.row(analysis.id).freshness.state is FreshnessState.FRESH
    assert readiness.workflow_plan_id == plan.id
    assert all(step.readiness is WorkflowStepReadiness.BLOCKED for step in readiness.steps)
    assert payload["report"]["project"]["schema_version"] == 3
    assert payload["report"]["readiness"][0]["workflow_plan_id"] == str(plan.id)
    assert payload["report"]["presentations"][0]["kind"] == "structure"
    assert payload["report"]["presentations"][0]["payload"]["structure_snapshot_id"] == str(
        snapshot.id
    )

    cli_before = io.StringIO()
    assert cli_main(
        ["--project", str(store.root), "status", "--json"],
        stdout=cli_before,
    ) == 0
    status_before = json.loads(cli_before.getvalue())
    assert ["fresh", 1] in status_before["freshness"]
    assert status_before["attention_rows"] == 0

    changed_site = replace(snapshot.sites[0], fractional_coords=(0.125, 0.0, 0.5))
    changed_snapshot = replace(snapshot, sites=(changed_site,))
    changed_bundle = replace(bundle, structure_snapshots=(changed_snapshot,))
    store.save(changed_bundle)

    after = facade.inspect()
    stale_row = after.inventory.row(analysis.id)
    assert stale_row.freshness.state is FreshnessState.STALE
    assert tuple(reason.code for reason in stale_row.freshness.reasons) == (
        "scientific_hash_changed",
    )

    cli_after = io.StringIO()
    assert cli_main(
        ["--project", str(store.root), "status", "--json"],
        stdout=cli_after,
    ) == 0
    status_after = json.loads(cli_after.getvalue())
    assert ["stale", 1] in status_after["freshness"]
    assert status_after["attention_rows"] == 1

    with pytest.raises(ScientificReportError, match="source hash does not match"):
        facade.frontend_handoff(
            inventory=after.inventory,
            presentations=(presentation,),
        )

    current_presentation = build_structure_presentation(changed_snapshot)
    current_handoff = facade.frontend_handoff(
        inventory=after.inventory,
        presentations=(current_presentation,),
    )
    current_payload = json.loads(render_frontend_handoff_json(current_handoff))
    analysis_row = next(
        row
        for row in current_payload["report"]["inventory"]["rows"]
        if row["entity_id"] == str(analysis.id)
    )
    assert analysis_row["freshness"]["state"] == "stale"
    assert current_handoff.handoff_hash != handoff.handoff_hash
    assert SCHEMA_VERSION == 3

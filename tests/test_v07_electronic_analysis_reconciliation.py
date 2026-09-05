from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import pytest

import ecatvasp.domain as domain
import ecatvasp.provenance as provenance
import ecatvasp.storage as storage
import ecatvasp.workflow as workflow


@dataclass(frozen=True, slots=True)
class _Case:
    project: domain.Project
    source_analysis: domain.Analysis
    input_artifact: domain.Artifact
    analysis: domain.Analysis
    output_artifact: domain.Artifact
    provenance_records: tuple[provenance.ProvenanceRecord, ...]
    dependencies: tuple[provenance.DependencyRecord, ...]
    requirement: workflow.ElectronicAnalysisRequirement


def _case() -> _Case:
    project = domain.Project(name="Electronic reconciliation", slug="electronic-reconciliation")
    source_analysis = domain.Analysis(
        project_id=project.id,
        analysis_type=domain.AnalysisType.DOS,
        input_artifact_ids=(),
        status=domain.AnalysisStatus.COMPLETED,
        tool="ecatvasp.analysis.dos-materializer",
        tool_version="1",
        parameters_hash="1" * 64,
    )
    input_artifact = domain.Artifact(
        artifact_type=domain.ArtifactType.PARSED_RESULT,
        producer=domain.AnalysisProducerRef(source_analysis.id),
        availability=domain.ArtifactAvailability.LOCAL,
        retrieval_policy=domain.RetrievalPolicy.ALWAYS,
        local_path="analyses/source/canonical-dos.json",
        size_bytes=10,
        sha256="a" * 64,
    )
    analysis = domain.Analysis(
        project_id=project.id,
        analysis_type=domain.AnalysisType.BAND_CENTER,
        input_artifact_ids=(input_artifact.id,),
        status=domain.AnalysisStatus.COMPLETED,
        tool="ecatvasp.analysis.band-center",
        tool_version="1",
        parameters_hash="2" * 64,
    )
    output_artifact = domain.Artifact(
        artifact_type=domain.ArtifactType.DERIVED_DATASET,
        producer=domain.AnalysisProducerRef(analysis.id),
        availability=domain.ArtifactAvailability.LOCAL,
        retrieval_policy=domain.RetrievalPolicy.ALWAYS,
        local_path="analyses/target/canonical-band-center.json",
        size_bytes=12,
        sha256="b" * 64,
    )
    provenance_records = (
        provenance.ProvenanceRecord(
            subject_id=input_artifact.id,
            tool="source",
            tool_version="1",
        ),
        provenance.ProvenanceRecord(
            subject_id=analysis.id,
            tool="band-center",
            tool_version="1",
            parameters_hash=analysis.parameters_hash,
        ),
        provenance.ProvenanceRecord(
            subject_id=output_artifact.id,
            tool="band-center",
            tool_version="1",
            parameters_hash=output_artifact.sha256,
        ),
    )
    dependencies = (
        provenance.DependencyRecord(
            upstream_id=source_analysis.id,
            downstream_id=input_artifact.id,
            kind=provenance.DependencyKind.SCIENTIFIC,
            role="canonical_dos",
            recorded_hash=provenance.scientific_hash(source_analysis),
        ),
        provenance.DependencyRecord(
            upstream_id=input_artifact.id,
            downstream_id=analysis.id,
            kind=provenance.DependencyKind.SCIENTIFIC,
            role="canonical_dos",
            recorded_hash=provenance.scientific_hash(input_artifact),
        ),
        provenance.DependencyRecord(
            upstream_id=analysis.id,
            downstream_id=output_artifact.id,
            kind=provenance.DependencyKind.SCIENTIFIC,
            role="canonical_band_center",
            recorded_hash=provenance.scientific_hash(analysis),
        ),
    )
    requirement = workflow.ElectronicAnalysisRequirement(
        key="d-band-center",
        project_id=project.id,
        analysis_type=domain.AnalysisType.BAND_CENTER,
        input_artifact_ids=(input_artifact.id,),
        parameters_hash=analysis.parameters_hash or "",
    )
    return _Case(
        project=project,
        source_analysis=source_analysis,
        input_artifact=input_artifact,
        analysis=analysis,
        output_artifact=output_artifact,
        provenance_records=provenance_records,
        dependencies=dependencies,
        requirement=requirement,
    )


def _reconcile(
    case: _Case,
    *,
    include_analysis: bool = True,
    input_artifact: domain.Artifact | None = None,
    dependencies: tuple[provenance.DependencyRecord, ...] | None = None,
    current_hashes: dict | None = None,
    workflow_gates: workflow.WorkflowScientificGateEvaluation | None = None,
    requirement: workflow.ElectronicAnalysisRequirement | None = None,
) -> workflow.ElectronicAnalysisReconciliationReport:
    actual_input = case.input_artifact if input_artifact is None else input_artifact
    analyses = (
        (case.source_analysis, case.analysis)
        if include_analysis
        else (case.source_analysis,)
    )
    artifacts = (
        (actual_input, case.output_artifact)
        if include_analysis
        else (actual_input,)
    )
    return workflow.reconcile_electronic_analyses(
        requirements=(case.requirement if requirement is None else requirement,),
        analyses=analyses,
        artifacts=artifacts,
        dependencies=case.dependencies if dependencies is None else dependencies,
        current_hashes=current_hashes,
        workflow_gates=workflow_gates,
    )


def test_unmaterialized_exact_analysis_is_ready_when_inputs_are_fresh_and_local() -> None:
    case = _case()
    report = _reconcile(
        case,
        include_analysis=False,
        dependencies=(case.dependencies[0],),
    )
    item = report.requirement("d-band-center")
    assert item.scientific_state is workflow.ElectronicAnalysisScientificState.UNMATERIALIZED
    assert item.readiness is workflow.WorkflowStepReadiness.READY
    assert item.analysis_id is None
    assert item.reason_codes == ("exact_analysis_absent",)


def test_unmaterialized_exact_analysis_waits_for_remote_input_retrieval() -> None:
    case = _case()
    remote = replace(
        case.input_artifact,
        availability=domain.ArtifactAvailability.REMOTE,
        remote_path="/remote/canonical-dos.json",
    )
    report = _reconcile(
        case,
        include_analysis=False,
        input_artifact=remote,
        dependencies=(case.dependencies[0],),
    )
    item = report.requirement("d-band-center")
    assert item.scientific_state is workflow.ElectronicAnalysisScientificState.UNMATERIALIZED
    assert item.readiness is workflow.WorkflowStepReadiness.WAITING
    assert "input_artifact_retrieval_required" in item.reason_codes


def test_completed_exact_analysis_is_satisfied_only_with_full_scientific_chain() -> None:
    case = _case()
    report = _reconcile(case)
    item = report.requirement("d-band-center")
    assert item.scientific_state is workflow.ElectronicAnalysisScientificState.COMPLETED
    assert item.readiness is workflow.WorkflowStepReadiness.SATISFIED
    assert item.analysis_id == case.analysis.id
    assert item.output_artifact_ids == (case.output_artifact.id,)
    assert item.freshness_state is provenance.FreshnessState.FRESH

    incomplete = _reconcile(
        case,
        dependencies=(case.dependencies[0], case.dependencies[2]),
    )
    broken = incomplete.requirement("d-band-center")
    assert broken.scientific_state is workflow.ElectronicAnalysisScientificState.INVALID
    assert broken.readiness is workflow.WorkflowStepReadiness.BLOCKED
    assert "completed_analysis_missing_input_scientific_dependency" in broken.reason_codes


def test_upstream_hash_drift_propagates_stale_to_analysis_projection() -> None:
    case = _case()
    report = _reconcile(
        case,
        current_hashes={case.input_artifact.id: "f" * 64},
    )
    item = report.requirement("d-band-center")
    assert item.scientific_state is workflow.ElectronicAnalysisScientificState.STALE
    assert item.readiness is workflow.WorkflowStepReadiness.BLOCKED
    assert item.freshness_state is provenance.FreshnessState.STALE
    assert "scientific_hash_changed" in item.reason_codes


def test_duplicate_exact_analysis_identity_fails_closed() -> None:
    case = _case()
    duplicate = replace(case.analysis, id=domain.new_analysis_id())
    with pytest.raises(
        workflow.ElectronicAnalysisReconciliationError,
        match="duplicate exact Analysis identities",
    ):
        workflow.reconcile_electronic_analyses(
            requirements=(case.requirement,),
            analyses=(case.source_analysis, case.analysis, duplicate),
            artifacts=(case.input_artifact, case.output_artifact),
            dependencies=case.dependencies,
        )


def _workflow_projection(
    *,
    project_id: domain.ProjectId,
    anchored_calculation: domain.Calculation,
    current_calculation: domain.Calculation,
    readiness: workflow.WorkflowStepReadiness,
) -> workflow.WorkflowScientificGateEvaluation:
    plan_id = domain.new_workflow_plan_id()
    first_binding = domain.WorkflowStepBinding(
        workflow_plan_id=plan_id,
        step_key="dos",
        generation=1,
        calculation_id=anchored_calculation.id,
        resolved_input_structure_snapshot_id=anchored_calculation.input_structure_snapshot_id,
        materialization_reason="test",
    )
    current_binding = first_binding
    superseded_binding_ids: tuple[domain.WorkflowStepBindingId, ...] = ()
    superseded_calculation_ids: tuple[domain.CalculationId, ...] = ()
    if current_calculation.id != anchored_calculation.id:
        current_binding = domain.WorkflowStepBinding(
            workflow_plan_id=plan_id,
            step_key="dos",
            generation=2,
            calculation_id=current_calculation.id,
            resolved_input_structure_snapshot_id=current_calculation.input_structure_snapshot_id,
            materialization_reason="test supersession",
            supersedes_binding_id=first_binding.id,
        )
        superseded_binding_ids = (first_binding.id,)
        superseded_calculation_ids = (anchored_calculation.id,)
    selection = workflow.WorkflowBindingSelection(
        step_key="dos",
        current_binding=current_binding,
        current_calculation=current_calculation,
        superseded_binding_ids=superseded_binding_ids,
        superseded_calculation_ids=superseded_calculation_ids,
    )
    gate = workflow.WorkflowStepGate(
        step_key="dos",
        scientific_state=(
            workflow.WorkflowStepScientificState.PASSED
            if readiness is workflow.WorkflowStepReadiness.SATISFIED
            else workflow.WorkflowStepScientificState.IN_PROGRESS
        ),
        readiness=readiness,
        current_binding_id=current_binding.id,
        calculation_id=current_calculation.id,
        freshness_state=provenance.FreshnessState.FRESH,
    )
    return workflow.WorkflowScientificGateEvaluation(
        workflow_plan_id=plan_id,
        binding_selections=(selection,),
        step_gates=(gate,),
        edge_gates=(),
        superseded_calculation_ids=superseded_calculation_ids,
    )


def _calculation(project_id: domain.ProjectId, slug: str) -> domain.Calculation:
    return domain.Calculation(
        project_id=project_id,
        calculation_type=domain.CalculationType.DOS_STATIC,
        input_structure_snapshot_id=domain.new_structure_snapshot_id(),
        recipe_id="ECatVASP.VASP.DOSPrerequisite",
        method_fingerprint_id=domain.new_method_fingerprint_id(),
        status=domain.CalculationScientificStatus.CONVERGED,
        slug=slug,
    )


def test_workflow_anchor_never_silently_reanchors_to_new_generation() -> None:
    case = _case()
    anchored = _calculation(case.project.id, "old")
    current = _calculation(case.project.id, "current")
    requirement = replace(
        case.requirement,
        workflow_anchor=workflow.ElectronicWorkflowAnchor(
            step_key="dos",
            calculation_id=anchored.id,
        ),
    )
    gates = _workflow_projection(
        project_id=case.project.id,
        anchored_calculation=anchored,
        current_calculation=current,
        readiness=workflow.WorkflowStepReadiness.SATISFIED,
    )
    report = _reconcile(case, requirement=requirement, workflow_gates=gates)
    item = report.requirement("d-band-center")
    assert item.scientific_state is workflow.ElectronicAnalysisScientificState.SUPERSEDED
    assert item.readiness is workflow.WorkflowStepReadiness.BLOCKED
    assert item.workflow_calculation_id == anchored.id


def test_existing_completed_analysis_remains_completed_while_workflow_anchor_waits() -> None:
    case = _case()
    calculation = _calculation(case.project.id, "current")
    requirement = replace(
        case.requirement,
        workflow_anchor=workflow.ElectronicWorkflowAnchor(
            step_key="dos",
            calculation_id=calculation.id,
        ),
    )
    gates = _workflow_projection(
        project_id=case.project.id,
        anchored_calculation=calculation,
        current_calculation=calculation,
        readiness=workflow.WorkflowStepReadiness.WAITING,
    )
    report = _reconcile(case, requirement=requirement, workflow_gates=gates)
    item = report.requirement("d-band-center")
    assert item.scientific_state is workflow.ElectronicAnalysisScientificState.COMPLETED
    assert item.readiness is workflow.WorkflowStepReadiness.WAITING
    assert "workflow_step_not_satisfied" in item.reason_codes


def test_project_store_reopen_recomputes_identical_reconciliation_report(tmp_path: Path) -> None:
    case = _case()
    bundle = storage.ProjectBundle(
        project=case.project,
        artifacts=(case.input_artifact, case.output_artifact),
        analyses=(case.source_analysis, case.analysis),
        provenance_records=case.provenance_records,
        dependency_records=case.dependencies,
    )
    bundle.validate()
    store = storage.ProjectStore(tmp_path)
    store.save(bundle)

    direct = _reconcile(case)
    reopened = workflow.reconcile_electronic_analyses_from_store(
        store=store,
        requirements=(case.requirement,),
    )
    assert reopened == direct
    assert reopened.report_hash == direct.report_hash
    assert (
        reopened.requirement("d-band-center").readiness
        is workflow.WorkflowStepReadiness.SATISFIED
    )

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from ecatvasp.domain import (
    Analysis,
    AnalysisProducerRef,
    AnalysisStatus,
    AnalysisType,
    Artifact,
    ArtifactAvailability,
    ArtifactType,
    Calculation,
    CalculationScientificStatus,
    CalculationType,
    Project,
    RetrievalPolicy,
    WorkflowStepBinding,
    canonical_json,
    canonical_sha256,
)
from ecatvasp.domain.ids import (
    new_method_fingerprint_id,
    new_structure_snapshot_id,
    new_workflow_plan_id,
)
from ecatvasp.provenance import FreshnessState
from ecatvasp.storage import ProjectBundle, ProjectStore
from ecatvasp.thermo import (
    CANONICAL_HARMONIC_THERMOCHEMISTRY_FORMAT,
    CANONICAL_HARMONIC_THERMOCHEMISTRY_VERSION,
    CHEConditions,
    CHEPhSemantics,
    CHEProtonElectronChemicalPotential,
    CHEReactionSource,
    ElectronicEnergyKind,
    ElectronicEntropyPolicy,
    ElectrodePotentialReference,
    HARMONIC_THERMOCHEMISTRY_TOOL_NAME,
    HARMONIC_THERMOCHEMISTRY_TOOL_VERSION,
    ReactionPathwayDefinition,
    ReactionSourceArtifactBinding,
    ReactionStepDefinition,
    StoichiometricTerm,
    ThermochemicalConditions,
    ThermochemicalStandardState,
    ThermochemistryComponents,
    ThermochemistryIdentity,
    ThermochemistryReactionSource,
    ThermochemistryResult,
    ThermochemistrySubjectKind,
    define_limiting_potential_descriptor,
    evaluate_reaction_pathway,
    materialize_reaction_diagram,
    solve_limiting_potential,
)
from ecatvasp.workflow import (
    ThermochemistryAnalysisRequirement,
    ThermochemistryAnalysisScientificState,
    ThermochemistryWorkflowAnchor,
    WorkflowBindingSelection,
    WorkflowScientificGateEvaluation,
    WorkflowStepGate,
    WorkflowStepReadiness,
    WorkflowStepScientificState,
    reconcile_thermochemistry_analyses,
    reconcile_thermochemistry_analyses_from_store,
)


def _conditions(potential_v: float) -> CHEConditions:
    return CHEConditions(
        temperature_k=298.15,
        potential_v=potential_v,
        ph=0.0,
        potential_reference=ElectrodePotentialReference.RHE,
        ph_semantics=CHEPhSemantics.INCLUDED_IN_RHE,
    )


def _surface_result(energy_ev: float) -> ThermochemistryResult:
    return ThermochemistryResult(
        identity=ThermochemistryIdentity(
            subject_kind=ThermochemistrySubjectKind.SURFACE,
            conditions=ThermochemicalConditions(
                temperature_k=298.15,
                standard_state=ThermochemicalStandardState.SURFACE_FIXED_CELL,
            ),
            electronic_energy_kind=ElectronicEnergyKind.SIGMA_ZERO,
            electronic_entropy_policy=ElectronicEntropyPolicy.NEGLECTED,
            vibrational_policy=None,
        ),
        components=ThermochemistryComponents(electronic_energy_ev=energy_ev),
    )


def _che_source(conditions: CHEConditions) -> CHEReactionSource:
    return CHEReactionSource(
        species_key="che",
        result=CHEProtonElectronChemicalPotential(
            conditions=conditions,
            hydrogen_reference_hash="a" * 64,
            hydrogen_gibbs_free_energy_ev=0.0,
            half_h2_term_ev=0.0,
            potential_term_ev=-conditions.potential_v,
            ph_term_ev=0.0,
            chemical_potential_ev=-conditions.potential_v,
        ),
    )


def _pathway() -> ReactionPathwayDefinition:
    return ReactionPathwayDefinition(
        pathway_key="test-reduction",
        label="test reduction",
        state_keys=("clean", "ads"),
        steps=(
            ReactionStepDefinition(
                step_key="adsorb",
                label="adsorb",
                initial_state_key="clean",
                final_state_key="ads",
                terms=(
                    StoichiometricTerm("ads", 1.0),
                    StoichiometricTerm("clean", -1.0),
                    StoichiometricTerm("che", -1.0),
                ),
            ),
        ),
    )


def _durable_surface_source(
    *,
    root: Path,
    project: Project,
    species_key: str,
    energy_ev: float,
) -> tuple[ThermochemistryReactionSource, ReactionSourceArtifactBinding]:
    result = _surface_result(energy_ev)
    receipt = {
        "format": CANONICAL_HARMONIC_THERMOCHEMISTRY_FORMAT,
        "version": CANONICAL_HARMONIC_THERMOCHEMISTRY_VERSION,
        "test_species_key": species_key,
        "result_hash": result.result_hash,
    }
    receipt_hash = canonical_sha256(receipt)
    analysis = Analysis(
        project_id=project.id,
        analysis_type=AnalysisType.THERMOCHEMISTRY,
        input_artifact_ids=(),
        status=AnalysisStatus.COMPLETED,
        tool=HARMONIC_THERMOCHEMISTRY_TOOL_NAME,
        tool_version=HARMONIC_THERMOCHEMISTRY_TOOL_VERSION,
        parameters_hash=receipt_hash,
    )
    payload = {
        "format": CANONICAL_HARMONIC_THERMOCHEMISTRY_FORMAT,
        "version": CANONICAL_HARMONIC_THERMOCHEMISTRY_VERSION,
        "analysis_id": analysis.id,
        "source_receipt": receipt,
        "source_receipt_hash": receipt_hash,
        "result_hash": result.result_hash,
        "result": result,
    }
    relative = Path("analyses") / str(analysis.id) / f"{species_key}.json"
    body = (canonical_json(payload) + "\n").encode()
    absolute = root / relative
    absolute.parent.mkdir(parents=True, exist_ok=True)
    absolute.write_bytes(body)
    artifact = Artifact(
        artifact_type=ArtifactType.DERIVED_DATASET,
        producer=AnalysisProducerRef(analysis.id),
        availability=ArtifactAvailability.LOCAL,
        retrieval_policy=RetrievalPolicy.ALWAYS,
        local_path=relative.as_posix(),
        size_bytes=len(body),
        sha256=hashlib.sha256(body).hexdigest(),
    )
    source = ThermochemistryReactionSource(species_key=species_key, result=result)
    return source, ReactionSourceArtifactBinding(
        species_key=species_key,
        analysis=analysis,
        artifact=artifact,
    )


def _materialized_case(tmp_path: Path) -> tuple[
    Project,
    tuple[ThermochemistryReactionSource | CHEReactionSource, ...],
    tuple[ReactionSourceArtifactBinding, ...],
    object,
]:
    project = Project(name="reaction diagram", slug="reaction-diagram")
    baseline_conditions = _conditions(0.0)
    clean, clean_binding = _durable_surface_source(
        root=tmp_path,
        project=project,
        species_key="clean",
        energy_ev=0.0,
    )
    ads, ads_binding = _durable_surface_source(
        root=tmp_path,
        project=project,
        species_key="ads",
        energy_ev=0.4,
    )
    sources = (clean, ads, _che_source(baseline_conditions))
    baseline = evaluate_reaction_pathway(definition=_pathway(), sources=sources)
    limiting = solve_limiting_potential(
        baseline_pathway=baseline,
        baseline_conditions=baseline_conditions,
    )
    descriptor = define_limiting_potential_descriptor(key="u_lim", result=limiting)
    durable = materialize_reaction_diagram(
        project_root=tmp_path,
        definition=_pathway(),
        sources=sources,
        source_bindings=(clean_binding, ads_binding),
        baseline_conditions=baseline_conditions,
        requested_conditions=_conditions(-0.2),
        descriptor_definitions=(descriptor,),
    )
    return project, sources, (clean_binding, ads_binding), durable


def test_reaction_diagram_materializes_verified_requested_condition_dataset(
    tmp_path: Path,
) -> None:
    project, _, bindings, durable = _materialized_case(tmp_path)

    assert durable.analysis.project_id == project.id
    assert durable.analysis.analysis_type is AnalysisType.REACTION_DIAGRAM
    assert durable.dataset.state_keys == ("clean", "ads")
    assert durable.dataset.step_delta_g_ev == pytest.approx((0.2,))
    assert durable.dataset.cumulative_state_free_energies_ev == pytest.approx((0.0, 0.2))
    assert durable.dataset.descriptor_definitions[0].definition_hash
    assert durable.dataset.descriptor_definitions[0].value == pytest.approx(-0.4)
    assert {item.upstream_id for item in durable.dependency_records} >= {
        bindings[0].analysis.id,
        bindings[0].artifact.id,
        bindings[1].analysis.id,
        bindings[1].artifact.id,
        durable.analysis.id,
    }


def test_source_receipt_must_match_bound_analysis_identity(tmp_path: Path) -> None:
    project = Project(name="receipt", slug="receipt")
    clean, binding = _durable_surface_source(
        root=tmp_path,
        project=project,
        species_key="clean",
        energy_ev=0.0,
    )
    ads, ads_binding = _durable_surface_source(
        root=tmp_path,
        project=project,
        species_key="ads",
        energy_ev=0.4,
    )
    altered = ReactionSourceArtifactBinding(
        species_key="clean",
        analysis=replace(binding.analysis, parameters_hash="f" * 64),
        artifact=binding.artifact,
    )
    with pytest.raises(Exception, match="receipt differs from Analysis parameters_hash"):
        materialize_reaction_diagram(
            project_root=tmp_path,
            definition=_pathway(),
            sources=(clean, ads, _che_source(_conditions(0.0))),
            source_bindings=(altered, ads_binding),
            baseline_conditions=_conditions(0.0),
            requested_conditions=_conditions(-0.2),
        )


def test_reopen_reconciliation_propagates_source_hash_drift(tmp_path: Path) -> None:
    project, _, bindings, durable = _materialized_case(tmp_path)
    source_analyses = tuple(item.analysis for item in bindings)
    source_artifacts = tuple(item.artifact for item in bindings)
    bundle = ProjectBundle(
        project=project,
        artifacts=(*source_artifacts, durable.artifact),
        analyses=(*source_analyses, durable.analysis),
        provenance_records=durable.provenance_records,
        dependency_records=durable.dependency_records,
    )
    store = ProjectStore(tmp_path)
    store.save(bundle)
    requirement = ThermochemistryAnalysisRequirement(
        key="diagram",
        project_id=project.id,
        analysis_type=AnalysisType.REACTION_DIAGRAM,
        input_artifact_ids=durable.analysis.input_artifact_ids,
        parameters_hash=durable.analysis.parameters_hash or "0" * 64,
    )

    first = reconcile_thermochemistry_analyses_from_store(
        store=store,
        requirements=(requirement,),
    ).requirement("diagram")
    assert first.scientific_state is ThermochemistryAnalysisScientificState.COMPLETED
    assert first.readiness is WorkflowStepReadiness.SATISFIED

    drifted = reconcile_thermochemistry_analyses_from_store(
        store=store,
        requirements=(requirement,),
        current_hash_overrides={source_artifacts[0].id: "f" * 64},
    ).requirement("diagram")
    assert drifted.scientific_state is ThermochemistryAnalysisScientificState.STALE
    assert drifted.readiness is WorkflowStepReadiness.BLOCKED
    assert "scientific_hash_changed" in drifted.reason_codes


def _workflow_gate_evidence(
    *,
    project: Project,
    second_calculation: Calculation | None = None,
) -> tuple[
    WorkflowScientificGateEvaluation,
    Calculation,
    Calculation,
]:
    plan_id = new_workflow_plan_id()
    snapshot_id = new_structure_snapshot_id()
    method_id = new_method_fingerprint_id()
    first = Calculation(
        project_id=project.id,
        calculation_type=CalculationType.FREQUENCY,
        input_structure_snapshot_id=snapshot_id,
        recipe_id="test.frequency.a",
        method_fingerprint_id=method_id,
        status=CalculationScientificStatus.CONVERGED,
    )
    second = second_calculation or Calculation(
        project_id=project.id,
        calculation_type=CalculationType.FREQUENCY,
        input_structure_snapshot_id=snapshot_id,
        recipe_id="test.frequency.b",
        method_fingerprint_id=method_id,
        status=CalculationScientificStatus.CONVERGED,
    )
    binding_a = WorkflowStepBinding(
        workflow_plan_id=plan_id,
        step_key="freq-a",
        generation=1,
        calculation_id=first.id,
        resolved_input_structure_snapshot_id=snapshot_id,
        materialization_reason="test",
    )
    binding_b = WorkflowStepBinding(
        workflow_plan_id=plan_id,
        step_key="freq-b",
        generation=1,
        calculation_id=second.id,
        resolved_input_structure_snapshot_id=snapshot_id,
        materialization_reason="test",
    )
    selections = (
        WorkflowBindingSelection("freq-a", binding_a, first),
        WorkflowBindingSelection("freq-b", binding_b, second),
    )
    gates = tuple(
        WorkflowStepGate(
            step_key=item.step_key,
            scientific_state=WorkflowStepScientificState.PASSED,
            readiness=WorkflowStepReadiness.SATISFIED,
            current_binding_id=item.current_binding.id if item.current_binding else None,
            calculation_id=item.current_calculation.id if item.current_calculation else None,
            freshness_state=FreshnessState.FRESH,
        )
        for item in selections
    )
    return (
        WorkflowScientificGateEvaluation(
            workflow_plan_id=plan_id,
            binding_selections=selections,
            step_gates=gates,
            edge_gates=(),
        ),
        first,
        second,
    )


def test_multi_anchor_workflow_gate_blocks_historical_generation(tmp_path: Path) -> None:
    project, _, bindings, durable = _materialized_case(tmp_path)
    workflow_gates, first, second = _workflow_gate_evidence(project=project)
    requirement = ThermochemistryAnalysisRequirement(
        key="diagram",
        project_id=project.id,
        analysis_type=AnalysisType.REACTION_DIAGRAM,
        input_artifact_ids=durable.analysis.input_artifact_ids,
        parameters_hash=durable.analysis.parameters_hash or "0" * 64,
        workflow_anchors=(
            ThermochemistryWorkflowAnchor("freq-a", first.id),
            ThermochemistryWorkflowAnchor("freq-b", second.id),
        ),
    )
    artifacts = (*tuple(item.artifact for item in bindings), durable.artifact)
    analyses = (*tuple(item.analysis for item in bindings), durable.analysis)
    current = reconcile_thermochemistry_analyses(
        requirements=(requirement,),
        analyses=analyses,
        artifacts=artifacts,
        dependencies=durable.dependency_records,
        workflow_gates=workflow_gates,
    ).requirement("diagram")
    assert current.readiness is WorkflowStepReadiness.SATISFIED

    replacement = replace(second, id=Calculation(
        project_id=project.id,
        calculation_type=CalculationType.FREQUENCY,
        input_structure_snapshot_id=second.input_structure_snapshot_id,
        recipe_id=second.recipe_id,
        method_fingerprint_id=second.method_fingerprint_id,
    ).id)
    superseding_gates, _, _ = _workflow_gate_evidence(
        project=project,
        second_calculation=replacement,
    )
    historical = reconcile_thermochemistry_analyses(
        requirements=(requirement,),
        analyses=analyses,
        artifacts=artifacts,
        dependencies=durable.dependency_records,
        workflow_gates=superseding_gates,
    ).requirement("diagram")
    assert historical.scientific_state is ThermochemistryAnalysisScientificState.SUPERSEDED
    assert historical.readiness is WorkflowStepReadiness.BLOCKED

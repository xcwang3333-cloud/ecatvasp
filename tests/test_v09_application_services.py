from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import cast

import pytest

import ecatvasp.api.application as application_module
from ecatvasp.api import ApplicationReportFormat, ApplicationServiceError, ProjectApplicationService
from ecatvasp.domain import (
    Artifact,
    ArtifactAvailability,
    ArtifactId,
    ArtifactType,
    Calculation,
    CalculationId,
    CalculationScientificStatus,
    CalculationType,
    Catalyst,
    ExecutionAttempt,
    ExecutionAttemptProducerRef,
    ExecutionAttemptStatus,
    ExecutionSettings,
    KPointPolicy,
    KPointPolicyKind,
    Lattice,
    MethodDefinition,
    MethodFingerprint,
    PotcarIdentity,
    Project,
    ProtocolDefinition,
    RecipeIdentity,
    RetrievalPolicy,
    SpinTreatment,
    StructureOrigin,
    StructureSite,
    StructureSnapshot,
    StructureVariant,
    VariantType,
    WorkflowRecipeIdentity,
    canonical_sha256,
    new_artifact_id,
    new_atom_uid,
    new_execution_attempt_id,
)
from ecatvasp.execution.batch import (
    BatchConcurrencyPolicy,
    SchedulerDag,
    SchedulerDagNode,
)
from ecatvasp.provenance import (
    DependencyKind,
    DependencyRecord,
    ProvenanceRecord,
    scientific_hash,
)
from ecatvasp.schema.version import SCHEMA_VERSION
from ecatvasp.storage import ProjectBundle, ProjectStore
from ecatvasp.vasp import (
    ConvergenceVerdict,
    ExecutionPlan,
    PotcarResolutionEntry,
    PotcarResolutionRequest,
    ProjectNumericalLock,
    VaspConvergenceAssessment,
    VaspConvergenceEvidence,
    VaspEnergySummary,
    VaspResultArtifactIntake,
    VaspResultDocument,
    VaspResultInputFile,
    VaspResultSource,
    VaspResultSourceRole,
    VaspRuntimeConstraints,
    VaspScientificResultIntake,
    VaspStructurePromotionError,
    VaspSystemContext,
    VaspSystemKind,
)
from ecatvasp.vasp.contracts import LatticeAxis
from ecatvasp.vasp.structure_promotion import (
    VaspContcarReconstruction,
    VaspStructurePromotionResult,
)
from ecatvasp.vasp.structure_provenance import VaspContcarReconstructionProvenance
from ecatvasp.workflow import (
    WORKFLOW_RECIPE_SLAB_SCIENTIFIC_PREPARATION,
    WorkflowOrchestrationAction,
    WorkflowOrchestrationEvaluation,
    WorkflowStepOrchestration,
)


def _snapshot(*, parent: StructureSnapshot | None = None) -> StructureSnapshot:
    return StructureSnapshot(
        lattice=Lattice(
            vectors=((3.0, 0.0, 0.0), (0.0, 3.0, 0.0), (0.0, 0.0, 15.0))
        ),
        sites=(
            StructureSite(
                atom_uid=new_atom_uid(),
                element="C",
                fractional_coords=(0.1, 0.2, 0.3),
            ),
        ),
        origin=StructureOrigin.IMPORTED if parent is None else StructureOrigin.RELAXED,
        parent_snapshot_id=None if parent is None else parent.id,
    )


def _fingerprint(*, recipe_id: str = "ECatVASP.VASP.SlabRelax") -> MethodFingerprint:
    return MethodFingerprint(
        method=MethodDefinition(
            xc_functional="PBE",
            potcar_family="PBE_54",
            potcars=(PotcarIdentity("C", "C", "a" * 64),),
            spin_treatment=SpinTreatment.UNPOLARIZED,
        ),
        protocol=ProtocolDefinition(
            encut_ev=500.0,
            kpoints=KPointPolicy(KPointPolicyKind.GAMMA_ONLY),
        ),
        recipe=RecipeIdentity(recipe_id=recipe_id, version="1"),
    )


def _context() -> VaspSystemContext:
    return VaspSystemContext(kind=VaspSystemKind.SLAB_2D, vacuum_axis=LatticeAxis.C)


def _lock(project: Project, fingerprint: MethodFingerprint) -> ProjectNumericalLock:
    return ProjectNumericalLock(
        project_id=project.id,
        system_kind=VaspSystemKind.SLAB_2D,
        core_method_hash=fingerprint.core_method_hash,
        encut_ev=fingerprint.protocol.encut_ev,
        encut_validation_hash="b" * 64,
        kpoints=fingerprint.protocol.kpoints,
        kpoints_validation_hash="c" * 64,
    )


def _materialization_orchestration(*, plan_id, root_id) -> WorkflowOrchestrationEvaluation:
    return WorkflowOrchestrationEvaluation(
        workflow_plan_id=plan_id,
        step_handoffs=(
            WorkflowStepOrchestration(
                step_key="relax",
                action=WorkflowOrchestrationAction.MATERIALIZE_STEP,
                target_input_structure_snapshot_id=root_id,
                reason_codes=("application_ready_materialization",),
            ),
        ),
    )


def _execution_plan(calculation: Calculation) -> ExecutionPlan:
    return ExecutionPlan(
        calculation_id=calculation.id,
        recipe_id=calculation.recipe_id,
        system_context=_context(),
        input_manifest_artifact_id=new_artifact_id(),
        input_manifest_sha256="d" * 64,
        preparation_hash="e" * 64,
        staging_inputs=(),
        potcar_resolution=PotcarResolutionRequest(
            family="PBE_54",
            core_method_hash="f" * 64,
            metadata_hash="1" * 64,
            entries=(PotcarResolutionEntry("C", "C", "2" * 64),),
        ),
        expected_outputs=(),
        runtime_constraints=VaspRuntimeConstraints(),
        execution_settings=ExecutionSettings(),
    )


def _execution_orchestration(
    *,
    plan_id,
    binding,
    calculation: Calculation,
    execution_plan: ExecutionPlan,
) -> WorkflowOrchestrationEvaluation:
    node_id = f"workflow-relax-g{binding.generation}"
    node = SchedulerDagNode(
        node_id=node_id,
        calculation=calculation,
        plan=execution_plan,
    )
    return WorkflowOrchestrationEvaluation(
        workflow_plan_id=plan_id,
        step_handoffs=(
            WorkflowStepOrchestration(
                step_key="relax",
                action=WorkflowOrchestrationAction.EXECUTION_READY,
                current_binding_id=binding.id,
                calculation_id=calculation.id,
                execution_plan_hash=execution_plan.plan_hash,
                scheduler_node_id=node_id,
                reason_codes=("application_execution_ready",),
            ),
        ),
        scheduler_dag=SchedulerDag(nodes=(node,)),
    )


def _workflow_store(tmp_path: Path) -> tuple[Project, StructureSnapshot, MethodFingerprint, ProjectStore]:
    project = Project(name="Application workflow", slug="application-workflow")
    root = _snapshot()
    fingerprint = _fingerprint()
    store = ProjectStore(tmp_path)
    store.save(
        ProjectBundle(
            project=project,
            structure_snapshots=(root,),
            method_fingerprints=(fingerprint,),
        )
    )
    return project, root, fingerprint, store


def test_inspect_and_report_reopen_current_project_state(tmp_path: Path) -> None:
    project, root, fingerprint, store = _workflow_store(tmp_path)
    service = ProjectApplicationService(store)

    first = service.inspect()
    first_json = service.report(format=ApplicationReportFormat.JSON)
    repeated_json = service.report(format="json")

    assert first.projection.project_id == project.id
    assert first.projection.entity_counts.structure_snapshots == 1
    assert first.projection.entity_counts.method_fingerprints == 1
    assert first_json.content == repeated_json.content
    assert first_json.report.report_hash == repeated_json.report.report_hash
    assert '"contract_version": "ecatvasp-scientific-report-v1"' in first_json.content

    newer = _snapshot()
    store.save(replace(store.open(), structure_snapshots=(root, newer)))
    second = service.inspect()

    assert second.projection.entity_counts.structure_snapshots == 2
    assert second.projection.projection_hash != first.projection.projection_hash
    assert fingerprint.id in {item.id for item in store.open().method_fingerprints}
    assert SCHEMA_VERSION == 3


def test_prepare_materialize_and_run_are_durable_and_replay_safe(tmp_path: Path) -> None:
    project, root, fingerprint, store = _workflow_store(tmp_path)
    service = ProjectApplicationService(store)
    recipe = WorkflowRecipeIdentity(WORKFLOW_RECIPE_SLAB_SCIENTIFIC_PREPARATION)

    first_plan = service.prepare_workflow(
        workflow_recipe=recipe,
        root_structure_snapshot_id=root.id,
    )
    repeated_plan = service.prepare_workflow(
        workflow_recipe=recipe,
        root_structure_snapshot_id=root.id,
    )

    assert not first_plan.reused
    assert repeated_plan.reused
    assert repeated_plan.plan.id == first_plan.plan.id
    assert len(store.open().workflow_plans) == 1

    orchestration = _materialization_orchestration(
        plan_id=first_plan.plan.id,
        root_id=root.id,
    )
    first_step = service.prepare_workflow_step(
        workflow_plan_id=first_plan.plan.id,
        orchestration=orchestration,
        step_key="relax",
        method_fingerprint_id=fingerprint.id,
        system_context=_context(),
        project_lock=_lock(project, fingerprint),
    )
    repeated_step = service.prepare_workflow_step(
        workflow_plan_id=first_plan.plan.id,
        orchestration=orchestration,
        step_key="relax",
        method_fingerprint_id=fingerprint.id,
        system_context=_context(),
        project_lock=_lock(project, fingerprint),
    )

    assert not first_step.reused
    assert repeated_step.reused
    assert repeated_step.materialization.binding.id == first_step.materialization.binding.id
    assert len(store.open().calculations) == 1

    materialized = first_step.materialization
    execution_plan = _execution_plan(materialized.calculation)
    execution = _execution_orchestration(
        plan_id=first_plan.plan.id,
        binding=materialized.binding,
        calculation=materialized.calculation,
        execution_plan=execution_plan,
    )
    first_run = service.run_workflow(
        workflow_plan_id=first_plan.plan.id,
        orchestration=execution,
        concurrency=BatchConcurrencyPolicy(max_active=1),
    )
    repeated_run = service.run_workflow(
        workflow_plan_id=first_plan.plan.id,
        orchestration=execution,
        concurrency=BatchConcurrencyPolicy(max_active=1),
    )

    assert len(first_run.newly_persisted_attempt_ids) == 1
    assert repeated_run.newly_persisted_attempt_ids == ()
    reopened = store.open()
    assert len(reopened.execution_attempts) == 1
    assert reopened.remote_jobs == ()
    assert reopened.calculations[0].status is CalculationScientificStatus.READY


@dataclass(frozen=True, slots=True)
class _ScientificIntake:
    calculation_id: CalculationId
    calculation_type: CalculationType
    recipe_id: str
    files: tuple[VaspResultInputFile, ...]
    intake_hash: str = field(default_factory=lambda: "e" * 64)

    @property
    def sources(self) -> tuple[VaspResultSource, ...]:
        return tuple(item.source for item in self.files)

    @property
    def input_artifact_ids(self) -> tuple[ArtifactId, ...]:
        return tuple(item.source.artifact_id for item in self.files)


def test_analyze_persists_only_preclassified_vasp_scientific_result(tmp_path: Path) -> None:
    project = Project(name="Application analysis", slug="application-analysis")
    snapshot = _snapshot()
    fingerprint = _fingerprint(recipe_id="ECatVASP.VASP.GroundStateStatic")
    calculation = Calculation(
        project_id=project.id,
        calculation_type=CalculationType.STATIC,
        input_structure_snapshot_id=snapshot.id,
        recipe_id=fingerprint.recipe.recipe_id,
        method_fingerprint_id=fingerprint.id,
        slug="application-static",
    )
    attempt = ExecutionAttempt(
        calculation_id=calculation.id,
        attempt_number=1,
        status=ExecutionAttemptStatus.PARSED,
    )
    outcar = Artifact(
        artifact_type=ArtifactType.OUTCAR,
        producer=ExecutionAttemptProducerRef(attempt.id),
        availability=ArtifactAvailability.LOCAL,
        retrieval_policy=RetrievalPolicy.ALWAYS,
        local_path="outputs/OUTCAR",
        size_bytes=10,
        sha256="a" * 64,
    )
    source = VaspResultSource(
        role=VaspResultSourceRole.OUTCAR,
        artifact_id=outcar.id,
        artifact_type=outcar.artifact_type,
        sha256=outcar.sha256 or "",
    )
    intake = _ScientificIntake(
        calculation_id=calculation.id,
        calculation_type=calculation.calculation_type,
        recipe_id=calculation.recipe_id,
        files=(
            VaspResultInputFile(
                source=source,
                expected_output_path="OUTCAR",
                local_relative_path="outputs/OUTCAR",
                size_bytes=10,
                retrieval_policy=RetrievalPolicy.ALWAYS,
            ),
        ),
        intake_hash=canonical_sha256({"case": "application-analysis"}),
    )
    result = VaspResultDocument(
        calculation_type=CalculationType.STATIC,
        sources=intake.sources,
        energies=VaspEnergySummary(free_energy_toten_ev=-10.0),
        termination_observed=True,
    )
    assessment = VaspConvergenceAssessment(
        calculation_type=CalculationType.STATIC,
        electronic=ConvergenceVerdict.CONVERGED,
        ionic=ConvergenceVerdict.NOT_APPLICABLE,
        overall=ConvergenceVerdict.CONVERGED,
        evidence_codes=("application.preclassified",),
    )
    store = ProjectStore(tmp_path)
    store.save(
        ProjectBundle(
            project=project,
            structure_snapshots=(snapshot,),
            method_fingerprints=(fingerprint,),
            calculations=(calculation,),
            execution_attempts=(attempt,),
            artifacts=(outcar,),
        )
    )

    receipt = ProjectApplicationService(store).analyze_vasp_result(
        calculation_id=calculation.id,
        intake=cast(VaspScientificResultIntake, intake),
        result=result,
        assessment=assessment,
    )
    reopened = store.open()

    assert receipt.project_id == project.id
    assert reopened.calculations[0].status is CalculationScientificStatus.CONVERGED
    assert len(reopened.analyses) == 2
    assert len(reopened.artifacts) == 3
    assert len(reopened.provenance_records) == 4
    assert receipt.materialization.result_parse_analysis in reopened.analyses
    assert receipt.materialization.convergence_analysis in reopened.analyses


def test_uid_bound_analysis_fails_before_materialization_without_execution_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, root, fingerprint, store = _workflow_store(tmp_path)
    calculation = Calculation(
        project_id=project.id,
        calculation_type=CalculationType.RELAX,
        input_structure_snapshot_id=root.id,
        recipe_id=fingerprint.recipe.recipe_id,
        method_fingerprint_id=fingerprint.id,
    )
    store.save(replace(store.open(), calculations=(calculation,)))
    called = False

    def _unexpected_materialization(**kwargs):
        nonlocal called
        called = True
        raise AssertionError(kwargs)

    monkeypatch.setattr(
        application_module,
        "materialize_vasp_scientific_result",
        _unexpected_materialization,
    )
    uid_result = cast(
        VaspResultDocument,
        type(
            "UidResult",
            (),
            {"forces": object(), "magnetization": None, "frequencies": None},
        )(),
    )

    with pytest.raises(ApplicationServiceError, match="ExecutionPlan"):
        ProjectApplicationService(store).analyze_vasp_result(
            calculation_id=calculation.id,
            intake=cast(VaspScientificResultIntake, object()),
            result=uid_result,
            assessment=cast(VaspConvergenceAssessment, object()),
        )
    assert not called


def test_promote_persists_exact_lower_layer_receipts_and_failure_does_not_mutate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = Project(name="Application promotion", slug="application-promotion")
    catalyst = Catalyst(project_id=project.id, name="model", slug="model")
    root = _snapshot()
    variant = StructureVariant(
        catalyst_id=catalyst.id,
        name="model variant",
        variant_type=VariantType.GEOMETRY,
        current_structure_snapshot_id=root.id,
    )
    fingerprint = _fingerprint()
    calculation = Calculation(
        project_id=project.id,
        calculation_type=CalculationType.RELAX,
        input_structure_snapshot_id=root.id,
        recipe_id=fingerprint.recipe.recipe_id,
        method_fingerprint_id=fingerprint.id,
    )
    store = ProjectStore(tmp_path)
    store.save(
        ProjectBundle(
            project=project,
            catalysts=(catalyst,),
            structure_variants=(variant,),
            structure_snapshots=(root,),
            method_fingerprints=(fingerprint,),
            calculations=(calculation,),
        )
    )
    candidate = StructureSnapshot(
        lattice=root.lattice,
        sites=root.sites,
        label="promoted",
        origin=StructureOrigin.RELAXED,
        parent_snapshot_id=root.id,
        periodic=root.periodic,
    )
    reconstruction = VaspContcarReconstruction(
        calculation_id=calculation.id,
        intake_hash="1" * 64,
        attempt_id=new_execution_attempt_id(),
        source_artifact_id=new_artifact_id(),
        source_sha256="2" * 64,
        input_snapshot_id=root.id,
        snapshot=candidate,
    )
    convergence = VaspConvergenceAssessment(
        calculation_type=CalculationType.RELAX,
        electronic=ConvergenceVerdict.CONVERGED,
        ionic=ConvergenceVerdict.CONVERGED,
        overall=ConvergenceVerdict.CONVERGED,
        evidence_codes=("application.promoted",),
    )
    promotion = VaspStructurePromotionResult(
        updated_variant=replace(variant, current_structure_snapshot_id=candidate.id),
        snapshot=candidate,
        convergence=convergence,
    )
    provenance = VaspContcarReconstructionProvenance(
        provenance_record=ProvenanceRecord(
            subject_id=candidate.id,
            tool="application-test-reconstructor",
            tool_version="1",
        ),
        dependency_records=(
            DependencyRecord(
                upstream_id=root.id,
                downstream_id=candidate.id,
                kind=DependencyKind.SCIENTIFIC,
                role="input_structure_identity",
                recorded_hash=scientific_hash(root),
            ),
        ),
    )
    monkeypatch.setattr(
        application_module,
        "reconstruct_vasp_contcar_snapshot",
        lambda **kwargs: reconstruction,
    )
    monkeypatch.setattr(
        application_module,
        "build_vasp_contcar_reconstruction_provenance",
        lambda **kwargs: provenance,
    )
    monkeypatch.setattr(
        application_module,
        "promote_vasp_contcar_snapshot",
        lambda **kwargs: promotion,
    )
    service = ProjectApplicationService(store)
    receipt = service.promote_vasp_structure(
        structure_variant_id=variant.id,
        calculation_id=calculation.id,
        method_fingerprint_id=fingerprint.id,
        execution_plan=cast(ExecutionPlan, object()),
        intake=cast(VaspResultArtifactIntake, object()),
        evidence=cast(VaspConvergenceEvidence, object()),
    )
    reopened = store.open()

    assert receipt.promotion == promotion
    assert reopened.structure_variants[0].current_structure_snapshot_id == candidate.id
    assert candidate in reopened.structure_snapshots
    assert provenance.provenance_record in reopened.provenance_records
    assert provenance.dependency_records[0] in reopened.dependency_records

    before = reopened
    monkeypatch.setattr(
        application_module,
        "promote_vasp_contcar_snapshot",
        lambda **kwargs: (_ for _ in ()).throw(
            VaspStructurePromotionError("scientifically converged promotion required")
        ),
    )
    with pytest.raises(VaspStructurePromotionError, match="scientifically converged"):
        service.promote_vasp_structure(
            structure_variant_id=variant.id,
            calculation_id=calculation.id,
            method_fingerprint_id=fingerprint.id,
            execution_plan=cast(ExecutionPlan, object()),
            intake=cast(VaspResultArtifactIntake, object()),
            evidence=cast(VaspConvergenceEvidence, object()),
        )
    assert store.open() == before
    assert SCHEMA_VERSION == 3

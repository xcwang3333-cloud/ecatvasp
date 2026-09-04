from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from ecatvasp.domain import (
    AnalysisProducerRef,
    AnalysisType,
    Artifact,
    ArtifactAvailability,
    ArtifactType,
    Calculation,
    CalculationScientificStatus,
    CalculationType,
    ExecutionAttempt,
    ExecutionAttemptProducerRef,
    ExecutionAttemptStatus,
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
    StructureSite,
    StructureSnapshot,
    canonical_sha256,
    new_atom_uid,
)
from ecatvasp.domain.ids import ArtifactId, CalculationId
from ecatvasp.provenance import FreshnessEngine, FreshnessState, scientific_hash
from ecatvasp.schema.version import SCHEMA_VERSION
from ecatvasp.storage import ProjectBundle, ProjectStore
from ecatvasp.vasp.result_intake import VaspResultInputFile
from ecatvasp.vasp.result_provenance import (
    VASP_CONVERGENCE_ARTIFACT_FORMAT,
    VASP_SCIENTIFIC_RESULT_PIPELINE_NAME,
    materialize_vasp_scientific_result,
    reconcile_vasp_calculation_status,
)
from ecatvasp.vasp.results import (
    ConvergenceVerdict,
    VaspConvergenceAssessment,
    VaspEnergySummary,
    VaspResultDocument,
    VaspResultSource,
    VaspResultSourceRole,
)


@dataclass(frozen=True, slots=True)
class _Intake:
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


@dataclass(frozen=True, slots=True)
class _Case:
    project: Project
    snapshot: StructureSnapshot
    fingerprint: MethodFingerprint
    calculation: Calculation
    attempt: ExecutionAttempt
    raw_artifacts: tuple[Artifact, ...]
    intake: _Intake
    result: VaspResultDocument
    assessment: VaspConvergenceAssessment


def _case() -> _Case:
    project = Project(name="Result provenance", slug="result-provenance")
    snapshot = StructureSnapshot(
        lattice=Lattice(
            vectors=((8.0, 0.0, 0.0), (0.0, 8.0, 0.0), (0.0, 0.0, 16.0))
        ),
        sites=(StructureSite(new_atom_uid(), "C", (0.5, 0.5, 0.5)),),
    )
    fingerprint = MethodFingerprint(
        method=MethodDefinition(
            xc_functional="PBE",
            potcar_family="PBE_54",
            potcars=(PotcarIdentity("C", "C", "c" * 64),),
            dispersion_model="NONE",
            spin_treatment=SpinTreatment.UNPOLARIZED,
        ),
        protocol=ProtocolDefinition(
            encut_ev=450.0,
            kpoints=KPointPolicy(KPointPolicyKind.GAMMA_ONLY),
        ),
        recipe=RecipeIdentity("ECatVASP.VASP.GroundStateStatic"),
    )
    calculation = Calculation(
        project_id=project.id,
        calculation_type=CalculationType.STATIC,
        input_structure_snapshot_id=snapshot.id,
        recipe_id=fingerprint.recipe.recipe_id,
        method_fingerprint_id=fingerprint.id,
        slug="result-provenance",
    )
    attempt = ExecutionAttempt(
        calculation_id=calculation.id,
        attempt_number=1,
        status=ExecutionAttemptStatus.PARSED,
    )
    raw_artifacts = (
        Artifact(
            artifact_type=ArtifactType.OUTCAR,
            producer=ExecutionAttemptProducerRef(attempt.id),
            availability=ArtifactAvailability.LOCAL,
            retrieval_policy=RetrievalPolicy.ALWAYS,
            local_path="outputs/OUTCAR",
            size_bytes=10,
            sha256="a" * 64,
        ),
        Artifact(
            artifact_type=ArtifactType.OSZICAR,
            producer=ExecutionAttemptProducerRef(attempt.id),
            availability=ArtifactAvailability.LOCAL,
            retrieval_policy=RetrievalPolicy.ALWAYS,
            local_path="outputs/OSZICAR",
            size_bytes=10,
            sha256="b" * 64,
        ),
    )
    files = tuple(
        VaspResultInputFile(
            source=VaspResultSource(
                role=(
                    VaspResultSourceRole.OUTCAR
                    if artifact.artifact_type is ArtifactType.OUTCAR
                    else VaspResultSourceRole.OSZICAR
                ),
                artifact_id=artifact.id,
                artifact_type=artifact.artifact_type,
                sha256=artifact.sha256 or "",
            ),
            expected_output_path=(
                "OUTCAR" if artifact.artifact_type is ArtifactType.OUTCAR else "OSZICAR"
            ),
            local_relative_path=(
                "outputs/OUTCAR"
                if artifact.artifact_type is ArtifactType.OUTCAR
                else "outputs/OSZICAR"
            ),
            size_bytes=artifact.size_bytes or 0,
            retrieval_policy=artifact.retrieval_policy,
        )
        for artifact in raw_artifacts
    )
    intake = _Intake(
        calculation_id=calculation.id,
        calculation_type=calculation.calculation_type,
        recipe_id=calculation.recipe_id,
        files=files,
        intake_hash=canonical_sha256({"case": "result-provenance"}),
    )
    result = VaspResultDocument(
        calculation_type=calculation.calculation_type,
        sources=intake.sources,
        energies=VaspEnergySummary(free_energy_toten_ev=-10.0),
        termination_observed=True,
    )
    assessment = VaspConvergenceAssessment(
        calculation_type=calculation.calculation_type,
        electronic=ConvergenceVerdict.CONVERGED,
        ionic=ConvergenceVerdict.NOT_APPLICABLE,
        overall=ConvergenceVerdict.CONVERGED,
        evidence_codes=("test.converged",),
    )
    return _Case(
        project=project,
        snapshot=snapshot,
        fingerprint=fingerprint,
        calculation=calculation,
        attempt=attempt,
        raw_artifacts=raw_artifacts,
        intake=intake,
        result=result,
        assessment=assessment,
    )


def test_result_materialization_builds_durable_analysis_artifact_chain(
    tmp_path: Path,
) -> None:
    case = _case()
    materialized = materialize_vasp_scientific_result(
        project_root=tmp_path,
        calculation=case.calculation,
        intake=case.intake,
        result=case.result,
        assessment=case.assessment,
    )

    assert materialized.updated_calculation.status is CalculationScientificStatus.CONVERGED
    assert materialized.result_parse_analysis.analysis_type is AnalysisType.RESULT_PARSE
    assert materialized.convergence_analysis.analysis_type is AnalysisType.CONVERGENCE
    assert materialized.result_parse_analysis.tool == VASP_SCIENTIFIC_RESULT_PIPELINE_NAME
    assert materialized.parsed_result_artifact.artifact_type is ArtifactType.PARSED_RESULT
    assert isinstance(materialized.parsed_result_artifact.producer, AnalysisProducerRef)
    assert materialized.parsed_result_artifact.producer.id == materialized.result_parse_analysis.id
    assert isinstance(materialized.convergence_artifact.producer, AnalysisProducerRef)
    assert materialized.convergence_artifact.producer.id == materialized.convergence_analysis.id

    convergence_text = (
        tmp_path / (materialized.convergence_artifact.local_path or "")
    ).read_text(encoding="utf-8")
    assert VASP_CONVERGENCE_ARTIFACT_FORMAT in convergence_text

    bundle = ProjectBundle(
        project=case.project,
        structure_snapshots=(case.snapshot,),
        method_fingerprints=(case.fingerprint,),
        calculations=(materialized.updated_calculation,),
        execution_attempts=(case.attempt,),
        artifacts=(*case.raw_artifacts, *materialized.artifacts),
        analyses=materialized.analyses,
        provenance_records=materialized.provenance_records,
        dependency_records=materialized.dependency_records,
    )
    bundle.validate()
    ProjectStore(tmp_path).save(bundle)
    assert ProjectStore(tmp_path).open() == bundle
    assert SCHEMA_VERSION == 3


def test_raw_hash_drift_stales_entire_scientific_result_chain(tmp_path: Path) -> None:
    case = _case()
    materialized = materialize_vasp_scientific_result(
        project_root=tmp_path,
        calculation=case.calculation,
        intake=case.intake,
        result=case.result,
        assessment=case.assessment,
    )
    outcar, oszicar = case.raw_artifacts
    node_ids = {
        case.calculation.id,
        outcar.id,
        oszicar.id,
        materialized.result_parse_analysis.id,
        materialized.parsed_result_artifact.id,
        materialized.convergence_analysis.id,
        materialized.convergence_artifact.id,
    }
    current_hashes = {
        case.calculation.id: scientific_hash(case.calculation),
        outcar.id: "f" * 64,
        oszicar.id: scientific_hash(oszicar),
        materialized.result_parse_analysis.id: scientific_hash(
            materialized.result_parse_analysis
        ),
        materialized.parsed_result_artifact.id: scientific_hash(
            materialized.parsed_result_artifact
        ),
        materialized.convergence_analysis.id: scientific_hash(
            materialized.convergence_analysis
        ),
    }
    freshness = FreshnessEngine(materialized.dependency_records).evaluate(
        node_ids=node_ids,
        current_hashes=current_hashes,
    )

    assert freshness[materialized.result_parse_analysis.id].state is FreshnessState.STALE
    assert freshness[materialized.parsed_result_artifact.id].state is FreshnessState.STALE
    assert freshness[materialized.convergence_analysis.id].state is FreshnessState.STALE
    assert freshness[materialized.convergence_artifact.id].state is FreshnessState.STALE


@pytest.mark.parametrize(
    ("verdict", "expected"),
    (
        (ConvergenceVerdict.CONVERGED, CalculationScientificStatus.CONVERGED),
        (
            ConvergenceVerdict.UNCONVERGED,
            CalculationScientificStatus.COMPLETED_UNCONVERGED,
        ),
        (ConvergenceVerdict.INDETERMINATE, CalculationScientificStatus.BLOCKED),
    ),
)
def test_status_reconciliation_is_explicit(
    verdict: ConvergenceVerdict,
    expected: CalculationScientificStatus,
) -> None:
    calculation = _case().calculation
    assessment = VaspConvergenceAssessment(
        calculation_type=calculation.calculation_type,
        electronic=verdict,
        ionic=ConvergenceVerdict.NOT_APPLICABLE,
        overall=verdict,
    )
    assert reconcile_vasp_calculation_status(calculation, assessment).status is expected


def test_status_reconciliation_rejects_not_applicable_overall() -> None:
    calculation = _case().calculation
    assessment = VaspConvergenceAssessment(
        calculation_type=calculation.calculation_type,
        electronic=ConvergenceVerdict.NOT_APPLICABLE,
        ionic=ConvergenceVerdict.NOT_APPLICABLE,
        overall=ConvergenceVerdict.NOT_APPLICABLE,
    )
    with pytest.raises(ValueError, match="overall convergence verdict"):
        reconcile_vasp_calculation_status(calculation, assessment)

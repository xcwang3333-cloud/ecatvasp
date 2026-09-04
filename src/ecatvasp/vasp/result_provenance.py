"""Durable v0.5 scientific-result provenance and status reconciliation.

This layer materializes already-normalized VASP facts and convergence assessments
into the frozen Analysis/Artifact provenance graph. It performs no VASP parsing,
no convergence classification, no structure promotion, and no workflow
orchestration.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from ecatvasp.domain import (
    Analysis,
    AnalysisProducerRef,
    AnalysisStatus,
    AnalysisType,
    Artifact,
    ArtifactAvailability,
    ArtifactId,
    ArtifactType,
    Calculation,
    CalculationScientificStatus,
    CalculationType,
    RetrievalPolicy,
    canonical_json,
    canonical_sha256,
)
from ecatvasp.domain.ids import CalculationId
from ecatvasp.provenance import (
    DependencyKind,
    DependencyRecord,
    ProvenanceRecord,
    scientific_hash,
)
from ecatvasp.vasp.convergence import (
    VASP_CONVERGENCE_CLASSIFIER_NAME,
    VASP_CONVERGENCE_CLASSIFIER_VERSION,
)
from ecatvasp.vasp.result_intake import VaspResultInputFile
from ecatvasp.vasp.results import (
    VASP_RESULT_DOCUMENT_FORMAT,
    VASP_RESULT_DOCUMENT_VERSION,
    ConvergenceVerdict,
    VaspConvergenceAssessment,
    VaspResultDocument,
    VaspResultSource,
    VaspResultSourceRole,
    validate_result_parse_analysis,
)

VASP_SCIENTIFIC_RESULT_PIPELINE_NAME = "ecatvasp.vasp.scientific-result-pipeline"
VASP_SCIENTIFIC_RESULT_PIPELINE_VERSION = "1"
VASP_CONVERGENCE_ARTIFACT_FORMAT = "ecatvasp-vasp-convergence-assessment"
VASP_CONVERGENCE_ARTIFACT_VERSION = 1


class VaspScientificResultIntake(Protocol):
    """Read-only structural contract shared by managed and compatibility result intakes."""

    @property
    def calculation_id(self) -> CalculationId: ...

    @property
    def calculation_type(self) -> CalculationType: ...

    @property
    def recipe_id(self) -> str: ...

    @property
    def files(self) -> tuple[VaspResultInputFile, ...]: ...

    @property
    def intake_hash(self) -> str: ...

    @property
    def sources(self) -> tuple[VaspResultSource, ...]: ...

    @property
    def input_artifact_ids(self) -> tuple[ArtifactId, ...]: ...


class VaspResultProvenanceError(ValueError):
    """Raised when a scientific result cannot be persisted without ambiguity."""


@dataclass(frozen=True, slots=True)
class VaspScientificResultMaterialization:
    """Durable objects produced for one normalized VASP result and verdict."""

    updated_calculation: Calculation
    result_parse_analysis: Analysis
    parsed_result_artifact: Artifact
    convergence_analysis: Analysis
    convergence_artifact: Artifact
    provenance_records: tuple[ProvenanceRecord, ...]
    dependency_records: tuple[DependencyRecord, ...]

    @property
    def analyses(self) -> tuple[Analysis, Analysis]:
        return (self.result_parse_analysis, self.convergence_analysis)

    @property
    def artifacts(self) -> tuple[Artifact, Artifact]:
        return (self.parsed_result_artifact, self.convergence_artifact)


def reconcile_vasp_calculation_status(
    calculation: Calculation,
    assessment: VaspConvergenceAssessment,
) -> Calculation:
    """Map an explicit convergence verdict onto Calculation scientific lifecycle state."""

    if assessment.calculation_type is not calculation.calculation_type:
        raise VaspResultProvenanceError(
            "convergence assessment CalculationType does not match Calculation"
        )
    status_by_verdict = {
        ConvergenceVerdict.CONVERGED: CalculationScientificStatus.CONVERGED,
        ConvergenceVerdict.UNCONVERGED: (
            CalculationScientificStatus.COMPLETED_UNCONVERGED
        ),
        ConvergenceVerdict.INDETERMINATE: CalculationScientificStatus.BLOCKED,
    }
    try:
        status = status_by_verdict[assessment.overall]
    except KeyError as error:
        raise VaspResultProvenanceError(
            "overall convergence verdict must be converged, unconverged, or indeterminate"
        ) from error
    return replace(calculation, status=status)


def materialize_vasp_scientific_result(
    *,
    project_root: Path | str,
    calculation: Calculation,
    intake: VaspScientificResultIntake,
    result: VaspResultDocument,
    assessment: VaspConvergenceAssessment,
) -> VaspScientificResultMaterialization:
    """Persist normalized result/verdict artifacts and their exact scientific DAG."""

    _validate_identity(
        calculation=calculation,
        intake=intake,
        result=result,
        assessment=assessment,
    )
    root = Path(project_root).resolve()
    root.mkdir(parents=True, exist_ok=True)

    parse_parameters_hash = canonical_sha256(
        {
            "intake_hash": intake.intake_hash,
            "result_format": VASP_RESULT_DOCUMENT_FORMAT,
            "result_version": VASP_RESULT_DOCUMENT_VERSION,
        }
    )
    parse_analysis = Analysis(
        project_id=calculation.project_id,
        analysis_type=AnalysisType.RESULT_PARSE,
        input_artifact_ids=intake.input_artifact_ids,
        status=AnalysisStatus.COMPLETED,
        tool=VASP_SCIENTIFIC_RESULT_PIPELINE_NAME,
        tool_version=VASP_SCIENTIFIC_RESULT_PIPELINE_VERSION,
        parameters_hash=parse_parameters_hash,
    )
    validate_result_parse_analysis(parse_analysis)

    parsed_payload = {
        "format": VASP_RESULT_DOCUMENT_FORMAT,
        "version": VASP_RESULT_DOCUMENT_VERSION,
        "calculation_id": calculation.id,
        "analysis_id": parse_analysis.id,
        "intake_hash": intake.intake_hash,
        "result": result,
    }
    parsed_artifact = _write_json_artifact(
        root=root,
        relative_path=(
            Path("calculations")
            / str(calculation.id)
            / "scientific"
            / "parsed-result.json"
        ),
        payload=parsed_payload,
        artifact_type=ArtifactType.PARSED_RESULT,
        producer=AnalysisProducerRef(parse_analysis.id),
    )

    convergence_input_ids = _convergence_input_artifact_ids(
        intake=intake,
        parsed_result_artifact_id=parsed_artifact.id,
    )
    convergence_parameters_hash = canonical_sha256(
        {
            "classifier": VASP_CONVERGENCE_CLASSIFIER_NAME,
            "classifier_version": VASP_CONVERGENCE_CLASSIFIER_VERSION,
            "intake_hash": intake.intake_hash,
        }
    )
    convergence_analysis = Analysis(
        project_id=calculation.project_id,
        analysis_type=AnalysisType.CONVERGENCE,
        input_artifact_ids=convergence_input_ids,
        status=AnalysisStatus.COMPLETED,
        tool=VASP_CONVERGENCE_CLASSIFIER_NAME,
        tool_version=VASP_CONVERGENCE_CLASSIFIER_VERSION,
        parameters_hash=convergence_parameters_hash,
    )
    convergence_payload = {
        "format": VASP_CONVERGENCE_ARTIFACT_FORMAT,
        "version": VASP_CONVERGENCE_ARTIFACT_VERSION,
        "calculation_id": calculation.id,
        "analysis_id": convergence_analysis.id,
        "intake_hash": intake.intake_hash,
        "assessment": assessment,
    }
    convergence_artifact = _write_json_artifact(
        root=root,
        relative_path=(
            Path("calculations")
            / str(calculation.id)
            / "scientific"
            / "convergence.json"
        ),
        payload=convergence_payload,
        artifact_type=ArtifactType.DERIVED_DATASET,
        producer=AnalysisProducerRef(convergence_analysis.id),
    )

    updated_calculation = reconcile_vasp_calculation_status(calculation, assessment)
    provenance_records = _provenance_records(
        calculation=calculation,
        parse_analysis=parse_analysis,
        parsed_artifact=parsed_artifact,
        convergence_analysis=convergence_analysis,
        convergence_artifact=convergence_artifact,
    )
    dependency_records = _dependency_records(
        calculation=calculation,
        intake=intake,
        parse_analysis=parse_analysis,
        parsed_artifact=parsed_artifact,
        convergence_analysis=convergence_analysis,
        convergence_artifact=convergence_artifact,
    )
    return VaspScientificResultMaterialization(
        updated_calculation=updated_calculation,
        result_parse_analysis=parse_analysis,
        parsed_result_artifact=parsed_artifact,
        convergence_analysis=convergence_analysis,
        convergence_artifact=convergence_artifact,
        provenance_records=provenance_records,
        dependency_records=dependency_records,
    )


def _validate_identity(
    *,
    calculation: Calculation,
    intake: VaspScientificResultIntake,
    result: VaspResultDocument,
    assessment: VaspConvergenceAssessment,
) -> None:
    if intake.calculation_id != calculation.id:
        raise VaspResultProvenanceError("result intake belongs to another Calculation")
    if intake.calculation_type is not calculation.calculation_type:
        raise VaspResultProvenanceError("result intake CalculationType does not match Calculation")
    if intake.recipe_id != calculation.recipe_id:
        raise VaspResultProvenanceError("result intake recipe does not match Calculation")
    if result.calculation_type is not calculation.calculation_type:
        raise VaspResultProvenanceError("result CalculationType does not match Calculation")
    if result.sources != intake.sources:
        raise VaspResultProvenanceError("result sources do not match exact result intake")
    if assessment.calculation_type is not calculation.calculation_type:
        raise VaspResultProvenanceError(
            "convergence assessment CalculationType does not match Calculation"
        )
    if not intake.input_artifact_ids:
        raise VaspResultProvenanceError("scientific result intake requires raw Artifacts")
    if len(intake.input_artifact_ids) != len(set(intake.input_artifact_ids)):
        raise VaspResultProvenanceError("scientific result input Artifact ids must be unique")


def _convergence_input_artifact_ids(
    *,
    intake: VaspScientificResultIntake,
    parsed_result_artifact_id: ArtifactId,
) -> tuple[ArtifactId, ...]:
    raw_evidence_ids = tuple(
        item.source.artifact_id
        for item in intake.files
        if item.source.role in {
            VaspResultSourceRole.OUTCAR,
            VaspResultSourceRole.OSZICAR,
        }
    )
    return (parsed_result_artifact_id, *raw_evidence_ids)


def _write_json_artifact(
    *,
    root: Path,
    relative_path: Path,
    payload: object,
    artifact_type: ArtifactType,
    producer: AnalysisProducerRef,
) -> Artifact:
    text = canonical_json(payload) + "\n"
    absolute_path = (root / relative_path).resolve()
    if not absolute_path.is_relative_to(root):
        raise VaspResultProvenanceError("derived result path resolves outside project_root")
    absolute_path.parent.mkdir(parents=True, exist_ok=True)
    if absolute_path.exists():
        if not absolute_path.is_file():
            raise VaspResultProvenanceError("derived result path is not a regular file")
        if absolute_path.read_text(encoding="utf-8") != text:
            raise VaspResultProvenanceError(
                "derived scientific result already exists with different content"
            )
    else:
        temporary = absolute_path.with_name(f".{absolute_path.name}.tmp")
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, absolute_path)
    body = text.encode("utf-8")
    return Artifact(
        artifact_type=artifact_type,
        producer=producer,
        availability=ArtifactAvailability.LOCAL,
        retrieval_policy=RetrievalPolicy.ALWAYS,
        local_path=relative_path.as_posix(),
        size_bytes=len(body),
        sha256=hashlib.sha256(body).hexdigest(),
    )


def _provenance_records(
    *,
    calculation: Calculation,
    parse_analysis: Analysis,
    parsed_artifact: Artifact,
    convergence_analysis: Analysis,
    convergence_artifact: Artifact,
) -> tuple[ProvenanceRecord, ...]:
    method_id = calculation.method_fingerprint_id
    return (
        ProvenanceRecord(
            subject_id=parse_analysis.id,
            tool=VASP_SCIENTIFIC_RESULT_PIPELINE_NAME,
            tool_version=VASP_SCIENTIFIC_RESULT_PIPELINE_VERSION,
            parameters_hash=parse_analysis.parameters_hash,
            method_fingerprint_id=method_id,
        ),
        ProvenanceRecord(
            subject_id=parsed_artifact.id,
            tool=VASP_SCIENTIFIC_RESULT_PIPELINE_NAME,
            tool_version=VASP_SCIENTIFIC_RESULT_PIPELINE_VERSION,
            parameters_hash=parsed_artifact.sha256,
            method_fingerprint_id=method_id,
        ),
        ProvenanceRecord(
            subject_id=convergence_analysis.id,
            tool=VASP_CONVERGENCE_CLASSIFIER_NAME,
            tool_version=VASP_CONVERGENCE_CLASSIFIER_VERSION,
            parameters_hash=convergence_analysis.parameters_hash,
            method_fingerprint_id=method_id,
        ),
        ProvenanceRecord(
            subject_id=convergence_artifact.id,
            tool=VASP_SCIENTIFIC_RESULT_PIPELINE_NAME,
            tool_version=VASP_SCIENTIFIC_RESULT_PIPELINE_VERSION,
            parameters_hash=convergence_artifact.sha256,
            method_fingerprint_id=method_id,
        ),
    )


def _dependency_records(
    *,
    calculation: Calculation,
    intake: VaspScientificResultIntake,
    parse_analysis: Analysis,
    parsed_artifact: Artifact,
    convergence_analysis: Analysis,
    convergence_artifact: Artifact,
) -> tuple[DependencyRecord, ...]:
    records: list[DependencyRecord] = [
        DependencyRecord(
            upstream_id=calculation.id,
            downstream_id=parse_analysis.id,
            kind=DependencyKind.SCIENTIFIC,
            role="calculation_context",
            recorded_hash=scientific_hash(calculation),
        )
    ]
    records.extend(
        DependencyRecord(
            upstream_id=source.artifact_id,
            downstream_id=parse_analysis.id,
            kind=DependencyKind.SCIENTIFIC,
            role=f"result_source:{source.role.value}",
            recorded_hash=source.sha256,
        )
        for source in intake.sources
    )
    records.append(
        DependencyRecord(
            upstream_id=parse_analysis.id,
            downstream_id=parsed_artifact.id,
            kind=DependencyKind.SCIENTIFIC,
            role="normalized_result_analysis",
            recorded_hash=scientific_hash(parse_analysis),
        )
    )
    records.extend(
        (
            DependencyRecord(
                upstream_id=calculation.id,
                downstream_id=convergence_analysis.id,
                kind=DependencyKind.SCIENTIFIC,
                role="calculation_context",
                recorded_hash=scientific_hash(calculation),
            ),
            DependencyRecord(
                upstream_id=parsed_artifact.id,
                downstream_id=convergence_analysis.id,
                kind=DependencyKind.SCIENTIFIC,
                role="normalized_result",
                recorded_hash=scientific_hash(parsed_artifact),
            ),
        )
    )
    for item in intake.files:
        if item.source.role not in {
            VaspResultSourceRole.OUTCAR,
            VaspResultSourceRole.OSZICAR,
        }:
            continue
        records.append(
            DependencyRecord(
                upstream_id=item.source.artifact_id,
                downstream_id=convergence_analysis.id,
                kind=DependencyKind.SCIENTIFIC,
                role=f"convergence_evidence:{item.source.role.value}",
                recorded_hash=item.source.sha256,
            )
        )
    records.append(
        DependencyRecord(
            upstream_id=convergence_analysis.id,
            downstream_id=convergence_artifact.id,
            kind=DependencyKind.SCIENTIFIC,
            role="convergence_assessment",
            recorded_hash=scientific_hash(convergence_analysis),
        )
    )
    return tuple(records)

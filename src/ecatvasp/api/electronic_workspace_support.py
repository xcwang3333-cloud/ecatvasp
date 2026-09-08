"""Typed support functions for the v1.1 Electronic Analysis Workspace."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from uuid import UUID

from ecatvasp.analysis import (
    BandCenterParameters,
    CanonicalDosResult,
    load_band_center_artifact,
    load_canonical_dos_artifact,
)
from ecatvasp.api.application import ApplicationServiceError
from ecatvasp.domain import (
    Analysis,
    AnalysisId,
    AnalysisProducerRef,
    AnalysisStatus,
    AnalysisType,
    Artifact,
    ArtifactAvailability,
    ArtifactId,
    ArtifactType,
    Calculation,
    CalculationId,
    ExecutionAttempt,
    ExecutionAttemptId,
    MethodFingerprint,
    StructureSnapshot,
)
from ecatvasp.provenance import DependencyRecord, ProvenanceRecord, scientific_hash
from ecatvasp.storage import ProjectBundle, ProjectStore
from ecatvasp.workflow import (
    ElectronicAnalysisProjection,
    ElectronicAnalysisRequirement,
    reconcile_electronic_analyses_from_store,
)

DOS_OUTPUT = "canonical-dos.json"
BADER_ACF_OUTPUT = "ACF.dat"
BADER_OUTPUT = "canonical-bader.json"
CHARGE_DENSITY_OUTPUT = "charge-difference.f64"
CHARGE_METADATA_OUTPUT = "canonical-charge-difference.json"
COHP_RAW_OUTPUT = "COHPCAR.lobster"
ICOHP_RAW_OUTPUT = "ICOHPLIST.lobster"
COHP_OUTPUT = "canonical-cohp.json"
BAND_CENTER_OUTPUT = "canonical-band-center.json"
ATOM_MAP_OUTPUT = "atom-index-map.json"


@dataclass(frozen=True, slots=True)
class DosSource:
    bundle: ProjectBundle
    calculation: Calculation
    attempt: ExecutionAttempt
    fingerprint: MethodFingerprint
    snapshot: StructureSnapshot
    doscar: Artifact
    atom_map: Artifact
    doscar_bytes: bytes
    atom_map_bytes: bytes


def analysis_catalog_row(
    *,
    store: ProjectStore,
    bundle: ProjectBundle,
    analysis: Analysis,
    current_hash_overrides: dict[UUID, str],
    invalid_ids: set[UUID],
) -> dict[str, object]:
    state = analysis_projection_payload(
        store=store,
        bundle=bundle,
        analysis=analysis,
        current_hash_overrides=current_hash_overrides,
        invalid_ids=invalid_ids,
    )
    return {
        "analysis_id": str(analysis.id),
        "analysis_type": analysis.analysis_type.value,
        "status": analysis.status.value,
        "tool": analysis.tool,
        "tool_version": analysis.tool_version,
        "input_artifact_count": len(analysis.input_artifact_ids),
        "freshness": state,
        "view_supported": analysis.analysis_type
        in {
            AnalysisType.DOS,
            AnalysisType.BADER,
            AnalysisType.CHARGE_DIFFERENCE,
            AnalysisType.COHP,
            AnalysisType.BAND_CENTER,
        },
    }


def analysis_projection_payload(
    *,
    store: ProjectStore,
    bundle: ProjectBundle,
    analysis: Analysis,
    current_hash_overrides: dict[UUID, str],
    invalid_ids: set[UUID],
) -> dict[str, object]:
    projection = analysis_projection(
        store=store,
        bundle=bundle,
        analysis=analysis,
        current_hash_overrides=current_hash_overrides,
        invalid_ids=invalid_ids,
    )
    return {
        "scientific_state": projection.scientific_state.value,
        "readiness": projection.readiness.value,
        "freshness_state": (
            projection.freshness_state.value
            if projection.freshness_state is not None
            else None
        ),
        "reason_codes": list(projection.reason_codes),
    }


def analysis_projection(
    *,
    store: ProjectStore,
    bundle: ProjectBundle,
    analysis: Analysis,
    current_hash_overrides: dict[UUID, str],
    invalid_ids: set[UUID],
) -> ElectronicAnalysisProjection:
    parameters_hash = analysis.parameters_hash
    if parameters_hash is None:
        raise ApplicationServiceError(
            "electronic Analysis requires a deterministic parameters_hash"
        )
    requirement = ElectronicAnalysisRequirement(
        key=str(analysis.id),
        project_id=bundle.project.id,
        analysis_type=analysis.analysis_type,
        input_artifact_ids=analysis.input_artifact_ids,
        parameters_hash=parameters_hash,
    )
    report = reconcile_electronic_analyses_from_store(
        store=store,
        requirements=(requirement,),
        current_hash_overrides=current_hash_overrides,
        invalid_ids=invalid_ids,
    )
    return report.requirement(requirement.key)


def observed_artifact_state(
    root: Path,
    bundle: ProjectBundle,
) -> tuple[dict[UUID, str], set[UUID]]:
    overrides: dict[UUID, str] = {}
    invalid: set[UUID] = set()
    resolved_root = root.resolve()
    for artifact in bundle.artifacts:
        if artifact.availability not in {
            ArtifactAvailability.LOCAL,
            ArtifactAvailability.BOTH,
        }:
            continue
        try:
            path = safe_artifact_path(resolved_root, artifact, "local Artifact")
            body = path.read_bytes()
        except (OSError, ValueError):
            invalid.add(artifact.id)
            continue
        observed_sha = hashlib.sha256(body).hexdigest()
        observed_size = len(body)
        if artifact.sha256 != observed_sha or artifact.size_bytes != observed_size:
            observed = replace(
                artifact,
                sha256=observed_sha,
                size_bytes=observed_size,
            )
            overrides[artifact.id] = scientific_hash(observed)
    return overrides, invalid


def find_existing_dos(
    *,
    root: Path,
    bundle: ProjectBundle,
    doscar: Artifact,
    atom_map: Artifact,
    expected: CanonicalDosResult,
) -> tuple[Analysis, Artifact] | None:
    candidates = tuple(
        analysis
        for analysis in bundle.analyses
        if analysis.analysis_type is AnalysisType.DOS
        and analysis.status is AnalysisStatus.COMPLETED
        and analysis.input_artifact_ids == (doscar.id, atom_map.id)
    )
    exact: list[tuple[Analysis, Artifact]] = []
    for analysis in candidates:
        artifact = analysis_output(
            bundle,
            analysis,
            filename=DOS_OUTPUT,
            artifact_type=ArtifactType.DERIVED_DATASET,
        )
        reopened = load_canonical_dos_artifact(
            project_root=root,
            analysis=analysis,
            artifact=artifact,
        )
        if reopened.content_hash != expected.content_hash:
            raise ApplicationServiceError(
                "existing canonical DOS for exact source Artifacts disagrees with current parser"
            )
        exact.append((analysis, artifact))
    if len(exact) > 1:
        raise ApplicationServiceError("duplicate canonical DOS Analyses exist for exact source")
    return exact[0] if exact else None


def find_existing_band_center(
    *,
    root: Path,
    bundle: ProjectBundle,
    source_analysis: Analysis,
    source_artifact: Artifact,
    parameters: BandCenterParameters,
) -> tuple[Analysis, Artifact] | None:
    matches: list[tuple[Analysis, Artifact]] = []
    candidates = tuple(
        analysis
        for analysis in bundle.analyses
        if analysis.analysis_type is AnalysisType.BAND_CENTER
        and analysis.status is AnalysisStatus.COMPLETED
        and analysis.input_artifact_ids == (source_artifact.id,)
    )
    for analysis in candidates:
        artifact = analysis_output(
            bundle,
            analysis,
            filename=BAND_CENTER_OUTPUT,
            artifact_type=ArtifactType.DERIVED_DATASET,
        )
        result = load_band_center_artifact(
            project_root=root,
            source_analysis=source_analysis,
            source_artifact=source_artifact,
            analysis=analysis,
            artifact=artifact,
        )
        if result.parameters == parameters:
            matches.append((analysis, artifact))
    if len(matches) > 1:
        raise ApplicationServiceError(
            "duplicate band-center Analyses exist for exact source and parameters"
        )
    return matches[0] if matches else None


def persist_analysis_graph(
    *,
    store: ProjectStore,
    bundle: ProjectBundle,
    analysis: Analysis,
    artifacts: tuple[Artifact, ...],
    provenance_records: tuple[ProvenanceRecord, ...],
    dependency_records: tuple[DependencyRecord, ...],
) -> None:
    if any(item.id == analysis.id for item in bundle.analyses):
        raise ApplicationServiceError("new electronic Analysis id already exists")
    known_artifacts = {item.id for item in bundle.artifacts}
    if any(item.id in known_artifacts for item in artifacts):
        raise ApplicationServiceError("new electronic Artifact id already exists")
    updated = replace(
        bundle,
        analyses=(*bundle.analyses, analysis),
        artifacts=(*bundle.artifacts, *artifacts),
        provenance_records=(*bundle.provenance_records, *provenance_records),
        dependency_records=(*bundle.dependency_records, *dependency_records),
    )
    store.save(updated)
    reopened = store.open()
    if require_analysis(reopened, analysis.id) != analysis:
        raise ApplicationServiceError("electronic Analysis failed post-save verification")
    for artifact in artifacts:
        if require_artifact(reopened, artifact.id) != artifact:
            raise ApplicationServiceError("electronic Artifact failed post-save verification")


def require_source_unchanged(bundle: ProjectBundle, source: DosSource) -> None:
    if require_calculation(bundle, source.calculation.id) != source.calculation:
        raise ApplicationServiceError("DOS Calculation changed during materialization")
    if require_attempt(bundle, source.attempt.id) != source.attempt:
        raise ApplicationServiceError("DOS ExecutionAttempt changed during materialization")
    if require_fingerprint(bundle, source.calculation) != source.fingerprint:
        raise ApplicationServiceError("DOS MethodFingerprint changed during materialization")
    if require_snapshot(bundle, source.calculation) != source.snapshot:
        raise ApplicationServiceError("DOS StructureSnapshot changed during materialization")
    if require_artifact(bundle, source.doscar.id) != source.doscar:
        raise ApplicationServiceError("DOSCAR Artifact changed during materialization")
    if require_artifact(bundle, source.atom_map.id) != source.atom_map:
        raise ApplicationServiceError("atom-index-map Artifact changed during materialization")


def matching_analysis_ids(
    bundle: ProjectBundle,
    analysis_type: AnalysisType,
    input_artifact_ids: tuple[ArtifactId, ...],
) -> tuple[AnalysisId, ...]:
    return tuple(
        item.id
        for item in bundle.analyses
        if item.analysis_type is analysis_type
        and item.input_artifact_ids == input_artifact_ids
    )


def analysis_output(
    bundle: ProjectBundle,
    analysis: Analysis,
    *,
    filename: str,
    artifact_type: ArtifactType,
) -> Artifact:
    return single_artifact(
        bundle,
        producer=AnalysisProducerRef(analysis.id),
        artifact_type=artifact_type,
        filename=filename,
        label=f"{analysis.analysis_type.value} {filename}",
    )


def single_artifact(
    bundle: ProjectBundle,
    *,
    producer: object,
    artifact_type: ArtifactType,
    filename: str,
    label: str,
) -> Artifact:
    matches = tuple(
        item
        for item in bundle.artifacts
        if item.producer == producer
        and item.artifact_type is artifact_type
        and PurePosixPath(item.local_path or "").name == filename
    )
    if len(matches) != 1:
        raise ApplicationServiceError(f"{label} requires exactly one matching Artifact")
    return matches[0]


def verified_local_bytes(
    *,
    root: Path,
    artifact: Artifact,
    expected_filename: str,
    label: str,
) -> bytes:
    if artifact.availability not in {
        ArtifactAvailability.LOCAL,
        ArtifactAvailability.BOTH,
    }:
        raise ApplicationServiceError(f"{label} must be locally available")
    if artifact.sha256 is None or artifact.size_bytes is None:
        raise ApplicationServiceError(f"{label} requires exact size/SHA-256 metadata")
    path = safe_artifact_path(root.resolve(), artifact, label)
    if path.name != expected_filename:
        raise ApplicationServiceError(f"{label} has unexpected local filename")
    try:
        body = path.read_bytes()
    except OSError as error:
        raise ApplicationServiceError(f"{label} cannot be read") from error
    if len(body) != artifact.size_bytes:
        raise ApplicationServiceError(f"{label} local byte size changed")
    if hashlib.sha256(body).hexdigest() != artifact.sha256.lower():
        raise ApplicationServiceError(f"{label} local content hash changed")
    return body


def safe_artifact_path(root: Path, artifact: Artifact, label: str) -> Path:
    if artifact.local_path is None:
        raise ApplicationServiceError(f"{label} requires local_path")
    relative = PurePosixPath(artifact.local_path)
    if (
        relative.is_absolute()
        or artifact.local_path != relative.as_posix()
        or ".." in relative.parts
        or artifact.local_path in {"", "."}
    ):
        raise ApplicationServiceError(f"{label} local_path must be normalized and relative")
    path = (root / Path(*relative.parts)).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ApplicationServiceError(f"{label} local file is missing or outside project_root")
    return path


def latest_attempt(
    bundle: ProjectBundle,
    calculation_id: CalculationId,
) -> ExecutionAttempt | None:
    attempts = sorted(
        (item for item in bundle.execution_attempts if item.calculation_id == calculation_id),
        key=lambda item: item.attempt_number,
    )
    return attempts[-1] if attempts else None


def require_calculation(bundle: ProjectBundle, calculation_id: CalculationId) -> Calculation:
    matches = tuple(item for item in bundle.calculations if item.id == calculation_id)
    if len(matches) != 1:
        raise ApplicationServiceError("Calculation is absent or duplicated")
    return matches[0]


def require_attempt(bundle: ProjectBundle, attempt_id: ExecutionAttemptId) -> ExecutionAttempt:
    matches = tuple(item for item in bundle.execution_attempts if item.id == attempt_id)
    if len(matches) != 1:
        raise ApplicationServiceError("ExecutionAttempt is absent or duplicated")
    return matches[0]


def require_fingerprint(bundle: ProjectBundle, calculation: Calculation) -> MethodFingerprint:
    matches = tuple(
        item
        for item in bundle.method_fingerprints
        if item.id == calculation.method_fingerprint_id
    )
    if len(matches) != 1:
        raise ApplicationServiceError("MethodFingerprint is absent or duplicated")
    return matches[0]


def require_snapshot(bundle: ProjectBundle, calculation: Calculation) -> StructureSnapshot:
    matches = tuple(
        item
        for item in bundle.structure_snapshots
        if item.id == calculation.input_structure_snapshot_id
    )
    if len(matches) != 1:
        raise ApplicationServiceError("StructureSnapshot is absent or duplicated")
    return matches[0]


def require_analysis(bundle: ProjectBundle, analysis_id: AnalysisId) -> Analysis:
    matches = tuple(item for item in bundle.analyses if item.id == analysis_id)
    if len(matches) != 1:
        raise ApplicationServiceError("Analysis is absent or duplicated")
    return matches[0]


def require_artifact(bundle: ProjectBundle, artifact_id: ArtifactId) -> Artifact:
    matches = tuple(item for item in bundle.artifacts if item.id == artifact_id)
    if len(matches) != 1:
        raise ApplicationServiceError("Artifact is absent or duplicated")
    return matches[0]


__all__ = [
    "ATOM_MAP_OUTPUT",
    "BADER_ACF_OUTPUT",
    "BADER_OUTPUT",
    "BAND_CENTER_OUTPUT",
    "CHARGE_DENSITY_OUTPUT",
    "CHARGE_METADATA_OUTPUT",
    "COHP_OUTPUT",
    "COHP_RAW_OUTPUT",
    "DOS_OUTPUT",
    "ICOHP_RAW_OUTPUT",
    "DosSource",
    "analysis_catalog_row",
    "analysis_output",
    "analysis_projection",
    "analysis_projection_payload",
    "find_existing_band_center",
    "find_existing_dos",
    "latest_attempt",
    "matching_analysis_ids",
    "observed_artifact_state",
    "persist_analysis_graph",
    "require_analysis",
    "require_artifact",
    "require_calculation",
    "require_fingerprint",
    "require_snapshot",
    "require_source_unchanged",
    "single_artifact",
    "verified_local_bytes",
]

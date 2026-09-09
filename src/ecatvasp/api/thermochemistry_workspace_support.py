"""Evidence resolution and canonical reads for the v1.1 thermochemistry workspace.

This module is application support only. It resolves exact ProjectStore entities, observes local
Artifact bytes, replays the frozen VASP frequency parser against the same RESULT_PARSE inputs, and
projects the existing v0.8 thermochemistry/reaction freshness model. It does not implement a second
thermochemistry, CHE, reaction, descriptor, or freshness engine.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import cast
from uuid import UUID

from ecatvasp.api.application import ApplicationServiceError
from ecatvasp.api.execution_plan_resolver import resolve_execution_plan
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
    CalculationScientificStatus,
    CalculationType,
    ExecutionAttempt,
    ExecutionAttemptId,
    ExecutionAttemptProducerRef,
    MethodFingerprint,
    StructureSnapshot,
    canonical_sha256,
)
from ecatvasp.provenance import DependencyRecord, ProvenanceRecord, scientific_hash
from ecatvasp.storage import ProjectBundle, ProjectStore
from ecatvasp.thermo import (
    CANONICAL_HARMONIC_THERMOCHEMISTRY_FORMAT,
    CANONICAL_HARMONIC_THERMOCHEMISTRY_VERSION,
    CANONICAL_IDEAL_GAS_THERMOCHEMISTRY_FORMAT,
    CANONICAL_IDEAL_GAS_THERMOCHEMISTRY_VERSION,
    CANONICAL_REACTION_DIAGRAM_FORMAT,
    CANONICAL_REACTION_DIAGRAM_VERSION,
    CANONICAL_REFERENCE_THERMOCHEMISTRY_FORMAT,
    CANONICAL_REFERENCE_THERMOCHEMISTRY_VERSION,
    HARMONIC_THERMOCHEMISTRY_TOOL_NAME,
    HARMONIC_THERMOCHEMISTRY_TOOL_VERSION,
    IDEAL_GAS_THERMOCHEMISTRY_TOOL_NAME,
    IDEAL_GAS_THERMOCHEMISTRY_TOOL_VERSION,
    REACTION_DIAGRAM_TOOL_NAME,
    REACTION_DIAGRAM_TOOL_VERSION,
    REFERENCE_CORRECTION_TOOL_NAME,
    REFERENCE_CORRECTION_TOOL_VERSION,
    GasReferenceDefinition,
    ThermochemistryIdentity,
)
from ecatvasp.vasp import (
    VASP_RESULT_DOCUMENT_FORMAT,
    VASP_RESULT_DOCUMENT_VERSION,
    VaspResultDocument,
    build_vasp_result_artifact_intake,
    parse_vasp_energy_metadata,
    parse_vasp_frequency_results,
)
from ecatvasp.workflow import (
    ThermochemistryAnalysisProjection,
    ThermochemistryAnalysisRequirement,
    reconcile_thermochemistry_analyses_from_store,
)

PARSED_RESULT_OUTPUT = "parsed-result.json"
HARMONIC_OUTPUT = "canonical-harmonic-thermochemistry.json"
GAS_OUTPUT = "canonical-ideal-gas-thermochemistry.json"
REFERENCE_OUTPUT = "canonical-reference-thermochemistry.json"
REACTION_DIAGRAM_OUTPUT = "canonical-reaction-diagram.json"


@dataclass(frozen=True, slots=True)
class ResolvedFrequencySource:
    """One exact persisted RESULT_PARSE generation reconstructed through frozen VASP parsers."""

    bundle: ProjectBundle
    calculation: Calculation
    attempt: ExecutionAttempt
    fingerprint: MethodFingerprint
    snapshot: StructureSnapshot
    source_analysis: Analysis
    source_artifact: Artifact
    result: VaspResultDocument


@dataclass(frozen=True, slots=True)
class CanonicalAnalysisPayload:
    """Verified canonical thermochemistry/reaction payload and its exact output Artifact."""

    artifact: Artifact
    payload: dict[str, object]


def resolve_frequency_source(
    *,
    store: ProjectStore,
    calculation_id: CalculationId,
    expected_type: CalculationType,
) -> ResolvedFrequencySource:
    """Resolve the newest persisted RESULT_PARSE attempt for one converged frequency Calculation."""

    bundle = store.open()
    calculation = require_calculation(bundle, calculation_id)
    if calculation.calculation_type is not expected_type:
        raise ApplicationServiceError(
            f"thermochemistry source requires CalculationType.{expected_type.name}"
        )
    if calculation.status is not CalculationScientificStatus.CONVERGED:
        raise ApplicationServiceError(
            "thermochemistry source Calculation must be scientifically CONVERGED"
        )
    fingerprint = require_fingerprint(bundle, calculation)
    snapshot = require_snapshot(bundle, calculation)

    candidates: list[tuple[ExecutionAttempt, Analysis, Artifact]] = []
    artifact_by_id = {item.id: item for item in bundle.artifacts}
    attempt_by_id = {item.id: item for item in bundle.execution_attempts}
    for analysis in bundle.analyses:
        if analysis.analysis_type is not AnalysisType.RESULT_PARSE:
            continue
        if analysis.status is not AnalysisStatus.COMPLETED:
            continue
        if not analysis.input_artifact_ids:
            continue
        try:
            inputs = tuple(artifact_by_id[item_id] for item_id in analysis.input_artifact_ids)
        except KeyError:
            continue
        attempt_ids = {
            item.producer.id
            for item in inputs
            if isinstance(item.producer, ExecutionAttemptProducerRef)
        }
        if len(attempt_ids) != 1 or any(
            not isinstance(item.producer, ExecutionAttemptProducerRef) for item in inputs
        ):
            continue
        attempt_id = next(iter(attempt_ids))
        attempt = attempt_by_id.get(attempt_id)
        if attempt is None or attempt.calculation_id != calculation.id:
            continue
        parsed = _single_analysis_output(
            bundle,
            analysis,
            artifact_type=ArtifactType.PARSED_RESULT,
            filename=PARSED_RESULT_OUTPUT,
            allow_absent=True,
        )
        if parsed is None:
            continue
        candidates.append((attempt, analysis, parsed))
    if not candidates:
        raise ApplicationServiceError(
            "converged frequency Calculation has no exact persisted RESULT_PARSE generation"
        )
    latest_attempt_number = max(item[0].attempt_number for item in candidates)
    latest_candidates = tuple(
        item for item in candidates if item[0].attempt_number == latest_attempt_number
    )
    if len(latest_candidates) != 1:
        raise ApplicationServiceError(
            "latest persisted RESULT_PARSE generation is absent or duplicated"
        )
    attempt, source_analysis, source_artifact = latest_candidates[0]

    plan = resolve_execution_plan(store.root, bundle, attempt.id)
    input_artifacts = tuple(
        require_artifact(bundle, item_id) for item_id in source_analysis.input_artifact_ids
    )
    intake = build_vasp_result_artifact_intake(
        project_root=store.root,
        calculation=calculation,
        plan=plan,
        attempt=attempt,
        artifacts=input_artifacts,
    )
    if intake.input_artifact_ids != source_analysis.input_artifact_ids:
        raise ApplicationServiceError(
            "RESULT_PARSE input Artifact ordering differs from reconstructed managed intake"
        )
    result = parse_vasp_energy_metadata(project_root=store.root, intake=intake)
    result = parse_vasp_frequency_results(
        project_root=store.root,
        calculation=calculation,
        fingerprint=fingerprint,
        plan=plan,
        intake=intake,
        input_snapshot=snapshot,
        result=result,
    )
    _verify_parsed_result_payload(
        root=store.root,
        calculation=calculation,
        analysis=source_analysis,
        artifact=source_artifact,
        intake_hash=intake.intake_hash,
        expected=result,
    )
    return ResolvedFrequencySource(
        bundle=bundle,
        calculation=calculation,
        attempt=attempt,
        fingerprint=fingerprint,
        snapshot=snapshot,
        source_analysis=source_analysis,
        source_artifact=source_artifact,
        result=result,
    )


def find_existing_thermochemistry(
    *,
    root: Path,
    bundle: ProjectBundle,
    source: ResolvedFrequencySource,
    identity: ThermochemistryIdentity,
    tool: str,
    tool_version: str,
    filename: str,
    expected_format: str,
    expected_version: int,
    reference: GasReferenceDefinition | None = None,
) -> tuple[Analysis, Artifact] | None:
    """Find an exact existing v0.8 thermochemistry materialization by canonical receipt identity."""

    matches: list[tuple[Analysis, Artifact]] = []
    for analysis in bundle.analyses:
        if analysis.analysis_type is not AnalysisType.THERMOCHEMISTRY:
            continue
        if analysis.status is not AnalysisStatus.COMPLETED:
            continue
        if analysis.input_artifact_ids != (source.source_artifact.id,):
            continue
        if (analysis.tool, analysis.tool_version) != (tool, tool_version):
            continue
        artifact = _single_analysis_output(
            bundle,
            analysis,
            artifact_type=ArtifactType.DERIVED_DATASET,
            filename=filename,
        )
        assert artifact is not None
        payload = verified_json_artifact(
            root=root,
            analysis=analysis,
            artifact=artifact,
            filename=filename,
            expected_format=expected_format,
            expected_version=expected_version,
        )
        receipt = _mapping(payload.get("source_receipt"), "thermochemistry source_receipt")
        receipt_hash = _required_string(payload, "source_receipt_hash")
        if analysis.parameters_hash != receipt_hash:
            raise ApplicationServiceError(
                "thermochemistry source receipt differs from Analysis parameters_hash"
            )
        if canonical_sha256(receipt) != receipt_hash:
            raise ApplicationServiceError("thermochemistry source receipt hash is inconsistent")
        if not _receipt_matches_frequency_source(receipt, source):
            continue
        if receipt.get("thermochemistry_parameters_hash") != identity.parameters_hash:
            continue
        if reference is not None:
            if receipt.get("reference_content_hash") != reference.content_hash:
                continue
        elif "reference_content_hash" in receipt:
            continue
        matches.append((analysis, artifact))
    if len(matches) > 1:
        raise ApplicationServiceError(
            "duplicate exact thermochemistry Analyses exist for the same source and parameters"
        )
    return matches[0] if matches else None


def thermochemistry_projection_payload(
    *,
    store: ProjectStore,
    bundle: ProjectBundle,
    analysis: Analysis,
    current_hash_overrides: dict[UUID, str],
    invalid_ids: set[UUID],
) -> dict[str, object]:
    projection = thermochemistry_projection(
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


def thermochemistry_projection(
    *,
    store: ProjectStore,
    bundle: ProjectBundle,
    analysis: Analysis,
    current_hash_overrides: dict[UUID, str],
    invalid_ids: set[UUID],
) -> ThermochemistryAnalysisProjection:
    if analysis.analysis_type not in {
        AnalysisType.THERMOCHEMISTRY,
        AnalysisType.REACTION_DIAGRAM,
    }:
        raise ApplicationServiceError("Analysis is not thermochemistry/reaction scientific data")
    if analysis.parameters_hash is None:
        raise ApplicationServiceError(
            "thermochemistry/reaction Analysis requires deterministic parameters_hash"
        )
    requirement = ThermochemistryAnalysisRequirement(
        key=str(analysis.id),
        project_id=bundle.project.id,
        analysis_type=analysis.analysis_type,
        input_artifact_ids=analysis.input_artifact_ids,
        parameters_hash=analysis.parameters_hash,
    )
    report = reconcile_thermochemistry_analyses_from_store(
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
    """Observe current local bytes so shared freshness sees out-of-band file drift."""

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


def canonical_analysis_payload(
    *,
    root: Path,
    bundle: ProjectBundle,
    analysis: Analysis,
) -> CanonicalAnalysisPayload:
    """Open one canonical v0.8 THERMOCHEMISTRY or REACTION_DIAGRAM Artifact fail closed."""

    if analysis.analysis_type is AnalysisType.REACTION_DIAGRAM:
        _require_tool(
            analysis,
            REACTION_DIAGRAM_TOOL_NAME,
            REACTION_DIAGRAM_TOOL_VERSION,
        )
        artifact = _required_analysis_output(
            bundle,
            analysis,
            filename=REACTION_DIAGRAM_OUTPUT,
        )
        payload = verified_json_artifact(
            root=root,
            analysis=analysis,
            artifact=artifact,
            filename=REACTION_DIAGRAM_OUTPUT,
            expected_format=CANONICAL_REACTION_DIAGRAM_FORMAT,
            expected_version=CANONICAL_REACTION_DIAGRAM_VERSION,
        )
        dataset_hash = _required_string(payload, "dataset_hash")
        if analysis.parameters_hash != dataset_hash:
            raise ApplicationServiceError(
                "reaction diagram dataset hash differs from Analysis parameters_hash"
            )
        dataset = _mapping(payload.get("dataset"), "reaction diagram dataset")
        if dataset.get("result_hash") != dataset_hash:
            raise ApplicationServiceError("reaction diagram dataset self-hash differs")
        return CanonicalAnalysisPayload(artifact=artifact, payload=payload)

    if analysis.analysis_type is not AnalysisType.THERMOCHEMISTRY:
        raise ApplicationServiceError("Analysis is not a thermochemistry/reaction Analysis")
    variants: dict[tuple[str | None, str | None], tuple[str, str, int]] = {
        (HARMONIC_THERMOCHEMISTRY_TOOL_NAME, HARMONIC_THERMOCHEMISTRY_TOOL_VERSION): (
            HARMONIC_OUTPUT,
            CANONICAL_HARMONIC_THERMOCHEMISTRY_FORMAT,
            CANONICAL_HARMONIC_THERMOCHEMISTRY_VERSION,
        ),
        (IDEAL_GAS_THERMOCHEMISTRY_TOOL_NAME, IDEAL_GAS_THERMOCHEMISTRY_TOOL_VERSION): (
            GAS_OUTPUT,
            CANONICAL_IDEAL_GAS_THERMOCHEMISTRY_FORMAT,
            CANONICAL_IDEAL_GAS_THERMOCHEMISTRY_VERSION,
        ),
        (REFERENCE_CORRECTION_TOOL_NAME, REFERENCE_CORRECTION_TOOL_VERSION): (
            REFERENCE_OUTPUT,
            CANONICAL_REFERENCE_THERMOCHEMISTRY_FORMAT,
            CANONICAL_REFERENCE_THERMOCHEMISTRY_VERSION,
        ),
    }
    try:
        filename, expected_format, expected_version = variants[
            (analysis.tool, analysis.tool_version)
        ]
    except KeyError as error:
        raise ApplicationServiceError(
            "THERMOCHEMISTRY Analysis uses an unsupported canonical tool/version"
        ) from error
    artifact = _required_analysis_output(bundle, analysis, filename=filename)
    payload = verified_json_artifact(
        root=root,
        analysis=analysis,
        artifact=artifact,
        filename=filename,
        expected_format=expected_format,
        expected_version=expected_version,
    )
    receipt = _mapping(payload.get("source_receipt"), "thermochemistry source_receipt")
    receipt_hash = _required_string(payload, "source_receipt_hash")
    if analysis.parameters_hash != receipt_hash:
        raise ApplicationServiceError(
            "thermochemistry source receipt differs from Analysis parameters_hash"
        )
    if canonical_sha256(receipt) != receipt_hash:
        raise ApplicationServiceError("thermochemistry source receipt hash is inconsistent")
    result = _mapping(payload.get("result"), "thermochemistry result")
    if result.get("result_hash") != payload.get("result_hash"):
        raise ApplicationServiceError("thermochemistry result self-hash differs")
    return CanonicalAnalysisPayload(artifact=artifact, payload=payload)


def persist_analysis_graph(
    *,
    store: ProjectStore,
    bundle: ProjectBundle,
    analysis: Analysis,
    artifacts: tuple[Artifact, ...],
    provenance_records: tuple[ProvenanceRecord, ...],
    dependency_records: tuple[DependencyRecord, ...],
) -> None:
    """Atomically append an existing generic Analysis/Artifact graph and verify reopen."""

    if any(item.id == analysis.id for item in bundle.analyses):
        raise ApplicationServiceError("new thermochemistry Analysis id already exists")
    known_artifacts = {item.id for item in bundle.artifacts}
    if any(item.id in known_artifacts for item in artifacts):
        raise ApplicationServiceError("new thermochemistry Artifact id already exists")
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
        raise ApplicationServiceError("thermochemistry Analysis failed post-save verification")
    for artifact in artifacts:
        if require_artifact(reopened, artifact.id) != artifact:
            raise ApplicationServiceError("thermochemistry Artifact failed post-save verification")


def source_is_unchanged(bundle: ProjectBundle, source: ResolvedFrequencySource) -> bool:
    """Return whether every durable entity used to derive one source is still exact."""

    try:
        return (
            require_calculation(bundle, source.calculation.id) == source.calculation
            and require_attempt(bundle, source.attempt.id) == source.attempt
            and require_fingerprint(bundle, source.calculation) == source.fingerprint
            and require_snapshot(bundle, source.calculation) == source.snapshot
            and require_analysis(bundle, source.source_analysis.id) == source.source_analysis
            and require_artifact(bundle, source.source_artifact.id) == source.source_artifact
        )
    except ApplicationServiceError:
        return False


def verified_json_artifact(
    *,
    root: Path,
    analysis: Analysis,
    artifact: Artifact,
    filename: str,
    expected_format: str,
    expected_version: int,
) -> dict[str, object]:
    body = verified_local_bytes(
        root=root,
        artifact=artifact,
        expected_filename=filename,
        label=filename,
    )
    try:
        raw: object = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ApplicationServiceError(f"{filename} is not valid UTF-8 JSON") from error
    payload = _mapping(raw, filename)
    if payload.get("format") != expected_format or payload.get("version") != expected_version:
        raise ApplicationServiceError(f"{filename} has unsupported canonical format/version")
    if payload.get("analysis_id") != str(analysis.id):
        raise ApplicationServiceError(f"{filename} belongs to another Analysis")
    return payload


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


def _verify_parsed_result_payload(
    *,
    root: Path,
    calculation: Calculation,
    analysis: Analysis,
    artifact: Artifact,
    intake_hash: str,
    expected: VaspResultDocument,
) -> None:
    body = verified_local_bytes(
        root=root,
        artifact=artifact,
        expected_filename=PARSED_RESULT_OUTPUT,
        label="parsed-result.json",
    )
    try:
        raw: object = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ApplicationServiceError("parsed-result.json is not valid UTF-8 JSON") from error
    payload = _mapping(raw, "parsed-result.json")
    if (
        payload.get("format") != VASP_RESULT_DOCUMENT_FORMAT
        or payload.get("version") != VASP_RESULT_DOCUMENT_VERSION
    ):
        raise ApplicationServiceError("parsed-result.json has unsupported format/version")
    if payload.get("calculation_id") != str(calculation.id):
        raise ApplicationServiceError("parsed-result.json belongs to another Calculation")
    if payload.get("analysis_id") != str(analysis.id):
        raise ApplicationServiceError("parsed-result.json belongs to another Analysis")
    if payload.get("intake_hash") != intake_hash:
        raise ApplicationServiceError("parsed-result intake hash differs from managed source")
    if canonical_sha256(payload.get("result")) != canonical_sha256(expected):
        raise ApplicationServiceError(
            "persisted parsed-result facts differ from replayed canonical VASP parser facts"
        )


def _receipt_matches_frequency_source(
    receipt: dict[str, object], source: ResolvedFrequencySource
) -> bool:
    expected = {
        "calculation_id": str(source.calculation.id),
        "structure_snapshot_id": str(source.snapshot.id),
        "method_fingerprint_id": str(source.fingerprint.id),
        "source_analysis_id": str(source.source_analysis.id),
        "source_artifact_id": str(source.source_artifact.id),
        "source_artifact_sha256": source.source_artifact.sha256,
        "source_result_hash": canonical_sha256(source.result),
    }
    return all(receipt.get(key) == value for key, value in expected.items())


def _required_analysis_output(
    bundle: ProjectBundle,
    analysis: Analysis,
    *,
    filename: str,
) -> Artifact:
    result = _single_analysis_output(
        bundle,
        analysis,
        artifact_type=ArtifactType.DERIVED_DATASET,
        filename=filename,
    )
    assert result is not None
    return result


def _single_analysis_output(
    bundle: ProjectBundle,
    analysis: Analysis,
    *,
    artifact_type: ArtifactType,
    filename: str,
    allow_absent: bool = False,
) -> Artifact | None:
    producer = AnalysisProducerRef(analysis.id)
    matches = tuple(
        item
        for item in bundle.artifacts
        if item.producer == producer
        and item.artifact_type is artifact_type
        and PurePosixPath(item.local_path or "").name == filename
    )
    if not matches and allow_absent:
        return None
    if len(matches) != 1:
        raise ApplicationServiceError(
            f"{analysis.analysis_type.value} {filename} requires exactly one matching Artifact"
        )
    return matches[0]


def _require_tool(analysis: Analysis, tool: str, version: str) -> None:
    if (analysis.tool, analysis.tool_version) != (tool, version):
        raise ApplicationServiceError("Analysis canonical tool/version is unsupported")


def _mapping(value: object, field_name: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ApplicationServiceError(f"{field_name} must be a JSON object")
    return cast(dict[str, object], value)


def _required_string(value: dict[str, object], field_name: str) -> str:
    result = value.get(field_name)
    if not isinstance(result, str) or not result:
        raise ApplicationServiceError(f"{field_name} must be a non-blank string")
    return result


__all__ = [
    "GAS_OUTPUT",
    "HARMONIC_OUTPUT",
    "PARSED_RESULT_OUTPUT",
    "REACTION_DIAGRAM_OUTPUT",
    "REFERENCE_OUTPUT",
    "CanonicalAnalysisPayload",
    "ResolvedFrequencySource",
    "canonical_analysis_payload",
    "find_existing_thermochemistry",
    "observed_artifact_state",
    "persist_analysis_graph",
    "require_analysis",
    "require_artifact",
    "require_calculation",
    "require_fingerprint",
    "require_snapshot",
    "resolve_frequency_source",
    "source_is_unchanged",
    "thermochemistry_projection",
    "thermochemistry_projection_payload",
    "verified_json_artifact",
    "verified_local_bytes",
]

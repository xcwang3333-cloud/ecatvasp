"""Explicit gas-reference correction and phase-reference materialization for v0.8 Block 4.

This layer never mutates raw harmonic or ideal-gas thermochemistry. It derives a new
reference dataset whose identity includes every correction value, policy version, evidence
record, target phase, and optional content-addressed evidence Artifact.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from enum import StrEnum
from math import isfinite
from pathlib import Path, PurePosixPath

from ecatvasp.domain import (
    Analysis,
    AnalysisProducerRef,
    AnalysisStatus,
    AnalysisType,
    Artifact,
    ArtifactAvailability,
    ArtifactId,
    ArtifactType,
    RetrievalPolicy,
    canonical_json,
    canonical_sha256,
)
from ecatvasp.provenance import (
    DependencyKind,
    DependencyRecord,
    ProvenanceRecord,
    scientific_hash,
)
from ecatvasp.thermo.contracts import (
    ThermochemistryCorrection,
    ThermochemistryCorrectionKind,
    ThermochemistryResult,
    ThermochemistrySubjectKind,
)
from ecatvasp.thermo.gas import (
    CANONICAL_IDEAL_GAS_THERMOCHEMISTRY_FORMAT,
    CANONICAL_IDEAL_GAS_THERMOCHEMISTRY_VERSION,
    IDEAL_GAS_THERMOCHEMISTRY_TOOL_NAME,
    IDEAL_GAS_THERMOCHEMISTRY_TOOL_VERSION,
    GasReferenceDefinition,
    GasReferenceSpecies,
)

REFERENCE_CORRECTION_TOOL_NAME = "ecatvasp.thermo.reference-correction"
REFERENCE_CORRECTION_TOOL_VERSION = "1"
CANONICAL_REFERENCE_THERMOCHEMISTRY_FORMAT = (
    "ecatvasp-canonical-reference-thermochemistry"
)
CANONICAL_REFERENCE_THERMOCHEMISTRY_VERSION = 1


class ReferenceCorrectionError(ValueError):
    """Raised when a corrected molecular reference cannot be derived unambiguously."""


class ReferencePhase(StrEnum):
    """Target physical reference state supported by the Block 4 MVP."""

    IDEAL_GAS = "ideal_gas"
    LIQUID_WATER = "liquid_water"


class CorrectionEvidenceKind(StrEnum):
    """Origin class for a correction value or reference policy."""

    USER_DECLARED = "user_declared"
    LITERATURE = "literature"
    EXPERIMENTAL = "experimental"
    CALIBRATION = "calibration"


def _require_text(value: str, field_name: str) -> None:
    if not value.strip():
        raise ReferenceCorrectionError(f"{field_name} must not be blank")


def _normalized_sha256(value: str, field_name: str) -> str:
    normalized = value.lower()
    valid_hex = all(character in "0123456789abcdef" for character in normalized)
    if len(normalized) != 64 or not valid_hex:
        raise ReferenceCorrectionError(
            f"{field_name} must be a 64-character hexadecimal SHA-256 digest"
        )
    return normalized


@dataclass(frozen=True, slots=True)
class CorrectionEvidence:
    """Auditable origin of one explicit correction policy/value.

    `source_id` may be an internal policy identifier, DOI-like identifier, dataset name,
    calibration name, or another stable reference chosen by the user. When an exact project
    Artifact carries the evidence, both `artifact_id` and `artifact_sha256` are required.
    """

    kind: CorrectionEvidenceKind
    source_id: str
    source_version: str
    citation: str | None = None
    artifact_id: ArtifactId | None = None
    artifact_sha256: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.source_id, "source_id")
        _require_text(self.source_version, "source_version")
        if self.citation is not None:
            _require_text(self.citation, "citation")
        if (self.artifact_id is None) != (self.artifact_sha256 is None):
            raise ReferenceCorrectionError(
                "correction evidence artifact_id and artifact_sha256 must be supplied together"
            )
        if self.artifact_sha256 is not None:
            object.__setattr__(
                self,
                "artifact_sha256",
                _normalized_sha256(self.artifact_sha256, "artifact_sha256"),
            )


@dataclass(frozen=True, slots=True)
class ReferenceCorrectionPolicy:
    """One additive correction plus its independently auditable evidence identity."""

    correction: ThermochemistryCorrection
    evidence: CorrectionEvidence
    note: str | None = None

    def __post_init__(self) -> None:
        if self.note is not None:
            _require_text(self.note, "note")

    @property
    def content_hash(self) -> str:
        """Return deterministic policy/evidence identity."""

        return canonical_sha256(self)


@dataclass(frozen=True, slots=True)
class GasReferenceAdjustmentIdentity:
    """Complete identity of a derived gas or liquid-water molecular reference."""

    reference: GasReferenceDefinition
    target_phase: ReferencePhase
    policies: tuple[ReferenceCorrectionPolicy, ...]

    def __post_init__(self) -> None:
        if not self.policies:
            raise ReferenceCorrectionError(
                "reference adjustment requires at least one explicit correction policy"
            )
        ordered = tuple(
            sorted(
                self.policies,
                key=lambda item: (
                    item.correction.kind.value,
                    item.correction.policy_id,
                    item.correction.policy_version,
                    item.correction.label,
                    item.evidence.kind.value,
                    item.evidence.source_id,
                    item.evidence.source_version,
                ),
            )
        )
        correction_keys = tuple(
            (
                item.correction.kind,
                item.correction.policy_id,
                item.correction.policy_version,
                item.correction.label,
            )
            for item in ordered
        )
        if len(correction_keys) != len(set(correction_keys)):
            raise ReferenceCorrectionError(
                "reference correction identities must be unique"
            )
        phase_change_count = sum(
            item.correction.kind is ThermochemistryCorrectionKind.PHASE_CHANGE
            for item in ordered
        )
        if self.target_phase is ReferencePhase.IDEAL_GAS:
            if phase_change_count:
                raise ReferenceCorrectionError(
                    "ideal-gas target reference must not carry a phase-change correction"
                )
        elif self.target_phase is ReferencePhase.LIQUID_WATER:
            if self.reference.species is not GasReferenceSpecies.H2O:
                raise ReferenceCorrectionError(
                    "LIQUID_WATER target phase is only valid for the H2O reference"
                )
            if phase_change_count != 1:
                raise ReferenceCorrectionError(
                    "LIQUID_WATER requires exactly one explicit PHASE_CHANGE correction"
                )
        else:
            raise ReferenceCorrectionError("unsupported reference target phase")
        object.__setattr__(self, "policies", ordered)

    @property
    def corrections(self) -> tuple[ThermochemistryCorrection, ...]:
        """Return correction terms in the canonical policy order."""

        return tuple(item.correction for item in self.policies)

    @property
    def parameters_hash(self) -> str:
        """Return the complete correction/reference identity hash."""

        return canonical_sha256(self)


@dataclass(frozen=True, slots=True)
class ReferenceThermochemistryResult:
    """Corrected molecular reference without mutating its raw thermochemistry parent."""

    adjustment: GasReferenceAdjustmentIdentity
    source_result_hash: str
    source_gibbs_free_energy_ev: float
    corrected_gibbs_free_energy_ev: float = field(init=False)
    result_hash: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_result_hash",
            _normalized_sha256(self.source_result_hash, "source_result_hash"),
        )
        if not isfinite(self.source_gibbs_free_energy_ev):
            raise ReferenceCorrectionError(
                "source_gibbs_free_energy_ev must be finite"
            )
        corrected = self.source_gibbs_free_energy_ev + sum(
            item.value_ev for item in self.adjustment.corrections
        )
        if not isfinite(corrected):
            raise ReferenceCorrectionError(
                "corrected molecular reference Gibbs energy must be finite"
            )
        object.__setattr__(self, "corrected_gibbs_free_energy_ev", corrected)
        object.__setattr__(
            self,
            "result_hash",
            canonical_sha256(
                {
                    "adjustment": self.adjustment,
                    "source_result_hash": self.source_result_hash,
                    "source_gibbs_free_energy_ev": self.source_gibbs_free_energy_ev,
                    "corrected_gibbs_free_energy_ev": corrected,
                }
            ),
        )


@dataclass(frozen=True, slots=True)
class DurableReferenceThermochemistry:
    """Durable reference-correction Analysis and its exact scientific dependencies."""

    analysis: Analysis
    artifact: Artifact
    result: ReferenceThermochemistryResult
    provenance_records: tuple[ProvenanceRecord, ...]
    dependency_records: tuple[DependencyRecord, ...]


def apply_reference_corrections(
    *,
    source_result: ThermochemistryResult,
    adjustment: GasReferenceAdjustmentIdentity,
) -> ReferenceThermochemistryResult:
    """Apply only explicitly supplied reference corrections to one raw gas result."""

    if source_result.identity.subject_kind is not ThermochemistrySubjectKind.GAS:
        raise ReferenceCorrectionError(
            "molecular reference corrections require raw GAS thermochemistry"
        )
    if source_result.identity.corrections or source_result.components.corrections:
        raise ReferenceCorrectionError(
            "reference correction layer accepts only uncorrected raw thermochemistry"
        )
    return ReferenceThermochemistryResult(
        adjustment=adjustment,
        source_result_hash=source_result.result_hash,
        source_gibbs_free_energy_ev=source_result.gibbs_free_energy_ev,
    )


def materialize_reference_thermochemistry(
    *,
    project_root: Path | str,
    source_analysis: Analysis,
    source_artifact: Artifact,
    source_result: ThermochemistryResult,
    adjustment: GasReferenceAdjustmentIdentity,
    evidence_artifacts: tuple[Artifact, ...] = (),
) -> DurableReferenceThermochemistry:
    """Persist one corrected molecular reference while preserving the raw gas parent."""

    _validate_source_contract(
        source_analysis=source_analysis,
        source_artifact=source_artifact,
    )
    root = Path(project_root).resolve()
    if not root.is_dir():
        raise ReferenceCorrectionError("project_root must be an existing directory")
    _verify_ideal_gas_source(
        root=root,
        source_analysis=source_analysis,
        source_artifact=source_artifact,
        source_result=source_result,
        reference=adjustment.reference,
    )
    evidence_by_id = _validate_evidence_artifacts(
        adjustment=adjustment,
        evidence_artifacts=evidence_artifacts,
    )
    result = apply_reference_corrections(
        source_result=source_result,
        adjustment=adjustment,
    )
    ordered_evidence_artifacts = tuple(
        evidence_by_id[artifact_id]
        for artifact_id in sorted(evidence_by_id, key=str)
    )
    input_artifact_ids = (
        source_artifact.id,
        *(item.id for item in ordered_evidence_artifacts),
    )
    source_receipt = {
        "format": CANONICAL_REFERENCE_THERMOCHEMISTRY_FORMAT,
        "version": CANONICAL_REFERENCE_THERMOCHEMISTRY_VERSION,
        "source_analysis_id": source_analysis.id,
        "source_artifact_id": source_artifact.id,
        "source_artifact_sha256": source_artifact.sha256,
        "source_result_hash": source_result.result_hash,
        "source_gibbs_free_energy_ev": source_result.gibbs_free_energy_ev,
        "adjustment": adjustment,
        "adjustment_parameters_hash": adjustment.parameters_hash,
        "evidence_artifacts": tuple(
            {
                "artifact_id": item.id,
                "artifact_sha256": item.sha256,
            }
            for item in ordered_evidence_artifacts
        ),
    }
    source_receipt_hash = canonical_sha256(source_receipt)
    analysis = Analysis(
        project_id=source_analysis.project_id,
        analysis_type=AnalysisType.THERMOCHEMISTRY,
        input_artifact_ids=input_artifact_ids,
        status=AnalysisStatus.COMPLETED,
        tool=REFERENCE_CORRECTION_TOOL_NAME,
        tool_version=REFERENCE_CORRECTION_TOOL_VERSION,
        parameters_hash=source_receipt_hash,
    )
    payload = {
        "format": CANONICAL_REFERENCE_THERMOCHEMISTRY_FORMAT,
        "version": CANONICAL_REFERENCE_THERMOCHEMISTRY_VERSION,
        "analysis_id": analysis.id,
        "source_receipt": source_receipt,
        "source_receipt_hash": source_receipt_hash,
        "result_hash": result.result_hash,
        "result": result,
    }
    artifact = _write_result_artifact(
        root=root,
        analysis=analysis,
        payload=payload,
    )
    provenance_records = (
        ProvenanceRecord(
            subject_id=analysis.id,
            tool=REFERENCE_CORRECTION_TOOL_NAME,
            tool_version=REFERENCE_CORRECTION_TOOL_VERSION,
            parameters_hash=analysis.parameters_hash,
        ),
        ProvenanceRecord(
            subject_id=artifact.id,
            tool=REFERENCE_CORRECTION_TOOL_NAME,
            tool_version=REFERENCE_CORRECTION_TOOL_VERSION,
            parameters_hash=artifact.sha256,
        ),
    )
    dependency_records = _dependency_records(
        source_analysis=source_analysis,
        source_artifact=source_artifact,
        evidence_artifacts=ordered_evidence_artifacts,
        analysis=analysis,
        artifact=artifact,
    )
    return DurableReferenceThermochemistry(
        analysis=analysis,
        artifact=artifact,
        result=result,
        provenance_records=provenance_records,
        dependency_records=dependency_records,
    )


def _validate_source_contract(
    *,
    source_analysis: Analysis,
    source_artifact: Artifact,
) -> None:
    if source_analysis.analysis_type is not AnalysisType.THERMOCHEMISTRY:
        raise ReferenceCorrectionError(
            "reference correction source Analysis must be THERMOCHEMISTRY"
        )
    if source_analysis.status is not AnalysisStatus.COMPLETED:
        raise ReferenceCorrectionError(
            "reference correction source Analysis must be completed"
        )
    if (
        source_analysis.tool != IDEAL_GAS_THERMOCHEMISTRY_TOOL_NAME
        or source_analysis.tool_version != IDEAL_GAS_THERMOCHEMISTRY_TOOL_VERSION
    ):
        raise ReferenceCorrectionError(
            "reference correction source must be raw Block 3 ideal-gas thermochemistry"
        )
    if source_artifact.artifact_type is not ArtifactType.DERIVED_DATASET:
        raise ReferenceCorrectionError(
            "reference correction source Artifact must be DERIVED_DATASET"
        )
    if (
        not isinstance(source_artifact.producer, AnalysisProducerRef)
        or source_artifact.producer.id != source_analysis.id
    ):
        raise ReferenceCorrectionError(
            "reference correction source Artifact producer differs from Analysis"
        )
    if source_artifact.availability not in {
        ArtifactAvailability.LOCAL,
        ArtifactAvailability.BOTH,
    }:
        raise ReferenceCorrectionError(
            "reference correction source Artifact must be locally available"
        )
    if source_artifact.local_path is None:
        raise ReferenceCorrectionError(
            "reference correction source Artifact requires local_path"
        )
    if source_artifact.sha256 is None or source_artifact.size_bytes is None:
        raise ReferenceCorrectionError(
            "reference correction source Artifact requires SHA-256 and byte size"
        )


def _verify_ideal_gas_source(
    *,
    root: Path,
    source_analysis: Analysis,
    source_artifact: Artifact,
    source_result: ThermochemistryResult,
    reference: GasReferenceDefinition,
) -> None:
    if source_artifact.local_path is None:
        raise ReferenceCorrectionError("source gas Artifact requires local_path")
    relative = PurePosixPath(source_artifact.local_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ReferenceCorrectionError(
            "source gas Artifact path must be project-relative"
        )
    absolute = (root / Path(*relative.parts)).resolve()
    if not absolute.is_relative_to(root) or not absolute.is_file():
        raise ReferenceCorrectionError("source gas Artifact file is unavailable")
    body = absolute.read_bytes()
    if source_artifact.size_bytes != len(body):
        raise ReferenceCorrectionError("source gas Artifact byte size differs")
    if source_artifact.sha256 != hashlib.sha256(body).hexdigest():
        raise ReferenceCorrectionError("source gas Artifact SHA-256 differs")
    try:
        raw_payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReferenceCorrectionError(
            "source gas Artifact is not valid UTF-8 JSON"
        ) from error
    payload = _mapping(raw_payload, "source gas payload")
    if payload.get("format") != CANONICAL_IDEAL_GAS_THERMOCHEMISTRY_FORMAT:
        raise ReferenceCorrectionError("source gas Artifact format is unsupported")
    if payload.get("version") != CANONICAL_IDEAL_GAS_THERMOCHEMISTRY_VERSION:
        raise ReferenceCorrectionError("source gas Artifact version is unsupported")
    if payload.get("analysis_id") != str(source_analysis.id):
        raise ReferenceCorrectionError("source gas Artifact belongs to another Analysis")
    receipt = _mapping(payload.get("source_receipt"), "source gas receipt")
    receipt_hash = payload.get("source_receipt_hash")
    if (
        not isinstance(receipt_hash, str)
        or receipt_hash != source_analysis.parameters_hash
        or canonical_sha256(receipt) != receipt_hash
    ):
        raise ReferenceCorrectionError("source gas receipt differs from source Analysis")
    if receipt.get("reference_content_hash") != reference.content_hash:
        raise ReferenceCorrectionError(
            "declared reference differs from raw ideal-gas source reference"
        )
    if canonical_sha256(receipt.get("reference")) != reference.content_hash:
        raise ReferenceCorrectionError(
            "raw ideal-gas source reference payload is inconsistent"
        )
    if payload.get("result_hash") != source_result.result_hash:
        raise ReferenceCorrectionError("source gas result hash differs")
    if canonical_sha256(payload.get("result")) != canonical_sha256(source_result):
        raise ReferenceCorrectionError(
            "in-memory source thermochemistry differs from durable gas Artifact"
        )


def _validate_evidence_artifacts(
    *,
    adjustment: GasReferenceAdjustmentIdentity,
    evidence_artifacts: tuple[Artifact, ...],
) -> dict[ArtifactId, Artifact]:
    expected: dict[ArtifactId, str] = {}
    for policy in adjustment.policies:
        evidence = policy.evidence
        if evidence.artifact_id is None:
            continue
        if evidence.artifact_sha256 is None:
            raise ReferenceCorrectionError(
                "correction evidence Artifact requires declared SHA-256"
            )
        previous = expected.get(evidence.artifact_id)
        if previous is not None and previous != evidence.artifact_sha256:
            raise ReferenceCorrectionError(
                "one evidence Artifact cannot carry multiple declared hashes"
            )
        expected[evidence.artifact_id] = evidence.artifact_sha256

    provided = {item.id: item for item in evidence_artifacts}
    if len(provided) != len(evidence_artifacts):
        raise ReferenceCorrectionError("evidence_artifacts must have unique Artifact ids")
    if set(provided) != set(expected):
        raise ReferenceCorrectionError(
            "provided evidence Artifacts must exactly match correction evidence bindings"
        )
    for artifact_id, expected_hash in expected.items():
        artifact = provided[artifact_id]
        if artifact.sha256 is None:
            raise ReferenceCorrectionError("evidence Artifact requires SHA-256")
        if artifact.sha256.lower() != expected_hash:
            raise ReferenceCorrectionError(
                "evidence Artifact SHA-256 differs from correction evidence identity"
            )
    return provided


def _dependency_records(
    *,
    source_analysis: Analysis,
    source_artifact: Artifact,
    evidence_artifacts: tuple[Artifact, ...],
    analysis: Analysis,
    artifact: Artifact,
) -> tuple[DependencyRecord, ...]:
    records = [
        DependencyRecord(
            upstream_id=source_analysis.id,
            downstream_id=analysis.id,
            kind=DependencyKind.SCIENTIFIC,
            role="raw_gas_thermochemistry_analysis",
            recorded_hash=scientific_hash(source_analysis),
        ),
        DependencyRecord(
            upstream_id=source_artifact.id,
            downstream_id=analysis.id,
            kind=DependencyKind.SCIENTIFIC,
            role="raw_gas_thermochemistry",
            recorded_hash=scientific_hash(source_artifact),
        ),
    ]
    records.extend(
        DependencyRecord(
            upstream_id=item.id,
            downstream_id=analysis.id,
            kind=DependencyKind.SCIENTIFIC,
            role=f"correction_evidence:{item.id}",
            recorded_hash=scientific_hash(item),
        )
        for item in evidence_artifacts
    )
    records.append(
        DependencyRecord(
            upstream_id=analysis.id,
            downstream_id=artifact.id,
            kind=DependencyKind.SCIENTIFIC,
            role="corrected_reference_thermochemistry",
            recorded_hash=scientific_hash(analysis),
        )
    )
    return tuple(records)


def _write_result_artifact(
    *,
    root: Path,
    analysis: Analysis,
    payload: object,
) -> Artifact:
    relative = (
        Path("analyses")
        / str(analysis.id)
        / "canonical-reference-thermochemistry.json"
    )
    absolute = (root / relative).resolve()
    if not absolute.is_relative_to(root):
        raise ReferenceCorrectionError(
            "reference thermochemistry output resolves outside project_root"
        )
    text = canonical_json(payload) + "\n"
    absolute.parent.mkdir(parents=True, exist_ok=True)
    if absolute.exists():
        if not absolute.is_file():
            raise ReferenceCorrectionError(
                "reference thermochemistry output is not a regular file"
            )
        if absolute.read_text(encoding="utf-8") != text:
            raise ReferenceCorrectionError(
                "reference thermochemistry output already has different content"
            )
    else:
        temporary = absolute.with_name(f".{absolute.name}.tmp")
        try:
            temporary.write_text(text, encoding="utf-8")
            os.replace(temporary, absolute)
        finally:
            if temporary.exists():
                temporary.unlink()
    body = text.encode("utf-8")
    return Artifact(
        artifact_type=ArtifactType.DERIVED_DATASET,
        producer=AnalysisProducerRef(analysis.id),
        availability=ArtifactAvailability.LOCAL,
        retrieval_policy=RetrievalPolicy.ALWAYS,
        local_path=relative.as_posix(),
        size_bytes=len(body),
        sha256=hashlib.sha256(body).hexdigest(),
    )


def _mapping(value: object, field_name: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(
        not isinstance(key, str) for key in value
    ):
        raise ReferenceCorrectionError(f"{field_name} must be a JSON object")
    return value

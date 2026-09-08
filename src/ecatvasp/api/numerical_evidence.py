"""Durable validated numerical-evidence handoff for v1.1 application services.

The artifact records evidence that has already passed the Calculation Wizard's
scientific validation gate. It is not a new numerical-lock authority and does
not infer convergence. ProjectStore and the existing VASP validation contracts
remain authoritative when the evidence is consumed again.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import cast

from ecatvasp.api.application import ApplicationServiceError
from ecatvasp.api.calculation_wizard import WizardNumericalEvidence
from ecatvasp.domain import (
    Artifact,
    ArtifactAvailability,
    ArtifactType,
    Calculation,
    CalculationId,
    CalculationProducerRef,
    MethodFingerprint,
    MethodFingerprintId,
    RetrievalPolicy,
    canonical_json,
    canonical_sha256,
)
from ecatvasp.provenance import ProvenanceRecord
from ecatvasp.storage import ProjectBundle, ProjectStore
from ecatvasp.vasp.contracts import VaspSystemKind
from ecatvasp.vasp.kpoints import KPointValidationEvidence
from ecatvasp.vasp.potcar import EncCutValidationEvidence

_EVIDENCE_FILENAME = "numerical-evidence.json"
_EVIDENCE_KIND = "ecatvasp.validated-numerical-evidence"
_EVIDENCE_SCHEMA_VERSION = 1


def persist_validated_numerical_evidence(
    *,
    store: ProjectStore,
    calculation_id: CalculationId,
    evidence: WizardNumericalEvidence,
) -> Artifact:
    """Persist or reuse validated evidence as one Calculation-produced artifact."""

    bundle = store.open()
    calculation = _require_calculation(bundle, calculation_id)
    fingerprint = _require_fingerprint(bundle, calculation.method_fingerprint_id)
    _validate_evidence_identity(evidence, fingerprint.core_method_hash)
    payload = {
        "schema_version": _EVIDENCE_SCHEMA_VERSION,
        "kind": _EVIDENCE_KIND,
        "calculation_id": calculation.id,
        "method_fingerprint_id": fingerprint.id,
        "core_method_hash": fingerprint.core_method_hash,
        "protocol_hash": fingerprint.protocol_hash,
        "evidence": evidence,
    }
    text = canonical_json(payload) + "\n"
    body = text.encode("utf-8")
    relative = _relative_path(calculation.id)
    matches = _matching_artifacts(bundle, calculation.id, relative)
    if len(matches) > 1:
        raise ApplicationServiceError(
            "Calculation has duplicate validated numerical-evidence artifacts"
        )
    if matches:
        artifact = matches[0]
        _verify_artifact_file(store.root, artifact, expected_body=body)
        return artifact

    path = (store.root / relative).resolve()
    root = store.root.resolve()
    if root not in path.parents:
        raise ApplicationServiceError("numerical-evidence path escaped project root")
    if path.exists():
        raise ApplicationServiceError(
            "untracked numerical-evidence file already exists for Calculation"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    artifact = Artifact(
        artifact_type=ArtifactType.DERIVED_DATASET,
        producer=CalculationProducerRef(calculation.id),
        availability=ArtifactAvailability.LOCAL,
        retrieval_policy=RetrievalPolicy.ALWAYS,
        local_path=relative.as_posix(),
        size_bytes=len(body),
        sha256=hashlib.sha256(body).hexdigest(),
    )
    provenance = ProvenanceRecord(
        subject_id=artifact.id,
        tool="ecatvasp.api.calculation-wizard",
        tool_version="1",
        parameters_hash=canonical_sha256(evidence),
        method_fingerprint_id=fingerprint.id,
    )
    try:
        store.save(
            replace(
                bundle,
                artifacts=(*bundle.artifacts, artifact),
                provenance_records=(*bundle.provenance_records, provenance),
            )
        )
    except Exception:
        path.unlink(missing_ok=True)
        raise

    reopened = store.open()
    persisted = _matching_artifacts(reopened, calculation.id, relative)
    if len(persisted) != 1:
        raise ApplicationServiceError(
            "validated numerical-evidence artifact is missing after durable save"
        )
    _verify_artifact_file(store.root, persisted[0], expected_body=body)
    return persisted[0]


def resolve_validated_numerical_evidence(
    *,
    project_root: Path | str,
    bundle: ProjectBundle,
    calculation_id: CalculationId,
) -> WizardNumericalEvidence:
    """Load the exact validated evidence attached to one durable Calculation."""

    calculation = _require_calculation(bundle, calculation_id)
    fingerprint = _require_fingerprint(bundle, calculation.method_fingerprint_id)
    relative = _relative_path(calculation.id)
    matches = _matching_artifacts(bundle, calculation.id, relative)
    if len(matches) != 1:
        raise ApplicationServiceError(
            "Calculation requires exactly one durable validated numerical-evidence artifact"
        )
    artifact = matches[0]
    body = _verify_artifact_file(Path(project_root), artifact)
    try:
        raw = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ApplicationServiceError(
            "validated numerical-evidence artifact is not valid UTF-8 JSON"
        ) from error
    if not isinstance(raw, dict):
        raise ApplicationServiceError("validated numerical-evidence payload must be an object")
    payload = cast(dict[str, object], raw)
    if payload.get("schema_version") != _EVIDENCE_SCHEMA_VERSION:
        raise ApplicationServiceError("validated numerical-evidence schema is unsupported")
    if payload.get("kind") != _EVIDENCE_KIND:
        raise ApplicationServiceError("validated numerical-evidence kind is invalid")
    if payload.get("calculation_id") != str(calculation.id):
        raise ApplicationServiceError("validated numerical evidence belongs to another Calculation")
    if payload.get("method_fingerprint_id") != str(fingerprint.id):
        raise ApplicationServiceError(
            "validated numerical evidence references another MethodFingerprint"
        )
    if payload.get("core_method_hash") != fingerprint.core_method_hash:
        raise ApplicationServiceError("validated numerical evidence core-method drift detected")
    if payload.get("protocol_hash") != fingerprint.protocol_hash:
        raise ApplicationServiceError("validated numerical evidence protocol drift detected")
    evidence = _decode_evidence(payload.get("evidence"))
    _validate_evidence_identity(evidence, fingerprint.core_method_hash)
    return evidence


def _decode_evidence(value: object) -> WizardNumericalEvidence:
    if not isinstance(value, dict):
        raise ApplicationServiceError("validated numerical evidence body is missing")
    raw = cast(dict[str, object], value)
    encut_raw = _mapping(raw, "encut")
    tested = encut_raw.get("tested_encuts_ev")
    if not isinstance(tested, list):
        raise ApplicationServiceError("validated ENCUT test series is invalid")
    try:
        encut = EncCutValidationEvidence(
            core_method_hash=_string(encut_raw, "core_method_hash"),
            potcar_spec_hash=_string(encut_raw, "potcar_spec_hash"),
            tested_encuts_ev=tuple(_number(item) for item in tested),
            selected_encut_ev=_number(encut_raw.get("selected_encut_ev")),
            analysis_hash=_string(encut_raw, "analysis_hash"),
        )
        kpoints_value = raw.get("kpoints")
        kpoints = None
        if kpoints_value is not None:
            if not isinstance(kpoints_value, dict):
                raise ValueError("kpoints evidence must be an object")
            kpoint_raw = cast(dict[str, object], kpoints_value)
            tested_hashes = kpoint_raw.get("tested_plan_hashes")
            if not isinstance(tested_hashes, list) or any(
                not isinstance(item, str) for item in tested_hashes
            ):
                raise ValueError("tested_plan_hashes must be a string list")
            kpoints = KPointValidationEvidence(
                core_method_hash=_string(kpoint_raw, "core_method_hash"),
                system_kind=VaspSystemKind(_string(kpoint_raw, "system_kind")),
                tested_plan_hashes=tuple(tested_hashes),
                selected_plan_hash=_string(kpoint_raw, "selected_plan_hash"),
                analysis_hash=_string(kpoint_raw, "analysis_hash"),
            )
        return WizardNumericalEvidence(encut=encut, kpoints=kpoints)
    except ValueError as error:
        raise ApplicationServiceError(
            "validated numerical-evidence payload is invalid"
        ) from error


def _matching_artifacts(
    bundle: ProjectBundle,
    calculation_id: CalculationId,
    relative: Path,
) -> tuple[Artifact, ...]:
    producer = CalculationProducerRef(calculation_id)
    return tuple(
        item
        for item in bundle.artifacts
        if item.artifact_type is ArtifactType.DERIVED_DATASET
        and item.producer == producer
        and item.local_path == relative.as_posix()
        and item.availability in {ArtifactAvailability.LOCAL, ArtifactAvailability.BOTH}
    )


def _verify_artifact_file(
    project_root: Path | str,
    artifact: Artifact,
    *,
    expected_body: bytes | None = None,
) -> bytes:
    if artifact.local_path is None or artifact.sha256 is None or artifact.size_bytes is None:
        raise ApplicationServiceError(
            "validated numerical-evidence artifact lacks local integrity metadata"
        )
    root = Path(project_root).resolve()
    path = (root / artifact.local_path).resolve()
    if root not in path.parents or not path.is_file():
        raise ApplicationServiceError(
            "validated numerical-evidence artifact path is missing or escaped project root"
        )
    body = path.read_bytes()
    if len(body) != artifact.size_bytes:
        raise ApplicationServiceError("validated numerical-evidence size drift detected")
    if hashlib.sha256(body).hexdigest() != artifact.sha256:
        raise ApplicationServiceError("validated numerical-evidence SHA-256 drift detected")
    if expected_body is not None and body != expected_body:
        raise ApplicationServiceError(
            "durable validated numerical evidence differs from supplied evidence"
        )
    return body


def _relative_path(calculation_id: CalculationId) -> Path:
    return Path("artifacts") / "calculations" / str(calculation_id) / _EVIDENCE_FILENAME


def _require_calculation(
    bundle: ProjectBundle,
    calculation_id: CalculationId,
) -> Calculation:
    matches = tuple(item for item in bundle.calculations if item.id == calculation_id)
    if len(matches) != 1:
        raise ApplicationServiceError("Calculation is absent or duplicated")
    return matches[0]


def _require_fingerprint(
    bundle: ProjectBundle,
    fingerprint_id: MethodFingerprintId,
) -> MethodFingerprint:
    matches = tuple(item for item in bundle.method_fingerprints if item.id == fingerprint_id)
    if len(matches) != 1:
        raise ApplicationServiceError("MethodFingerprint is absent or duplicated")
    return matches[0]


def _validate_evidence_identity(
    evidence: WizardNumericalEvidence,
    core_method_hash: str,
) -> None:
    if evidence.encut.core_method_hash != core_method_hash:
        raise ApplicationServiceError(
            "validated ENCUT evidence does not match Calculation core method"
        )
    if evidence.kpoints is not None and evidence.kpoints.core_method_hash != core_method_hash:
        raise ApplicationServiceError(
            "validated k-point evidence does not match Calculation core method"
        )


def _mapping(raw: dict[str, object], key: str) -> dict[str, object]:
    value = raw.get(key)
    if not isinstance(value, dict):
        raise ApplicationServiceError(f"validated numerical evidence {key} must be an object")
    return cast(dict[str, object], value)


def _string(raw: dict[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise ApplicationServiceError(
            f"validated numerical evidence {key} must be a non-empty string"
        )
    return value


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ApplicationServiceError("validated numerical evidence numeric field is invalid")
    return float(value)


__all__ = [
    "persist_validated_numerical_evidence",
    "resolve_validated_numerical_evidence",
]

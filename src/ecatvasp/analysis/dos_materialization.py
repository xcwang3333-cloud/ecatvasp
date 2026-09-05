"""Durable canonical DOS/PDOS analysis materialization for v0.7 Block 3.

This layer consumes an already parsed ``CanonicalDosIntake``. It verifies exact
managed source ownership and content, then materializes one immutable DOS Analysis
and one analysis-produced canonical dataset. Parsing, convergence classification,
and workflow orchestration remain outside this module.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import cast
from uuid import UUID

from ecatvasp.analysis.doscar import CanonicalDosIntake
from ecatvasp.analysis.electronic import (
    CanonicalDosResult,
    DosSeries,
    ElectronicEnergyAxis,
    ElectronicEnergyReference,
    OrbitalChannel,
    ProjectionScope,
    SpinChannel,
)
from ecatvasp.domain import (
    Analysis,
    AnalysisProducerRef,
    AnalysisStatus,
    AnalysisType,
    Artifact,
    ArtifactAvailability,
    ArtifactType,
    Calculation,
    CalculationProducerRef,
    CalculationScientificStatus,
    CalculationType,
    ExecutionAttempt,
    ExecutionAttemptProducerRef,
    RetrievalPolicy,
    canonical_json,
    canonical_sha256,
)
from ecatvasp.domain.ids import AtomUid, StructureSnapshotId
from ecatvasp.provenance import (
    DependencyKind,
    DependencyRecord,
    ProvenanceRecord,
    scientific_hash,
)

CANONICAL_DOS_ARTIFACT_FORMAT = "ecatvasp-canonical-dos-result"
CANONICAL_DOS_ARTIFACT_VERSION = 1
DOS_MATERIALIZER_NAME = "ecatvasp.analysis.dos-materializer"
DOS_MATERIALIZER_VERSION = "1"


class DosMaterializationError(ValueError):
    """Raised when canonical DOS facts cannot be persisted without guessing."""


@dataclass(frozen=True, slots=True)
class DurableDosMaterialization:
    """Durable Analysis/Artifact/provenance chain for one canonical DOS dataset."""

    analysis: Analysis
    artifact: Artifact
    provenance_records: tuple[ProvenanceRecord, ...]
    dependency_records: tuple[DependencyRecord, ...]


@dataclass(frozen=True, slots=True)
class _VerifiedSource:
    artifact: Artifact
    sha256: str
    size_bytes: int


def materialize_canonical_dos_analysis(
    *,
    project_root: Path | str,
    calculation: Calculation,
    execution_attempt: ExecutionAttempt,
    doscar_artifact: Artifact,
    atom_index_map_artifact: Artifact,
    intake: CanonicalDosIntake,
) -> DurableDosMaterialization:
    """Materialize parsed DOS/PDOS facts into the durable scientific provenance DAG."""

    _validate_identity(
        calculation=calculation,
        execution_attempt=execution_attempt,
        doscar_artifact=doscar_artifact,
        atom_index_map_artifact=atom_index_map_artifact,
        intake=intake,
    )
    root = Path(project_root).resolve()
    if not root.is_dir():
        raise DosMaterializationError("project_root must be an existing directory")

    doscar = _verify_local_source(
        root=root,
        artifact=doscar_artifact,
        expected_type=ArtifactType.DOSCAR,
        expected_sha256=intake.doscar_sha256,
        expected_basename="DOSCAR",
        label="DOSCAR",
    )
    atom_map = _verify_local_source(
        root=root,
        artifact=atom_index_map_artifact,
        expected_type=ArtifactType.DERIVED_DATASET,
        expected_sha256=intake.result.atom_index_map_sha256,
        expected_basename="atom-index-map.json",
        label="atom-index-map.json",
    )

    source_receipt = {
        "format": CANONICAL_DOS_ARTIFACT_FORMAT,
        "version": CANONICAL_DOS_ARTIFACT_VERSION,
        "parser_name": intake.parser_name,
        "parser_version": intake.parser_version,
        "structure_snapshot_id": intake.result.structure_snapshot_id,
        "doscar_artifact_id": doscar.artifact.id,
        "doscar_sha256": doscar.sha256,
        "atom_index_map_artifact_id": atom_map.artifact.id,
        "atom_index_map_sha256": atom_map.sha256,
    }
    source_receipt_hash = canonical_sha256(source_receipt)
    analysis = Analysis(
        project_id=calculation.project_id,
        analysis_type=AnalysisType.DOS,
        input_artifact_ids=(doscar.artifact.id, atom_map.artifact.id),
        status=AnalysisStatus.COMPLETED,
        tool=intake.parser_name,
        tool_version=intake.parser_version,
        parameters_hash=source_receipt_hash,
    )

    payload = {
        "format": CANONICAL_DOS_ARTIFACT_FORMAT,
        "version": CANONICAL_DOS_ARTIFACT_VERSION,
        "calculation_id": calculation.id,
        "analysis_id": analysis.id,
        "source_receipt": source_receipt,
        "source_receipt_hash": source_receipt_hash,
        "result_content_hash": intake.result.content_hash,
        "result": intake.result,
    }
    artifact = _write_canonical_artifact(
        root=root,
        analysis=analysis,
        payload=payload,
    )

    provenance_records = (
        ProvenanceRecord(
            subject_id=analysis.id,
            tool=intake.parser_name,
            tool_version=intake.parser_version,
            parameters_hash=source_receipt_hash,
            method_fingerprint_id=calculation.method_fingerprint_id,
        ),
        ProvenanceRecord(
            subject_id=artifact.id,
            tool=DOS_MATERIALIZER_NAME,
            tool_version=DOS_MATERIALIZER_VERSION,
            parameters_hash=artifact.sha256,
            method_fingerprint_id=calculation.method_fingerprint_id,
        ),
    )
    dependency_records = (
        DependencyRecord(
            upstream_id=calculation.id,
            downstream_id=analysis.id,
            kind=DependencyKind.SCIENTIFIC,
            role="calculation_context",
            recorded_hash=scientific_hash(calculation),
        ),
        DependencyRecord(
            upstream_id=doscar.artifact.id,
            downstream_id=analysis.id,
            kind=DependencyKind.SCIENTIFIC,
            role="doscar",
            recorded_hash=scientific_hash(doscar.artifact),
        ),
        DependencyRecord(
            upstream_id=atom_map.artifact.id,
            downstream_id=analysis.id,
            kind=DependencyKind.SCIENTIFIC,
            role="atom_index_map",
            recorded_hash=scientific_hash(atom_map.artifact),
        ),
        DependencyRecord(
            upstream_id=analysis.id,
            downstream_id=artifact.id,
            kind=DependencyKind.SCIENTIFIC,
            role="canonical_dos",
            recorded_hash=scientific_hash(analysis),
        ),
    )
    return DurableDosMaterialization(
        analysis=analysis,
        artifact=artifact,
        provenance_records=provenance_records,
        dependency_records=dependency_records,
    )


def load_canonical_dos_artifact(
    *,
    project_root: Path | str,
    analysis: Analysis,
    artifact: Artifact,
) -> CanonicalDosResult:
    """Reopen and validate one durable canonical DOS dataset without source guessing."""

    if analysis.analysis_type is not AnalysisType.DOS:
        raise DosMaterializationError("canonical DOS artifact requires AnalysisType.DOS")
    if analysis.status is not AnalysisStatus.COMPLETED:
        raise DosMaterializationError("canonical DOS Analysis must be completed")
    if (
        not isinstance(artifact.producer, AnalysisProducerRef)
        or artifact.producer.id != analysis.id
    ):
        raise DosMaterializationError("canonical DOS Artifact producer does not match Analysis")
    if artifact.artifact_type is not ArtifactType.DERIVED_DATASET:
        raise DosMaterializationError("canonical DOS Artifact must be DERIVED_DATASET")

    root = Path(project_root).resolve()
    verified = _verify_local_source(
        root=root,
        artifact=artifact,
        expected_type=ArtifactType.DERIVED_DATASET,
        expected_sha256=artifact.sha256,
        expected_basename="canonical-dos.json",
        label="canonical DOS Artifact",
    )
    path = _resolve_local_path(
        root=root,
        artifact=verified.artifact,
        label="canonical DOS Artifact",
    )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DosMaterializationError("canonical DOS Artifact is not valid UTF-8 JSON") from error
    if not isinstance(payload, dict):
        raise DosMaterializationError("canonical DOS Artifact root must be an object")
    if payload.get("format") != CANONICAL_DOS_ARTIFACT_FORMAT:
        raise DosMaterializationError("canonical DOS Artifact format is unsupported")
    if payload.get("version") != CANONICAL_DOS_ARTIFACT_VERSION:
        raise DosMaterializationError("canonical DOS Artifact version is unsupported")
    if payload.get("analysis_id") != str(analysis.id):
        raise DosMaterializationError("canonical DOS Artifact belongs to another Analysis")
    source_receipt_hash = payload.get("source_receipt_hash")
    if source_receipt_hash != analysis.parameters_hash:
        raise DosMaterializationError("canonical DOS source receipt does not match Analysis")
    source_receipt = payload.get("source_receipt")
    if not isinstance(source_receipt, dict):
        raise DosMaterializationError("canonical DOS source receipt is missing")
    if canonical_sha256(source_receipt) != source_receipt_hash:
        raise DosMaterializationError("canonical DOS source receipt hash is inconsistent")
    input_ids = (
        _uuid_text(source_receipt.get("doscar_artifact_id"), "doscar_artifact_id"),
        _uuid_text(
            source_receipt.get("atom_index_map_artifact_id"),
            "atom_index_map_artifact_id",
        ),
    )
    if input_ids != tuple(UUID(str(item)) for item in analysis.input_artifact_ids):
        raise DosMaterializationError("canonical DOS source receipt inputs differ from Analysis")

    result = _decode_canonical_result(payload.get("result"))
    if payload.get("result_content_hash") != result.content_hash:
        raise DosMaterializationError("canonical DOS result content hash is inconsistent")
    if source_receipt.get("structure_snapshot_id") != str(result.structure_snapshot_id):
        raise DosMaterializationError("canonical DOS source receipt snapshot differs from result")
    if source_receipt.get("atom_index_map_sha256") != result.atom_index_map_sha256:
        raise DosMaterializationError("canonical DOS atom-map receipt differs from result")
    return result


def _validate_identity(
    *,
    calculation: Calculation,
    execution_attempt: ExecutionAttempt,
    doscar_artifact: Artifact,
    atom_index_map_artifact: Artifact,
    intake: CanonicalDosIntake,
) -> None:
    if calculation.calculation_type is not CalculationType.DOS_STATIC:
        raise DosMaterializationError("canonical DOS materialization requires DOS_STATIC")
    if calculation.status is not CalculationScientificStatus.CONVERGED:
        raise DosMaterializationError(
            "canonical DOS materialization requires scientifically converged Calculation"
        )
    if intake.result.structure_snapshot_id != calculation.input_structure_snapshot_id:
        raise DosMaterializationError("canonical DOS result targets another StructureSnapshot")
    if execution_attempt.calculation_id != calculation.id:
        raise DosMaterializationError("DOSCAR ExecutionAttempt belongs to another Calculation")
    if not isinstance(doscar_artifact.producer, ExecutionAttemptProducerRef):
        raise DosMaterializationError("DOSCAR must be produced by an ExecutionAttempt")
    if doscar_artifact.producer.id != execution_attempt.id:
        raise DosMaterializationError("DOSCAR producer does not match supplied ExecutionAttempt")
    if not isinstance(atom_index_map_artifact.producer, CalculationProducerRef):
        raise DosMaterializationError("atom-index-map must be produced by the Calculation")
    if atom_index_map_artifact.producer.id != calculation.id:
        raise DosMaterializationError("atom-index-map producer does not match Calculation")


def _verify_local_source(
    *,
    root: Path,
    artifact: Artifact,
    expected_type: ArtifactType,
    expected_sha256: str | None,
    expected_basename: str,
    label: str,
) -> _VerifiedSource:
    if artifact.artifact_type is not expected_type:
        raise DosMaterializationError(f"{label} has incompatible ArtifactType")
    if artifact.availability not in {ArtifactAvailability.LOCAL, ArtifactAvailability.BOTH}:
        raise DosMaterializationError(f"{label} must be locally available")
    if artifact.sha256 is None or expected_sha256 is None:
        raise DosMaterializationError(f"{label} requires an exact SHA-256")
    if artifact.sha256.lower() != expected_sha256.lower():
        raise DosMaterializationError(f"{label} SHA-256 differs from parser receipt")
    path = _resolve_local_path(root=root, artifact=artifact, label=label)
    if PurePosixPath(artifact.local_path or "").name != expected_basename:
        raise DosMaterializationError(f"{label} has unexpected local filename")
    try:
        body = path.read_bytes()
    except OSError as error:
        raise DosMaterializationError(f"{label} cannot be read") from error
    observed_hash = hashlib.sha256(body).hexdigest()
    if observed_hash != artifact.sha256.lower():
        raise DosMaterializationError(f"{label} local content hash changed")
    if artifact.size_bytes is None or artifact.size_bytes != len(body):
        raise DosMaterializationError(f"{label} local byte size changed")
    return _VerifiedSource(artifact=artifact, sha256=observed_hash, size_bytes=len(body))


def _resolve_local_path(*, root: Path, artifact: Artifact, label: str) -> Path:
    if artifact.local_path is None:
        raise DosMaterializationError(f"{label} requires local_path")
    relative = PurePosixPath(artifact.local_path)
    if (
        relative.is_absolute()
        or artifact.local_path != relative.as_posix()
        or ".." in relative.parts
        or artifact.local_path in {"", "."}
    ):
        raise DosMaterializationError(f"{label} local_path must be normalized and relative")
    path = (root / Path(*relative.parts)).resolve()
    if not path.is_relative_to(root):
        raise DosMaterializationError(f"{label} local_path resolves outside project_root")
    if not path.is_file():
        raise DosMaterializationError(f"{label} local file is missing")
    return path


def _write_canonical_artifact(
    *,
    root: Path,
    analysis: Analysis,
    payload: object,
) -> Artifact:
    relative_path = Path("analyses") / str(analysis.id) / "canonical-dos.json"
    text = canonical_json(payload) + "\n"
    absolute_path = (root / relative_path).resolve()
    if not absolute_path.is_relative_to(root):
        raise DosMaterializationError("canonical DOS path resolves outside project_root")
    absolute_path.parent.mkdir(parents=True, exist_ok=True)
    if absolute_path.exists():
        if not absolute_path.is_file():
            raise DosMaterializationError("canonical DOS path is not a regular file")
        if absolute_path.read_text(encoding="utf-8") != text:
            raise DosMaterializationError(
                "canonical DOS dataset already exists with different content"
            )
    else:
        temporary = absolute_path.with_name(f".{absolute_path.name}.tmp")
        try:
            temporary.write_text(text, encoding="utf-8")
            os.replace(temporary, absolute_path)
        finally:
            if temporary.exists():
                temporary.unlink()
    body = text.encode("utf-8")
    return Artifact(
        artifact_type=ArtifactType.DERIVED_DATASET,
        producer=AnalysisProducerRef(analysis.id),
        availability=ArtifactAvailability.LOCAL,
        retrieval_policy=RetrievalPolicy.ALWAYS,
        local_path=relative_path.as_posix(),
        size_bytes=len(body),
        sha256=hashlib.sha256(body).hexdigest(),
    )


def _decode_canonical_result(raw: object) -> CanonicalDosResult:
    if not isinstance(raw, dict):
        raise DosMaterializationError("canonical DOS result must be an object")
    try:
        snapshot_id = StructureSnapshotId(UUID(_string(raw.get("structure_snapshot_id"))))
        atom_map_hash = _string(raw.get("atom_index_map_sha256"))
        contract_version = _integer(raw.get("contract_version"))
        axis_raw = _mapping(raw.get("energy_axis"), "energy_axis")
        energies_raw = axis_raw.get("energies_ev")
        if not isinstance(energies_raw, list):
            raise DosMaterializationError("canonical DOS energies_ev must be an array")
        axis = ElectronicEnergyAxis(
            energies_ev=tuple(_number(item, "energy") for item in energies_raw),
            fermi_energy_ev=_number(axis_raw.get("fermi_energy_ev"), "fermi_energy_ev"),
            reference=ElectronicEnergyReference(_string(axis_raw.get("reference"))),
        )
        series_raw = raw.get("series")
        if not isinstance(series_raw, list):
            raise DosMaterializationError("canonical DOS series must be an array")
        series = tuple(_decode_series(item) for item in series_raw)
        return CanonicalDosResult(
            structure_snapshot_id=snapshot_id,
            energy_axis=axis,
            series=series,
            atom_index_map_sha256=atom_map_hash,
            contract_version=contract_version,
        )
    except (TypeError, ValueError) as error:
        if isinstance(error, DosMaterializationError):
            raise
        raise DosMaterializationError("canonical DOS result contains invalid fields") from error


def _decode_series(raw: object) -> DosSeries:
    mapping = _mapping(raw, "series item")
    values_raw = mapping.get("values")
    if not isinstance(values_raw, list):
        raise DosMaterializationError("DOS series values must be an array")
    raw_uid = mapping.get("atom_uid")
    atom_uid = None if raw_uid is None else AtomUid(UUID(_string(raw_uid)))
    raw_element = mapping.get("element")
    element = None if raw_element is None else _string(raw_element)
    raw_orbital = mapping.get("orbital")
    orbital: OrbitalChannel | None = None
    if raw_orbital is not None:
        orbital_mapping = _mapping(raw_orbital, "orbital")
        orbital = OrbitalChannel(
            label=_string(orbital_mapping.get("label")),
            angular_momentum=_integer(orbital_mapping.get("angular_momentum")),
        )
    return DosSeries(
        scope=ProjectionScope(_string(mapping.get("scope"))),
        spin=SpinChannel(_string(mapping.get("spin"))),
        values=tuple(_number(item, "DOS value") for item in values_raw),
        atom_uid=atom_uid,
        element=element,
        orbital=orbital,
    )


def _mapping(value: object, field_name: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise DosMaterializationError(f"{field_name} must be an object")
    return cast(dict[str, object], value)


def _string(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DosMaterializationError("canonical DOS string field is invalid")
    return value


def _uuid_text(value: object, field_name: str) -> UUID:
    try:
        return UUID(_string(value))
    except ValueError as error:
        raise DosMaterializationError(f"{field_name} is not a UUID") from error


def _integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DosMaterializationError("canonical DOS integer field is invalid")
    return value


def _number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DosMaterializationError(f"{field_name} must be numeric")
    return float(value)

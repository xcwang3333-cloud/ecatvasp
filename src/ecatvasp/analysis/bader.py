"""Fail-closed Bader result intake and durable materialization for v0.7 Block 4."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from pathlib import Path, PurePosixPath
from typing import cast
from uuid import UUID

from ecatvasp.analysis.electronic import ExternalToolInvocation
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

BADER_ACF_PARSER_NAME = "ecatvasp.analysis.bader-acf"
BADER_ACF_PARSER_VERSION = "1"
BADER_MATERIALIZER_NAME = "ecatvasp.analysis.bader-materializer"
BADER_MATERIALIZER_VERSION = "1"
CANONICAL_BADER_FORMAT = "ecatvasp-canonical-bader-result"
CANONICAL_BADER_VERSION = 1

_CHARGE_ROLE = "charge_density"
_REFERENCE_ROLE = "reference_charge_density"


class BaderAnalysisError(ValueError):
    """Raised when Bader facts cannot be accepted without scientific guessing."""


class BaderReferenceMode(StrEnum):
    """Density policy used by the external Bader basin partitioning."""

    CHGCAR_ONLY = "chgcar_only"
    EXPLICIT_REFERENCE = "explicit_reference"


@dataclass(frozen=True, slots=True)
class BaderSiteResult:
    """Raw Bader basin facts bound to one permanent atom identity."""

    atom_uid: AtomUid
    electron_count: float
    min_distance_angstrom: float
    basin_volume_angstrom3: float

    def __post_init__(self) -> None:
        _require_nonnegative(self.electron_count, "electron_count")
        _require_nonnegative(self.min_distance_angstrom, "min_distance_angstrom")
        _require_nonnegative(self.basin_volume_angstrom3, "basin_volume_angstrom3")


@dataclass(frozen=True, slots=True)
class CanonicalBaderResult:
    """Normalized Bader facts without oxidation-state or charge-transfer interpretation."""

    structure_snapshot_id: StructureSnapshotId
    sites: tuple[BaderSiteResult, ...]
    atom_index_map_sha256: str
    reference_mode: BaderReferenceMode
    external_provenance_hash: str
    number_of_electrons: float
    vacuum_charge_e: float | None = None
    vacuum_volume_angstrom3: float | None = None
    contract_version: int = CANONICAL_BADER_VERSION

    def __post_init__(self) -> None:
        if self.contract_version != CANONICAL_BADER_VERSION:
            raise BaderAnalysisError("unsupported canonical Bader contract version")
        if not self.sites:
            raise BaderAnalysisError("canonical Bader result requires at least one atom")
        atom_uids = tuple(item.atom_uid for item in self.sites)
        if len(atom_uids) != len(set(atom_uids)):
            raise BaderAnalysisError("canonical Bader atom_uids must be unique")
        object.__setattr__(
            self,
            "atom_index_map_sha256",
            _normalized_sha256(self.atom_index_map_sha256, "atom_index_map_sha256"),
        )
        object.__setattr__(
            self,
            "external_provenance_hash",
            _normalized_sha256(
                self.external_provenance_hash,
                "external_provenance_hash",
            ),
        )
        _require_nonnegative(self.number_of_electrons, "number_of_electrons")
        _require_optional_nonnegative(self.vacuum_charge_e, "vacuum_charge_e")
        _require_optional_nonnegative(
            self.vacuum_volume_angstrom3,
            "vacuum_volume_angstrom3",
        )

    @property
    def content_hash(self) -> str:
        """Return deterministic scientific content identity."""

        return canonical_sha256(self)


@dataclass(frozen=True, slots=True)
class CanonicalBaderIntake:
    """Exact ACF.dat parse receipt tied to one external Bader invocation."""

    result: CanonicalBaderResult
    acf_sha256: str
    invocation: ExternalToolInvocation
    parser_name: str = BADER_ACF_PARSER_NAME
    parser_version: str = BADER_ACF_PARSER_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "acf_sha256",
            _normalized_sha256(self.acf_sha256, "acf_sha256"),
        )
        if self.result.external_provenance_hash != self.invocation.provenance_hash:
            raise BaderAnalysisError("Bader result provenance hash does not match invocation")


@dataclass(frozen=True, slots=True)
class DurableBaderMaterialization:
    """Bader Analysis plus raw ACF and normalized analysis-produced Artifacts."""

    analysis: Analysis
    acf_artifact: Artifact
    result_artifact: Artifact
    provenance_records: tuple[ProvenanceRecord, ...]
    dependency_records: tuple[DependencyRecord, ...]


@dataclass(frozen=True, slots=True)
class _AtomBinding:
    atom_uid: AtomUid
    element: str


@dataclass(frozen=True, slots=True)
class _AcfRow:
    ordinal: int
    electron_count: float
    min_distance_angstrom: float
    basin_volume_angstrom3: float


@dataclass(frozen=True, slots=True)
class _VerifiedArtifact:
    artifact: Artifact
    sha256: str


def parse_bader_acf(
    *,
    acf_bytes: bytes,
    atom_index_map_bytes: bytes,
    structure_snapshot_id: StructureSnapshotId,
    invocation: ExternalToolInvocation,
    reference_mode: BaderReferenceMode,
) -> CanonicalBaderIntake:
    """Parse ACF.dat and bind rows by one-based VASP ordinal, never coordinates."""

    _validate_invocation(invocation=invocation, reference_mode=reference_mode)
    bindings = _parse_atom_index_map(
        body=atom_index_map_bytes,
        structure_snapshot_id=structure_snapshot_id,
    )
    atom_map_hash = hashlib.sha256(atom_index_map_bytes).hexdigest()
    acf_hash = hashlib.sha256(acf_bytes).hexdigest()
    try:
        lines = acf_bytes.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise BaderAnalysisError("ACF.dat must be valid UTF-8/ASCII text") from error

    rows, summaries = _parse_acf_lines(lines)
    if len(rows) != len(bindings):
        raise BaderAnalysisError("ACF.dat atom count does not match frozen atom-index map")
    sites = tuple(
        BaderSiteResult(
            atom_uid=binding.atom_uid,
            electron_count=row.electron_count,
            min_distance_angstrom=row.min_distance_angstrom,
            basin_volume_angstrom3=row.basin_volume_angstrom3,
        )
        for binding, row in zip(bindings, rows, strict=True)
    )
    number_of_electrons = summaries.get("NUMBER OF ELECTRONS")
    if number_of_electrons is None:
        raise BaderAnalysisError("ACF.dat is missing NUMBER OF ELECTRONS")
    result = CanonicalBaderResult(
        structure_snapshot_id=structure_snapshot_id,
        sites=sites,
        atom_index_map_sha256=atom_map_hash,
        reference_mode=reference_mode,
        external_provenance_hash=invocation.provenance_hash,
        number_of_electrons=number_of_electrons,
        vacuum_charge_e=summaries.get("VACUUM CHARGE"),
        vacuum_volume_angstrom3=summaries.get("VACUUM VOLUME"),
    )
    return CanonicalBaderIntake(
        result=result,
        acf_sha256=acf_hash,
        invocation=invocation,
    )


def materialize_bader_analysis(
    *,
    project_root: Path | str,
    calculation: Calculation,
    execution_attempt: ExecutionAttempt,
    charge_density_artifact: Artifact,
    atom_index_map_artifact: Artifact,
    intake: CanonicalBaderIntake,
    acf_bytes: bytes,
    reference_artifact: Artifact | None = None,
) -> DurableBaderMaterialization:
    """Persist one externally executed Bader result with exact input/output provenance."""

    if hashlib.sha256(acf_bytes).hexdigest() != intake.acf_sha256:
        raise BaderAnalysisError("ACF.dat bytes differ from parser receipt")
    _validate_materialization_identity(
        calculation=calculation,
        execution_attempt=execution_attempt,
        charge_density_artifact=charge_density_artifact,
        atom_index_map_artifact=atom_index_map_artifact,
        intake=intake,
        reference_artifact=reference_artifact,
    )
    root = Path(project_root).resolve()
    if not root.is_dir():
        raise BaderAnalysisError("project_root must be an existing directory")

    charge = _verify_local_artifact(
        root=root,
        artifact=charge_density_artifact,
        expected_type=ArtifactType.CHGCAR,
        expected_sha256=_invocation_digest(intake.invocation, _CHARGE_ROLE),
        label="CHGCAR",
    )
    atom_map = _verify_local_artifact(
        root=root,
        artifact=atom_index_map_artifact,
        expected_type=ArtifactType.DERIVED_DATASET,
        expected_sha256=intake.result.atom_index_map_sha256,
        label="atom-index-map.json",
    )

    artifact_roles: tuple[tuple[str, Artifact], ...] = (
        ("charge_density", charge.artifact),
        ("atom_index_map", atom_map.artifact),
    )
    if intake.result.reference_mode is BaderReferenceMode.EXPLICIT_REFERENCE:
        if reference_artifact is None:
            raise BaderAnalysisError("explicit Bader reference mode requires reference Artifact")
        reference = _verify_reference_artifact(
            root=root,
            artifact=reference_artifact,
            expected_sha256=_invocation_digest(intake.invocation, _REFERENCE_ROLE),
        )
        artifact_roles = (*artifact_roles, ("reference_charge_density", reference))
    elif reference_artifact is not None:
        raise BaderAnalysisError("CHGCAR-only Bader mode forbids a reference Artifact")

    source_receipt = {
        "format": CANONICAL_BADER_FORMAT,
        "version": CANONICAL_BADER_VERSION,
        "structure_snapshot_id": intake.result.structure_snapshot_id,
        "parser_name": intake.parser_name,
        "parser_version": intake.parser_version,
        "acf_sha256": intake.acf_sha256,
        "reference_mode": intake.result.reference_mode,
        "invocation": intake.invocation,
        "invocation_hash": intake.invocation.provenance_hash,
        "inputs": tuple(
            {
                "role": role,
                "artifact_id": artifact.id,
                "artifact_type": artifact.artifact_type,
                "sha256": artifact.sha256,
            }
            for role, artifact in artifact_roles
        ),
    }
    source_receipt_hash = canonical_sha256(source_receipt)
    analysis = Analysis(
        project_id=calculation.project_id,
        analysis_type=AnalysisType.BADER,
        input_artifact_ids=tuple(artifact.id for _, artifact in artifact_roles),
        status=AnalysisStatus.COMPLETED,
        tool=intake.invocation.tool,
        tool_version=intake.invocation.tool_version,
        parameters_hash=source_receipt_hash,
    )
    acf_artifact = _write_analysis_bytes(
        root=root,
        analysis=analysis,
        filename="ACF.dat",
        artifact_type=ArtifactType.ACF_DAT,
        body=acf_bytes,
    )
    payload = {
        "format": CANONICAL_BADER_FORMAT,
        "version": CANONICAL_BADER_VERSION,
        "calculation_id": calculation.id,
        "analysis_id": analysis.id,
        "acf_artifact_id": acf_artifact.id,
        "source_receipt": source_receipt,
        "source_receipt_hash": source_receipt_hash,
        "result_content_hash": intake.result.content_hash,
        "result": intake.result,
    }
    result_artifact = _write_analysis_bytes(
        root=root,
        analysis=analysis,
        filename="canonical-bader.json",
        artifact_type=ArtifactType.DERIVED_DATASET,
        body=(canonical_json(payload) + "\n").encode("utf-8"),
    )

    provenance_records = (
        ProvenanceRecord(
            subject_id=analysis.id,
            tool=intake.invocation.tool,
            tool_version=intake.invocation.tool_version,
            parameters_hash=source_receipt_hash,
            method_fingerprint_id=calculation.method_fingerprint_id,
        ),
        ProvenanceRecord(
            subject_id=acf_artifact.id,
            tool=intake.invocation.tool,
            tool_version=intake.invocation.tool_version,
            parameters_hash=intake.invocation.provenance_hash,
            method_fingerprint_id=calculation.method_fingerprint_id,
        ),
        ProvenanceRecord(
            subject_id=result_artifact.id,
            tool=BADER_MATERIALIZER_NAME,
            tool_version=BADER_MATERIALIZER_VERSION,
            parameters_hash=result_artifact.sha256,
            method_fingerprint_id=calculation.method_fingerprint_id,
        ),
    )
    dependency_records = [
        DependencyRecord(
            upstream_id=calculation.id,
            downstream_id=analysis.id,
            kind=DependencyKind.SCIENTIFIC,
            role="calculation_context",
            recorded_hash=scientific_hash(calculation),
        )
    ]
    for role, artifact in artifact_roles:
        dependency_records.append(
            DependencyRecord(
                upstream_id=artifact.id,
                downstream_id=analysis.id,
                kind=DependencyKind.SCIENTIFIC,
                role=role,
                recorded_hash=scientific_hash(artifact),
            )
        )
    dependency_records.extend(
        (
            DependencyRecord(
                upstream_id=analysis.id,
                downstream_id=acf_artifact.id,
                kind=DependencyKind.SCIENTIFIC,
                role="acf_dat",
                recorded_hash=scientific_hash(analysis),
            ),
            DependencyRecord(
                upstream_id=analysis.id,
                downstream_id=result_artifact.id,
                kind=DependencyKind.SCIENTIFIC,
                role="canonical_bader",
                recorded_hash=scientific_hash(analysis),
            ),
        )
    )
    return DurableBaderMaterialization(
        analysis=analysis,
        acf_artifact=acf_artifact,
        result_artifact=result_artifact,
        provenance_records=provenance_records,
        dependency_records=tuple(dependency_records),
    )


def load_canonical_bader_artifact(
    *,
    project_root: Path | str,
    analysis: Analysis,
    acf_artifact: Artifact,
    result_artifact: Artifact,
) -> CanonicalBaderResult:
    """Reopen a durable Bader result and verify its sibling ACF.dat Artifact."""

    if analysis.analysis_type is not AnalysisType.BADER:
        raise BaderAnalysisError("canonical Bader result requires AnalysisType.BADER")
    if analysis.status is not AnalysisStatus.COMPLETED:
        raise BaderAnalysisError("canonical Bader Analysis must be completed")
    _validate_analysis_output(analysis, acf_artifact, ArtifactType.ACF_DAT, "ACF.dat")
    _validate_analysis_output(
        analysis,
        result_artifact,
        ArtifactType.DERIVED_DATASET,
        "canonical-bader.json",
    )
    root = Path(project_root).resolve()
    acf = _verify_local_artifact(
        root=root,
        artifact=acf_artifact,
        expected_type=ArtifactType.ACF_DAT,
        expected_sha256=acf_artifact.sha256,
        label="ACF.dat",
    )
    result = _verify_local_artifact(
        root=root,
        artifact=result_artifact,
        expected_type=ArtifactType.DERIVED_DATASET,
        expected_sha256=result_artifact.sha256,
        label="canonical Bader Artifact",
    )
    result_path = _resolve_local_path(root, result.artifact, "canonical Bader Artifact")
    try:
        payload = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BaderAnalysisError("canonical Bader Artifact is not valid UTF-8 JSON") from error
    mapping = _mapping(payload, "canonical Bader Artifact")
    if mapping.get("format") != CANONICAL_BADER_FORMAT:
        raise BaderAnalysisError("canonical Bader Artifact format is unsupported")
    if mapping.get("version") != CANONICAL_BADER_VERSION:
        raise BaderAnalysisError("canonical Bader Artifact version is unsupported")
    if mapping.get("analysis_id") != str(analysis.id):
        raise BaderAnalysisError("canonical Bader Artifact belongs to another Analysis")
    if mapping.get("acf_artifact_id") != str(acf.artifact.id):
        raise BaderAnalysisError("canonical Bader Artifact references another ACF.dat")
    receipt = _mapping(mapping.get("source_receipt"), "source_receipt")
    receipt_hash = mapping.get("source_receipt_hash")
    if receipt_hash != analysis.parameters_hash:
        raise BaderAnalysisError("canonical Bader source receipt differs from Analysis")
    if canonical_sha256(receipt) != receipt_hash:
        raise BaderAnalysisError("canonical Bader source receipt hash is inconsistent")
    if receipt.get("acf_sha256") != acf.sha256:
        raise BaderAnalysisError("canonical Bader source receipt ACF hash differs")
    if _receipt_input_ids(receipt) != tuple(
        UUID(str(item)) for item in analysis.input_artifact_ids
    ):
        raise BaderAnalysisError("canonical Bader source inputs differ from Analysis")
    decoded = _decode_canonical_result(mapping.get("result"))
    if mapping.get("result_content_hash") != decoded.content_hash:
        raise BaderAnalysisError("canonical Bader result content hash is inconsistent")
    return decoded


def _validate_invocation(
    *,
    invocation: ExternalToolInvocation,
    reference_mode: BaderReferenceMode,
) -> None:
    if invocation.tool.strip().casefold() != "bader":
        raise BaderAnalysisError("Bader intake requires external tool name 'bader'")
    roles = frozenset(item.role for item in invocation.inputs)
    has_ref_flag = "-ref" in invocation.argv
    if reference_mode is BaderReferenceMode.CHGCAR_ONLY:
        if roles != frozenset({_CHARGE_ROLE}) or has_ref_flag:
            raise BaderAnalysisError(
                "CHGCAR-only mode requires only charge_density and forbids -ref"
            )
        return
    expected_roles = frozenset({_CHARGE_ROLE, _REFERENCE_ROLE})
    if roles != expected_roles or not has_ref_flag:
        raise BaderAnalysisError(
            "explicit-reference mode requires charge/reference inputs and -ref"
        )


def _parse_acf_lines(lines: list[str]) -> tuple[tuple[_AcfRow, ...], dict[str, float]]:
    header_index = next(
        (index for index, line in enumerate(lines) if line.strip().startswith("#")),
        None,
    )
    if header_index is None:
        raise BaderAnalysisError("ACF.dat is missing its column header")
    header = " ".join(lines[header_index].strip().lstrip("#").upper().split())
    for token in ("X", "Y", "Z", "CHARGE", "MIN DIST", "ATOMIC VOL"):
        if token not in header:
            raise BaderAnalysisError("ACF.dat column header is unsupported")

    rows: list[_AcfRow] = []
    summaries: dict[str, float] = {}
    rows_finished = False
    for line in lines[header_index + 1 :]:
        stripped = line.strip()
        if not stripped or (stripped and set(stripped) == {"-"}):
            if rows:
                rows_finished = True
            continue
        if not rows_finished:
            tokens = stripped.split()
            if tokens and tokens[0].isdigit():
                rows.append(_parse_acf_row(tokens, len(rows) + 1))
                continue
            if rows:
                rows_finished = True
            else:
                continue
        if ":" not in stripped:
            continue
        name, raw_value = stripped.split(":", 1)
        key = " ".join(name.upper().split())
        accepted = {"VACUUM CHARGE", "VACUUM VOLUME", "NUMBER OF ELECTRONS"}
        if key not in accepted:
            continue
        if key in summaries:
            raise BaderAnalysisError(f"ACF.dat repeats summary field {key}")
        summaries[key] = _finite_float(raw_value.strip(), key)
    if not rows:
        raise BaderAnalysisError("ACF.dat contains no atom rows")
    return tuple(rows), summaries


def _parse_acf_row(tokens: list[str], expected_ordinal: int) -> _AcfRow:
    if len(tokens) != 7:
        raise BaderAnalysisError("ACF.dat atom rows must contain index plus six values")
    try:
        ordinal = int(tokens[0])
    except ValueError as error:
        raise BaderAnalysisError("ACF.dat atom index is not an integer") from error
    if ordinal != expected_ordinal:
        raise BaderAnalysisError("ACF.dat atom indices must be contiguous and one-based")
    values = tuple(_finite_float(token, "ACF.dat atom value") for token in tokens[1:])
    _x, _y, _z, charge, min_distance, volume = values
    _require_nonnegative(charge, "ACF.dat CHARGE")
    _require_nonnegative(min_distance, "ACF.dat MIN DIST")
    _require_nonnegative(volume, "ACF.dat ATOMIC VOL")
    return _AcfRow(
        ordinal=ordinal,
        electron_count=charge,
        min_distance_angstrom=min_distance,
        basin_volume_angstrom3=volume,
    )


def _parse_atom_index_map(
    *,
    body: bytes,
    structure_snapshot_id: StructureSnapshotId,
) -> tuple[_AtomBinding, ...]:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BaderAnalysisError("atom-index-map.json must be valid UTF-8 JSON") from error
    mapping = _mapping(payload, "atom-index-map.json")
    valid_format = mapping.get("format") == "ecatvasp-v03-atom-index-map"
    if not valid_format or mapping.get("version") != 1:
        raise BaderAnalysisError("unsupported atom-index-map.json format/version")
    if mapping.get("structure_snapshot_id") != str(structure_snapshot_id):
        raise BaderAnalysisError("atom index map belongs to another StructureSnapshot")
    for field_name in ("structure_sha256", "poscar_sha256"):
        value = mapping.get(field_name)
        if not isinstance(value, str):
            raise BaderAnalysisError(f"atom index map {field_name} must be a SHA-256 string")
        _normalized_sha256(value, field_name)

    raw_entries = mapping.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise BaderAnalysisError("atom index map requires non-empty entries")
    bindings: list[_AtomBinding] = []
    elements: list[str] = []
    for expected_index, raw in enumerate(raw_entries):
        entry = _mapping(raw, "atom index map entry")
        if (
            entry.get("poscar_index") != expected_index
            or entry.get("vasp_ordinal") != expected_index + 1
        ):
            raise BaderAnalysisError("atom index map indices/ordinals are not contiguous")
        raw_uid = entry.get("atom_uid")
        element = entry.get("element")
        if not isinstance(raw_uid, str) or not isinstance(element, str):
            raise BaderAnalysisError("atom index map entry requires atom_uid and element")
        if not element.strip():
            raise BaderAnalysisError("atom index map element must not be blank")
        try:
            atom_uid = AtomUid(UUID(raw_uid))
        except ValueError as error:
            raise BaderAnalysisError("atom index map atom_uid is not a UUID") from error
        bindings.append(_AtomBinding(atom_uid=atom_uid, element=element))
        elements.append(element)
    if len({item.atom_uid for item in bindings}) != len(bindings):
        raise BaderAnalysisError("atom index map atom_uids must be unique")

    species_order = mapping.get("species_order")
    species_counts = mapping.get("species_counts")
    if not isinstance(species_order, list) or not all(
        isinstance(value, str) and value.strip() for value in species_order
    ):
        raise BaderAnalysisError("atom index map species_order is invalid")
    if not isinstance(species_counts, list) or any(
        isinstance(value, bool) or not isinstance(value, int) or value < 1
        for value in species_counts
    ):
        raise BaderAnalysisError("atom index map species_counts are invalid")
    if len(species_order) != len(species_counts):
        raise BaderAnalysisError("atom index map species metadata lengths differ")
    expanded = tuple(
        element
        for element, count in zip(species_order, species_counts, strict=True)
        for _ in range(count)
    )
    if expanded != tuple(elements):
        raise BaderAnalysisError("atom index map species metadata does not match entry order")
    return tuple(bindings)


def _validate_materialization_identity(
    *,
    calculation: Calculation,
    execution_attempt: ExecutionAttempt,
    charge_density_artifact: Artifact,
    atom_index_map_artifact: Artifact,
    intake: CanonicalBaderIntake,
    reference_artifact: Artifact | None,
) -> None:
    if calculation.calculation_type is not CalculationType.CHARGE_STATIC:
        raise BaderAnalysisError("Bader materialization requires CHARGE_STATIC Calculation")
    if calculation.status is not CalculationScientificStatus.CONVERGED:
        raise BaderAnalysisError(
            "Bader materialization requires scientifically converged Calculation"
        )
    if intake.result.structure_snapshot_id != calculation.input_structure_snapshot_id:
        raise BaderAnalysisError("Bader result targets another StructureSnapshot")
    if execution_attempt.calculation_id != calculation.id:
        raise BaderAnalysisError("CHGCAR ExecutionAttempt belongs to another Calculation")
    if not isinstance(charge_density_artifact.producer, ExecutionAttemptProducerRef):
        raise BaderAnalysisError("CHGCAR must be produced by an ExecutionAttempt")
    if charge_density_artifact.producer.id != execution_attempt.id:
        raise BaderAnalysisError("CHGCAR producer does not match supplied ExecutionAttempt")
    if not isinstance(atom_index_map_artifact.producer, CalculationProducerRef):
        raise BaderAnalysisError("atom-index-map must be produced by the Calculation")
    if atom_index_map_artifact.producer.id != calculation.id:
        raise BaderAnalysisError("atom-index-map producer does not match Calculation")
    _validate_invocation(
        invocation=intake.invocation,
        reference_mode=intake.result.reference_mode,
    )
    if intake.result.reference_mode is BaderReferenceMode.EXPLICIT_REFERENCE:
        if reference_artifact is None:
            raise BaderAnalysisError("explicit Bader reference mode requires reference Artifact")
    elif reference_artifact is not None:
        raise BaderAnalysisError("CHGCAR-only Bader mode forbids reference Artifact")


def _verify_reference_artifact(
    *,
    root: Path,
    artifact: Artifact,
    expected_sha256: str,
) -> Artifact:
    allowed = {ArtifactType.CHGCAR, ArtifactType.DERIVED_DATASET}
    if artifact.artifact_type not in allowed:
        raise BaderAnalysisError(
            "Bader reference Artifact must be CHGCAR or DERIVED_DATASET"
        )
    return _verify_local_artifact(
        root=root,
        artifact=artifact,
        expected_type=artifact.artifact_type,
        expected_sha256=expected_sha256,
        label="Bader reference charge density",
    ).artifact


def _verify_local_artifact(
    *,
    root: Path,
    artifact: Artifact,
    expected_type: ArtifactType,
    expected_sha256: str | None,
    label: str,
) -> _VerifiedArtifact:
    if artifact.artifact_type is not expected_type:
        raise BaderAnalysisError(f"{label} has incompatible ArtifactType")
    if artifact.availability not in {ArtifactAvailability.LOCAL, ArtifactAvailability.BOTH}:
        raise BaderAnalysisError(f"{label} must be locally available")
    if artifact.sha256 is None or expected_sha256 is None:
        raise BaderAnalysisError(f"{label} requires exact SHA-256 metadata")
    if artifact.sha256.lower() != expected_sha256.lower():
        raise BaderAnalysisError(f"{label} SHA-256 differs from external provenance")
    path = _resolve_local_path(root, artifact, label)
    try:
        body = path.read_bytes()
    except OSError as error:
        raise BaderAnalysisError(f"{label} cannot be read") from error
    observed = hashlib.sha256(body).hexdigest()
    if observed != artifact.sha256.lower():
        raise BaderAnalysisError(f"{label} local content hash changed")
    if artifact.size_bytes is None or artifact.size_bytes != len(body):
        raise BaderAnalysisError(f"{label} local byte size changed")
    return _VerifiedArtifact(artifact=artifact, sha256=observed)


def _resolve_local_path(root: Path, artifact: Artifact, label: str) -> Path:
    if artifact.local_path is None:
        raise BaderAnalysisError(f"{label} requires local_path")
    relative = PurePosixPath(artifact.local_path)
    if (
        relative.is_absolute()
        or artifact.local_path != relative.as_posix()
        or ".." in relative.parts
        or artifact.local_path in {"", "."}
    ):
        raise BaderAnalysisError(f"{label} local_path must be normalized and relative")
    path = (root / Path(*relative.parts)).resolve()
    if not path.is_relative_to(root):
        raise BaderAnalysisError(f"{label} local_path resolves outside project_root")
    if not path.is_file():
        raise BaderAnalysisError(f"{label} local file is missing")
    return path


def _write_analysis_bytes(
    *,
    root: Path,
    analysis: Analysis,
    filename: str,
    artifact_type: ArtifactType,
    body: bytes,
) -> Artifact:
    relative = Path("analyses") / str(analysis.id) / filename
    absolute = (root / relative).resolve()
    if not absolute.is_relative_to(root):
        raise BaderAnalysisError("Bader output path resolves outside project_root")
    absolute.parent.mkdir(parents=True, exist_ok=True)
    if absolute.exists():
        if not absolute.is_file() or absolute.read_bytes() != body:
            raise BaderAnalysisError(f"{filename} already exists with different content")
    else:
        temporary = absolute.with_name(f".{absolute.name}.tmp")
        try:
            temporary.write_bytes(body)
            os.replace(temporary, absolute)
        finally:
            if temporary.exists():
                temporary.unlink()
    return Artifact(
        artifact_type=artifact_type,
        producer=AnalysisProducerRef(analysis.id),
        availability=ArtifactAvailability.LOCAL,
        retrieval_policy=RetrievalPolicy.ALWAYS,
        local_path=relative.as_posix(),
        size_bytes=len(body),
        sha256=hashlib.sha256(body).hexdigest(),
    )


def _validate_analysis_output(
    analysis: Analysis,
    artifact: Artifact,
    artifact_type: ArtifactType,
    filename: str,
) -> None:
    valid_producer = (
        isinstance(artifact.producer, AnalysisProducerRef)
        and artifact.producer.id == analysis.id
    )
    if not valid_producer:
        raise BaderAnalysisError(f"{filename} producer does not match Bader Analysis")
    if artifact.artifact_type is not artifact_type:
        raise BaderAnalysisError(f"{filename} has incompatible ArtifactType")
    if PurePosixPath(artifact.local_path or "").name != filename:
        raise BaderAnalysisError(f"{filename} Artifact has unexpected local filename")


def _invocation_digest(invocation: ExternalToolInvocation, role: str) -> str:
    matches = tuple(item.sha256 for item in invocation.inputs if item.role == role)
    if len(matches) != 1:
        raise BaderAnalysisError(f"Bader invocation requires exactly one {role} input")
    return matches[0]


def _receipt_input_ids(receipt: dict[str, object]) -> tuple[UUID, ...]:
    raw_inputs = receipt.get("inputs")
    if not isinstance(raw_inputs, list):
        raise BaderAnalysisError("canonical Bader receipt inputs must be an array")
    ids: list[UUID] = []
    for raw in raw_inputs:
        mapping = _mapping(raw, "source input")
        value = mapping.get("artifact_id")
        if not isinstance(value, str):
            raise BaderAnalysisError("canonical Bader source artifact_id must be a UUID")
        try:
            ids.append(UUID(value))
        except ValueError as error:
            raise BaderAnalysisError(
                "canonical Bader source artifact_id is not a UUID"
            ) from error
    return tuple(ids)


def _decode_canonical_result(raw: object) -> CanonicalBaderResult:
    mapping = _mapping(raw, "canonical Bader result")
    try:
        snapshot_id = StructureSnapshotId(UUID(_string(mapping.get("structure_snapshot_id"))))
        sites_raw = mapping.get("sites")
        if not isinstance(sites_raw, list):
            raise BaderAnalysisError("canonical Bader sites must be an array")
        sites = tuple(_decode_site(item) for item in sites_raw)
        return CanonicalBaderResult(
            structure_snapshot_id=snapshot_id,
            sites=sites,
            atom_index_map_sha256=_string(mapping.get("atom_index_map_sha256")),
            reference_mode=BaderReferenceMode(_string(mapping.get("reference_mode"))),
            external_provenance_hash=_string(
                mapping.get("external_provenance_hash")
            ),
            number_of_electrons=_number(
                mapping.get("number_of_electrons"),
                "number_of_electrons",
            ),
            vacuum_charge_e=_optional_number(
                mapping.get("vacuum_charge_e"),
                "vacuum_charge_e",
            ),
            vacuum_volume_angstrom3=_optional_number(
                mapping.get("vacuum_volume_angstrom3"),
                "vacuum_volume_angstrom3",
            ),
            contract_version=_integer(mapping.get("contract_version")),
        )
    except (TypeError, ValueError) as error:
        if isinstance(error, BaderAnalysisError):
            raise
        raise BaderAnalysisError("canonical Bader result contains invalid fields") from error


def _decode_site(raw: object) -> BaderSiteResult:
    mapping = _mapping(raw, "Bader site")
    try:
        atom_uid = AtomUid(UUID(_string(mapping.get("atom_uid"))))
    except ValueError as error:
        raise BaderAnalysisError("canonical Bader atom_uid is not a UUID") from error
    return BaderSiteResult(
        atom_uid=atom_uid,
        electron_count=_number(mapping.get("electron_count"), "electron_count"),
        min_distance_angstrom=_number(
            mapping.get("min_distance_angstrom"),
            "min_distance_angstrom",
        ),
        basin_volume_angstrom3=_number(
            mapping.get("basin_volume_angstrom3"),
            "basin_volume_angstrom3",
        ),
    )


def _mapping(value: object, field_name: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise BaderAnalysisError(f"{field_name} must be an object")
    return cast(dict[str, object], value)


def _string(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BaderAnalysisError("canonical Bader string field is invalid")
    return value


def _integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise BaderAnalysisError("canonical Bader integer field is invalid")
    return value


def _number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BaderAnalysisError(f"{field_name} must be numeric")
    result = float(value)
    if not isfinite(result):
        raise BaderAnalysisError(f"{field_name} must be finite")
    return result


def _optional_number(value: object, field_name: str) -> float | None:
    if value is None:
        return None
    return _number(value, field_name)


def _finite_float(value: str, field_name: str) -> float:
    try:
        result = float(value.replace("D", "E").replace("d", "e"))
    except ValueError as error:
        raise BaderAnalysisError(f"{field_name} is not numeric") from error
    if not isfinite(result):
        raise BaderAnalysisError(f"{field_name} must be finite")
    return result


def _normalized_sha256(value: str, field_name: str) -> str:
    normalized = value.lower()
    valid = len(normalized) == 64 and all(
        character in "0123456789abcdef" for character in normalized
    )
    if not valid:
        raise BaderAnalysisError(
            f"{field_name} must be a 64-character hexadecimal SHA-256 digest"
        )
    return normalized


def _require_nonnegative(value: float, field_name: str) -> None:
    if not isfinite(value) or value < 0:
        raise BaderAnalysisError(f"{field_name} must be finite and non-negative")


def _require_optional_nonnegative(value: float | None, field_name: str) -> None:
    if value is not None:
        _require_nonnegative(value, field_name)

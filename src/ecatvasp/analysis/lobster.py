"""Fail-closed LOBSTER COHP/ICOHP result intake for v0.7 Block 6."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from pathlib import Path, PurePosixPath
from typing import cast
from uuid import UUID

from ecatvasp.analysis.electronic import ExternalToolInvocation, SpinChannel
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
    MethodFingerprint,
    RetrievalPolicy,
    StructureSnapshot,
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

LOBSTER_COHP_PARSER_NAME = "ecatvasp.analysis.lobster.cohp"
LOBSTER_COHP_PARSER_VERSION = "1"
LOBSTER_COHP_MATERIALIZER_NAME = "ecatvasp.analysis.lobster.materialization"
LOBSTER_COHP_MATERIALIZER_VERSION = "1"
CANONICAL_COHP_FORMAT = "ecatvasp-canonical-cohp"
CANONICAL_COHP_VERSION = 1

_REQUIRED_PREREQUISITE_TYPES = frozenset(
    {
        ArtifactType.POSCAR,
        ArtifactType.INCAR,
        ArtifactType.KPOINTS,
        ArtifactType.POTCAR_SPEC,
    }
)
_REQUIRED_INVOCATION_ROLES = frozenset({"wavefunction", "poscar", "potcar", "lobsterin"})
_CENTER_RE = re.compile(
    r"^([A-Z][a-z]?)(\d+)(?:\[(-?\d+\s+-?\d+\s+-?\d+)\])?(?:\[([^\]]+)\])?$"
)
_INTERACTION_RE = re.compile(r"^No\.(\d+):(.+)->(.+)\(([^()]*)\)$")


class LobsterCohpError(ValueError):
    """Raised when LOBSTER COHP/ICOHP facts cannot be normalized exactly."""


class CohpEnergyReference(StrEnum):
    """Energy-reference semantics for canonical LOBSTER COHP data."""

    LOBSTER_FERMI_RELATIVE = "lobster_fermi_relative"


@dataclass(frozen=True, slots=True)
class CohpSpinSeries:
    """Native-sign COHP and integrated COHP values for one spin channel."""

    spin: SpinChannel
    cohp_values: tuple[float, ...]
    icohp_values: tuple[float, ...]
    icohp_at_fermi_ev: float | None = None

    def __post_init__(self) -> None:
        if not self.cohp_values or len(self.cohp_values) != len(self.icohp_values):
            raise LobsterCohpError("COHP and ICOHP series must be non-empty and equally sized")
        if not all(isfinite(value) for value in (*self.cohp_values, *self.icohp_values)):
            raise LobsterCohpError("COHP/ICOHP values must be finite")
        if self.icohp_at_fermi_ev is not None and not isfinite(self.icohp_at_fermi_ev):
            raise LobsterCohpError("ICOHP at Fermi must be finite when present")


@dataclass(frozen=True, slots=True)
class CohpInteraction:
    """One LOBSTER pair/orbital interaction bound to permanent atom identity."""

    source_index: int
    source_label: str
    atom_uid_a: AtomUid
    atom_uid_b: AtomUid
    element_a: str
    element_b: str
    bond_length_angstrom: float
    series: tuple[CohpSpinSeries, ...]
    cell_a: tuple[int, int, int] | None = None
    cell_b: tuple[int, int, int] | None = None
    orbital_a: str | None = None
    orbital_b: str | None = None

    def __post_init__(self) -> None:
        if self.source_index < 1:
            raise LobsterCohpError("LOBSTER interaction source_index must be positive")
        if not self.source_label.strip():
            raise LobsterCohpError("LOBSTER interaction source_label must not be blank")
        if not self.element_a.strip() or not self.element_b.strip():
            raise LobsterCohpError("LOBSTER interaction elements must not be blank")
        if not isfinite(self.bond_length_angstrom) or self.bond_length_angstrom <= 0:
            raise LobsterCohpError("LOBSTER bond length must be finite and positive")
        if not self.series:
            raise LobsterCohpError("LOBSTER interaction requires at least one spin series")
        spins = tuple(item.spin for item in self.series)
        if len(spins) != len(set(spins)):
            raise LobsterCohpError("LOBSTER interaction spin channels must be unique")
        for cell in (self.cell_a, self.cell_b):
            if cell is not None and len(cell) != 3:
                raise LobsterCohpError("LOBSTER cell translations require three integers")
        for orbital in (self.orbital_a, self.orbital_b):
            if orbital is not None and not orbital.strip():
                raise LobsterCohpError("LOBSTER orbital labels must not be blank")

    @property
    def is_orbital_resolved(self) -> bool:
        return self.orbital_a is not None or self.orbital_b is not None


@dataclass(frozen=True, slots=True)
class CanonicalCohpResult:
    """Canonical native-sign LOBSTER COHP/ICOHP facts on a Fermi-relative grid."""

    structure_snapshot_id: StructureSnapshotId
    energies_ev_relative_to_fermi: tuple[float, ...]
    source_fermi_energy_ev: float
    average_series: tuple[CohpSpinSeries, ...]
    interactions: tuple[CohpInteraction, ...]
    atom_index_map_sha256: str
    energy_reference: CohpEnergyReference = CohpEnergyReference.LOBSTER_FERMI_RELATIVE
    contract_version: int = CANONICAL_COHP_VERSION

    def __post_init__(self) -> None:
        if self.contract_version != CANONICAL_COHP_VERSION:
            raise LobsterCohpError("unsupported canonical COHP contract version")
        if self.energy_reference is not CohpEnergyReference.LOBSTER_FERMI_RELATIVE:
            raise LobsterCohpError("canonical COHP energy axis must remain LOBSTER Fermi-relative")
        object.__setattr__(
            self,
            "atom_index_map_sha256",
            _normalized_sha256(self.atom_index_map_sha256, "atom_index_map_sha256"),
        )
        if len(self.energies_ev_relative_to_fermi) < 2:
            raise LobsterCohpError("canonical COHP energy axis requires at least two points")
        if not isfinite(self.source_fermi_energy_ev):
            raise LobsterCohpError("source Fermi energy must be finite")
        if not all(isfinite(value) for value in self.energies_ev_relative_to_fermi):
            raise LobsterCohpError("COHP energies must be finite")
        if any(
            right <= left
            for left, right in zip(
                self.energies_ev_relative_to_fermi,
                self.energies_ev_relative_to_fermi[1:],
                strict=False,
            )
        ):
            raise LobsterCohpError("COHP energies must be strictly increasing")
        if self.energies_ev_relative_to_fermi[0] > 0 or self.energies_ev_relative_to_fermi[-1] < 0:
            raise LobsterCohpError("COHP energy window must include the Fermi level at 0 eV")
        if not self.average_series or not self.interactions:
            raise LobsterCohpError("canonical COHP requires average and pair interactions")
        expected_len = len(self.energies_ev_relative_to_fermi)
        spin_schema = tuple(item.spin for item in self.average_series)
        if spin_schema not in (
            (SpinChannel.TOTAL,),
            (SpinChannel.UP, SpinChannel.DOWN),
        ):
            raise LobsterCohpError("COHP spin schema must be TOTAL or ordered UP/DOWN")
        for series in (*self.average_series, *(item for pair in self.interactions for item in pair.series)):
            if len(series.cohp_values) != expected_len:
                raise LobsterCohpError("every COHP/ICOHP series must use the common energy grid")
        if any(tuple(item.spin for item in pair.series) != spin_schema for pair in self.interactions):
            raise LobsterCohpError("every COHP interaction must use the average spin schema")
        semantic_keys = tuple(
            (
                item.source_index,
                item.atom_uid_a,
                item.atom_uid_b,
                item.cell_a,
                item.cell_b,
                item.orbital_a,
                item.orbital_b,
            )
            for item in self.interactions
        )
        if len(semantic_keys) != len(set(semantic_keys)):
            raise LobsterCohpError("canonical COHP interaction identities must be unique")

    @property
    def content_hash(self) -> str:
        return canonical_sha256(self)


@dataclass(frozen=True, slots=True)
class CanonicalCohpIntake:
    """Parsed LOBSTER facts plus exact raw-output and invocation provenance."""

    result: CanonicalCohpResult
    invocation: ExternalToolInvocation
    cohpcar_sha256: str
    icohplist_sha256: str
    parser_name: str = LOBSTER_COHP_PARSER_NAME
    parser_version: str = LOBSTER_COHP_PARSER_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "cohpcar_sha256",
            _normalized_sha256(self.cohpcar_sha256, "cohpcar_sha256"),
        )
        object.__setattr__(
            self,
            "icohplist_sha256",
            _normalized_sha256(self.icohplist_sha256, "icohplist_sha256"),
        )


@dataclass(frozen=True, slots=True)
class DurableCohpMaterialization:
    analysis: Analysis
    cohpcar_artifact: Artifact
    icohplist_artifact: Artifact
    result_artifact: Artifact
    provenance_records: tuple[ProvenanceRecord, ...]
    dependency_records: tuple[DependencyRecord, ...]


@dataclass(frozen=True, slots=True)
class _AtomMapEntry:
    atom_uid: AtomUid
    element: str
    vasp_ordinal: int


@dataclass(frozen=True, slots=True)
class _ParsedCenter:
    atom_uid: AtomUid
    element: str
    ordinal: int
    cell: tuple[int, int, int] | None
    orbital: str | None


@dataclass(frozen=True, slots=True)
class _InteractionHeader:
    source_index: int
    source_label: str
    center_a: _ParsedCenter
    center_b: _ParsedCenter
    length: float


@dataclass(frozen=True, slots=True)
class _IcohpEfRow:
    source_index: int
    center_a: _ParsedCenter
    center_b: _ParsedCenter
    length: float
    translation_b: tuple[int, int, int]
    values: tuple[tuple[SpinChannel, float], ...]


def parse_lobster_cohp(
    *,
    cohpcar_bytes: bytes,
    icohplist_bytes: bytes,
    atom_index_map_bytes: bytes,
    structure_snapshot_id: StructureSnapshotId,
    invocation: ExternalToolInvocation,
) -> CanonicalCohpIntake:
    """Parse native-sign COHP/ICOHP facts and bind pair labels through the frozen atom map."""

    _validate_invocation_shape(invocation)
    atom_entries = _parse_atom_index_map(atom_index_map_bytes, structure_snapshot_id)
    entry_by_ordinal = {item.vasp_ordinal: item for item in atom_entries}
    map_hash = hashlib.sha256(atom_index_map_bytes).hexdigest()

    try:
        lines = cohpcar_bytes.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise LobsterCohpError("COHPCAR.lobster must be UTF-8 text") from error
    if len(lines) < 5 or "COHP" not in lines[0].upper():
        raise LobsterCohpError("COHPCAR.lobster header is unsupported")
    parameters = lines[1].split()
    if len(parameters) < 4:
        raise LobsterCohpError("COHPCAR.lobster parameter line is incomplete")
    try:
        interaction_count = int(parameters[0])
        spin_count = int(parameters[1])
        point_count = int(parameters[2])
        source_fermi = float(parameters[-1])
    except ValueError as error:
        raise LobsterCohpError("COHPCAR.lobster parameter line is invalid") from error
    if interaction_count < 2 or spin_count not in {1, 2} or point_count < 2:
        raise LobsterCohpError("COHPCAR.lobster dimensions are unsupported")
    if not isfinite(source_fermi):
        raise LobsterCohpError("COHPCAR.lobster source Fermi energy must be finite")

    header_lines = lines[2 : 2 + interaction_count]
    if len(header_lines) != interaction_count or "Average" not in header_lines[0]:
        raise LobsterCohpError("COHPCAR.lobster must start interaction headers with Average")
    interaction_headers = tuple(
        _parse_interaction_header(line, entry_by_ordinal) for line in header_lines[1:]
    )
    data_lines = [line for line in lines[2 + interaction_count :] if line.strip()]
    if len(data_lines) != point_count:
        raise LobsterCohpError("COHPCAR.lobster data-row count differs from header")
    expected_columns = 1 + 2 * interaction_count * spin_count
    rows: list[tuple[float, ...]] = []
    for line in data_lines:
        parts = line.split()
        if len(parts) != expected_columns:
            raise LobsterCohpError("COHPCAR.lobster data column count differs from header")
        try:
            row = tuple(float(value) for value in parts)
        except ValueError as error:
            raise LobsterCohpError("COHPCAR.lobster data contains a non-numeric value") from error
        if not all(isfinite(value) for value in row):
            raise LobsterCohpError("COHPCAR.lobster data contains non-finite values")
        rows.append(row)
    energies = tuple(row[0] for row in rows)
    if any(right <= left for left, right in zip(energies, energies[1:], strict=False)):
        raise LobsterCohpError("COHPCAR.lobster energies must be strictly increasing")

    spin_channels = (
        (SpinChannel.TOTAL,) if spin_count == 1 else (SpinChannel.UP, SpinChannel.DOWN)
    )
    zero_index = _exact_zero_index(energies)
    average_series = _series_for_interaction(
        rows=rows,
        interaction_position=0,
        interaction_count=interaction_count,
        spins=spin_channels,
        zero_index=zero_index,
        ef_values=None,
    )

    ef_rows = _parse_icohplist(
        icohplist_bytes=icohplist_bytes,
        entry_by_ordinal=entry_by_ordinal,
        expected_spins=spin_channels,
    )
    ef_lookup = {
        (row.source_index, row.center_a.ordinal, row.center_b.ordinal): row for row in ef_rows
    }
    interactions: list[CohpInteraction] = []
    for position, header in enumerate(interaction_headers, start=1):
        ef_row = ef_lookup.get(
            (header.source_index, header.center_a.ordinal, header.center_b.ordinal)
        )
        if not _header_is_orbital(header):
            if ef_row is None:
                raise LobsterCohpError(
                    f"ICOHPLIST.lobster is missing total interaction No.{header.source_index}"
                )
            _validate_ef_row_against_header(ef_row, header)
            ef_values = dict(ef_row.values)
        else:
            ef_values = None
        series = _series_for_interaction(
            rows=rows,
            interaction_position=position,
            interaction_count=interaction_count,
            spins=spin_channels,
            zero_index=zero_index,
            ef_values=ef_values,
        )
        interactions.append(
            CohpInteraction(
                source_index=header.source_index,
                source_label=header.source_label,
                atom_uid_a=header.center_a.atom_uid,
                atom_uid_b=header.center_b.atom_uid,
                element_a=header.center_a.element,
                element_b=header.center_b.element,
                bond_length_angstrom=header.length,
                series=series,
                cell_a=header.center_a.cell,
                cell_b=header.center_b.cell,
                orbital_a=header.center_a.orbital,
                orbital_b=header.center_b.orbital,
            )
        )
    result = CanonicalCohpResult(
        structure_snapshot_id=structure_snapshot_id,
        energies_ev_relative_to_fermi=energies,
        source_fermi_energy_ev=source_fermi,
        average_series=average_series,
        interactions=tuple(interactions),
        atom_index_map_sha256=map_hash,
    )
    return CanonicalCohpIntake(
        result=result,
        invocation=invocation,
        cohpcar_sha256=hashlib.sha256(cohpcar_bytes).hexdigest(),
        icohplist_sha256=hashlib.sha256(icohplist_bytes).hexdigest(),
    )


def materialize_lobster_cohp_analysis(
    *,
    project_root: Path | str,
    calculation: Calculation,
    snapshot: StructureSnapshot,
    method_fingerprint: MethodFingerprint,
    execution_attempt: ExecutionAttempt,
    wavecar_artifact: Artifact,
    atom_index_map_artifact: Artifact,
    prerequisite_input_artifacts: tuple[Artifact, ...],
    cohpcar_bytes: bytes,
    icohplist_bytes: bytes,
    intake: CanonicalCohpIntake,
) -> DurableCohpMaterialization:
    """Persist exact LOBSTER raw outputs and canonical native-sign COHP/ICOHP facts."""

    _validate_materialization_context(
        calculation=calculation,
        snapshot=snapshot,
        method_fingerprint=method_fingerprint,
        execution_attempt=execution_attempt,
        wavecar_artifact=wavecar_artifact,
        atom_index_map_artifact=atom_index_map_artifact,
        prerequisite_input_artifacts=prerequisite_input_artifacts,
        intake=intake,
    )
    if hashlib.sha256(cohpcar_bytes).hexdigest() != intake.cohpcar_sha256:
        raise LobsterCohpError("COHPCAR bytes differ from parser receipt")
    if hashlib.sha256(icohplist_bytes).hexdigest() != intake.icohplist_sha256:
        raise LobsterCohpError("ICOHPLIST bytes differ from parser receipt")
    root = Path(project_root).resolve()
    if not root.is_dir():
        raise LobsterCohpError("project_root must be an existing directory")

    wavecar = _verify_local_artifact(root, wavecar_artifact, ArtifactType.WAVECAR, "WAVECAR")
    atom_map = _verify_local_artifact(
        root,
        atom_index_map_artifact,
        ArtifactType.DERIVED_DATASET,
        "atom-index-map.json",
    )
    inputs = tuple(
        _verify_local_artifact(root, artifact, artifact.artifact_type, artifact.artifact_type.value)
        for artifact in prerequisite_input_artifacts
    )
    by_type = {artifact.artifact_type: artifact for artifact in inputs}
    if set(by_type) != _REQUIRED_PREREQUISITE_TYPES:
        raise LobsterCohpError("LOBSTER prerequisite inputs must be POSCAR/INCAR/KPOINTS/POTCAR.spec")
    if wavecar.sha256 != _invocation_digest(intake.invocation, "wavefunction"):
        raise LobsterCohpError("LOBSTER invocation wavefunction digest differs from WAVECAR")
    if by_type[ArtifactType.POSCAR].sha256 != _invocation_digest(intake.invocation, "poscar"):
        raise LobsterCohpError("LOBSTER invocation POSCAR digest differs from managed POSCAR")
    if atom_map.sha256 != intake.result.atom_index_map_sha256:
        raise LobsterCohpError("LOBSTER atom-index-map hash differs from canonical result")

    artifact_roles = (
        ("wavecar", wavecar),
        ("atom_index_map", atom_map),
        *((artifact.artifact_type.value, artifact) for artifact in inputs),
    )
    source_receipt = {
        "format": CANONICAL_COHP_FORMAT,
        "version": CANONICAL_COHP_VERSION,
        "calculation_id": calculation.id,
        "structure_snapshot_id": snapshot.id,
        "method_fingerprint_id": method_fingerprint.id,
        "parser_name": intake.parser_name,
        "parser_version": intake.parser_version,
        "cohpcar_sha256": intake.cohpcar_sha256,
        "icohplist_sha256": intake.icohplist_sha256,
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
    receipt_hash = canonical_sha256(source_receipt)
    analysis = Analysis(
        project_id=calculation.project_id,
        analysis_type=AnalysisType.COHP,
        input_artifact_ids=tuple(artifact.id for _, artifact in artifact_roles),
        status=AnalysisStatus.COMPLETED,
        tool=intake.invocation.tool,
        tool_version=intake.invocation.tool_version,
        parameters_hash=receipt_hash,
    )
    cohpcar_artifact = _write_analysis_bytes(
        root,
        analysis,
        "COHPCAR.lobster",
        ArtifactType.COHPCAR_LOBSTER,
        cohpcar_bytes,
    )
    icohplist_artifact = _write_analysis_bytes(
        root,
        analysis,
        "ICOHPLIST.lobster",
        ArtifactType.ICOHPLIST_LOBSTER,
        icohplist_bytes,
    )
    payload = {
        "format": CANONICAL_COHP_FORMAT,
        "version": CANONICAL_COHP_VERSION,
        "analysis_id": analysis.id,
        "cohpcar_artifact_id": cohpcar_artifact.id,
        "icohplist_artifact_id": icohplist_artifact.id,
        "source_receipt": source_receipt,
        "source_receipt_hash": receipt_hash,
        "result_content_hash": intake.result.content_hash,
        "result": intake.result,
    }
    result_artifact = _write_analysis_bytes(
        root,
        analysis,
        "canonical-cohp.json",
        ArtifactType.DERIVED_DATASET,
        (canonical_json(payload) + "\n").encode("utf-8"),
    )
    provenance_records = (
        ProvenanceRecord(
            subject_id=analysis.id,
            tool=intake.invocation.tool,
            tool_version=intake.invocation.tool_version,
            parameters_hash=receipt_hash,
            method_fingerprint_id=calculation.method_fingerprint_id,
        ),
        ProvenanceRecord(
            subject_id=cohpcar_artifact.id,
            tool=intake.invocation.tool,
            tool_version=intake.invocation.tool_version,
            parameters_hash=intake.cohpcar_sha256,
            method_fingerprint_id=calculation.method_fingerprint_id,
        ),
        ProvenanceRecord(
            subject_id=icohplist_artifact.id,
            tool=intake.invocation.tool,
            tool_version=intake.invocation.tool_version,
            parameters_hash=intake.icohplist_sha256,
            method_fingerprint_id=calculation.method_fingerprint_id,
        ),
        ProvenanceRecord(
            subject_id=result_artifact.id,
            tool=LOBSTER_COHP_MATERIALIZER_NAME,
            tool_version=LOBSTER_COHP_MATERIALIZER_VERSION,
            parameters_hash=result_artifact.sha256,
            method_fingerprint_id=calculation.method_fingerprint_id,
        ),
    )
    dependency_records: list[DependencyRecord] = [
        DependencyRecord(
            upstream_id=calculation.id,
            downstream_id=analysis.id,
            kind=DependencyKind.SCIENTIFIC,
            role="lobster_prerequisite_calculation",
            recorded_hash=scientific_hash(calculation),
        ),
        DependencyRecord(
            upstream_id=snapshot.id,
            downstream_id=analysis.id,
            kind=DependencyKind.SCIENTIFIC,
            role="structure_snapshot",
            recorded_hash=scientific_hash(snapshot),
        ),
        DependencyRecord(
            upstream_id=method_fingerprint.id,
            downstream_id=analysis.id,
            kind=DependencyKind.SCIENTIFIC,
            role="method_fingerprint",
            recorded_hash=scientific_hash(method_fingerprint),
        ),
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
    for role, artifact in (
        ("raw_cohpcar", cohpcar_artifact),
        ("raw_icohplist", icohplist_artifact),
        ("canonical_cohp", result_artifact),
    ):
        dependency_records.append(
            DependencyRecord(
                upstream_id=analysis.id,
                downstream_id=artifact.id,
                kind=DependencyKind.SCIENTIFIC,
                role=role,
                recorded_hash=scientific_hash(analysis),
            )
        )
    return DurableCohpMaterialization(
        analysis=analysis,
        cohpcar_artifact=cohpcar_artifact,
        icohplist_artifact=icohplist_artifact,
        result_artifact=result_artifact,
        provenance_records=provenance_records,
        dependency_records=tuple(dependency_records),
    )


def load_canonical_cohp_artifact(
    *,
    project_root: Path | str,
    analysis: Analysis,
    cohpcar_artifact: Artifact,
    icohplist_artifact: Artifact,
    result_artifact: Artifact,
) -> CanonicalCohpResult:
    """Reopen and validate one durable LOBSTER COHP/ICOHP result."""

    if analysis.analysis_type is not AnalysisType.COHP or analysis.status is not AnalysisStatus.COMPLETED:
        raise LobsterCohpError("canonical COHP requires a completed AnalysisType.COHP")
    _validate_output(analysis, cohpcar_artifact, ArtifactType.COHPCAR_LOBSTER, "COHPCAR.lobster")
    _validate_output(
        analysis,
        icohplist_artifact,
        ArtifactType.ICOHPLIST_LOBSTER,
        "ICOHPLIST.lobster",
    )
    _validate_output(analysis, result_artifact, ArtifactType.DERIVED_DATASET, "canonical-cohp.json")
    root = Path(project_root).resolve()
    cohpcar = _verified_bytes(root, cohpcar_artifact, "COHPCAR.lobster")
    icohplist = _verified_bytes(root, icohplist_artifact, "ICOHPLIST.lobster")
    payload_bytes = _verified_bytes(root, result_artifact, "canonical COHP Artifact")
    try:
        raw_payload = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LobsterCohpError("canonical COHP Artifact is not valid UTF-8 JSON") from error
    payload = _mapping(raw_payload, "canonical COHP Artifact")
    if payload.get("format") != CANONICAL_COHP_FORMAT or payload.get("version") != CANONICAL_COHP_VERSION:
        raise LobsterCohpError("canonical COHP Artifact format/version is unsupported")
    if payload.get("analysis_id") != str(analysis.id):
        raise LobsterCohpError("canonical COHP Artifact belongs to another Analysis")
    if payload.get("cohpcar_artifact_id") != str(cohpcar_artifact.id):
        raise LobsterCohpError("canonical COHP Artifact references another COHPCAR")
    if payload.get("icohplist_artifact_id") != str(icohplist_artifact.id):
        raise LobsterCohpError("canonical COHP Artifact references another ICOHPLIST")
    receipt = _mapping(payload.get("source_receipt"), "source_receipt")
    receipt_hash = payload.get("source_receipt_hash")
    if receipt_hash != analysis.parameters_hash or canonical_sha256(receipt) != receipt_hash:
        raise LobsterCohpError("canonical COHP source receipt is inconsistent")
    if receipt.get("cohpcar_sha256") != hashlib.sha256(cohpcar).hexdigest():
        raise LobsterCohpError("canonical COHP source receipt COHPCAR hash differs")
    if receipt.get("icohplist_sha256") != hashlib.sha256(icohplist).hexdigest():
        raise LobsterCohpError("canonical COHP source receipt ICOHPLIST hash differs")
    decoded = _decode_result(payload.get("result"))
    if payload.get("result_content_hash") != decoded.content_hash:
        raise LobsterCohpError("canonical COHP result content hash is inconsistent")
    return decoded


def _series_for_interaction(
    *,
    rows: list[tuple[float, ...]],
    interaction_position: int,
    interaction_count: int,
    spins: tuple[SpinChannel, ...],
    zero_index: int | None,
    ef_values: dict[SpinChannel, float] | None,
) -> tuple[CohpSpinSeries, ...]:
    result: list[CohpSpinSeries] = []
    for spin_index, spin in enumerate(spins):
        base = 1 + spin_index * 2 * interaction_count + 2 * interaction_position
        cohp = tuple(row[base] for row in rows)
        icohp = tuple(row[base + 1] for row in rows)
        if ef_values is not None:
            icohp_at_fermi = ef_values.get(spin)
            if icohp_at_fermi is None:
                raise LobsterCohpError("ICOHPLIST spin schema differs from COHPCAR")
        elif zero_index is not None:
            icohp_at_fermi = icohp[zero_index]
        else:
            icohp_at_fermi = None
        if zero_index is not None and icohp_at_fermi is not None:
            if abs(icohp[zero_index] - icohp_at_fermi) > 5.0e-4:
                raise LobsterCohpError("COHPCAR and ICOHPLIST ICOHP(E_F) values disagree")
        result.append(CohpSpinSeries(spin, cohp, icohp, icohp_at_fermi))
    return tuple(result)


def _parse_interaction_header(
    line: str,
    entry_by_ordinal: dict[int, _AtomMapEntry],
) -> _InteractionHeader:
    stripped = line.strip()
    match = _INTERACTION_RE.fullmatch(stripped)
    if match is None:
        raise LobsterCohpError(f"unsupported COHPCAR interaction header: {stripped}")
    source_index = int(match.group(1))
    center_a = _parse_center(match.group(2), entry_by_ordinal)
    center_b = _parse_center(match.group(3), entry_by_ordinal)
    try:
        length = float(match.group(4))
    except ValueError as error:
        raise LobsterCohpError("COHPCAR interaction length is invalid") from error
    if not isfinite(length) or length <= 0:
        raise LobsterCohpError("COHPCAR interaction length must be finite and positive")
    return _InteractionHeader(source_index, stripped, center_a, center_b, length)


def _parse_center(token: str, entry_by_ordinal: dict[int, _AtomMapEntry]) -> _ParsedCenter:
    match = _CENTER_RE.fullmatch(token.strip())
    if match is None:
        raise LobsterCohpError(f"unsupported LOBSTER center label: {token}")
    element = match.group(1)
    ordinal = int(match.group(2))
    entry = entry_by_ordinal.get(ordinal)
    if entry is None:
        raise LobsterCohpError("LOBSTER center ordinal is outside frozen atom-index-map")
    if entry.element != element:
        raise LobsterCohpError("LOBSTER center element differs from frozen atom-index-map")
    cell_text = match.group(3)
    cell = tuple(int(value) for value in cell_text.split()) if cell_text is not None else None
    orbital = match.group(4)
    return _ParsedCenter(entry.atom_uid, element, ordinal, cast(tuple[int, int, int] | None, cell), orbital)


def _parse_icohplist(
    *,
    icohplist_bytes: bytes,
    entry_by_ordinal: dict[int, _AtomMapEntry],
    expected_spins: tuple[SpinChannel, ...],
) -> tuple[_IcohpEfRow, ...]:
    try:
        lines = icohplist_bytes.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise LobsterCohpError("ICOHPLIST.lobster must be UTF-8 text") from error
    parsed: dict[tuple[int, int, int], dict[SpinChannel, float]] = {}
    metadata: dict[tuple[int, int, int], tuple[_ParsedCenter, _ParsedCenter, float, tuple[int, int, int]]] = {}
    duplicate_pass = False
    seen_total_rows = 0
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if "spin" in stripped.casefold() or "distance" in stripped.casefold():
            if seen_total_rows:
                duplicate_pass = True
            continue
        parts = stripped.split()
        if len(parts) not in {6, 8, 9} or not parts[0].isdigit():
            continue
        if "_" in parts[1] or "_" in parts[2]:
            continue
        try:
            source_index = int(parts[0])
            center_a = _parse_center(parts[1], entry_by_ordinal)
            center_b = _parse_center(parts[2], entry_by_ordinal)
            length = float(parts[3])
            if len(parts) == 6:
                translation = (0, 0, 0)
                up_value = float(parts[4])
                down_value = None
            else:
                translation = (int(parts[4]), int(parts[5]), int(parts[6]))
                up_value = float(parts[7])
                down_value = float(parts[8]) if len(parts) == 9 else None
        except ValueError as error:
            raise LobsterCohpError("ICOHPLIST.lobster contains an invalid total interaction row") from error
        if not isfinite(length) or length <= 0 or not isfinite(up_value):
            raise LobsterCohpError("ICOHPLIST total interaction values must be finite")
        key = (source_index, center_a.ordinal, center_b.ordinal)
        if key not in metadata:
            metadata[key] = (center_a, center_b, length, translation)
            parsed[key] = {}
            seen_total_rows += 1
        existing = parsed[key]
        if down_value is not None:
            if not isfinite(down_value):
                raise LobsterCohpError("ICOHPLIST down-spin value must be finite")
            existing[SpinChannel.UP] = up_value
            existing[SpinChannel.DOWN] = down_value
        elif expected_spins == (SpinChannel.TOTAL,):
            existing[SpinChannel.TOTAL] = up_value
        elif not existing or not duplicate_pass:
            existing[SpinChannel.UP] = up_value
        else:
            existing[SpinChannel.DOWN] = up_value
    rows: list[_IcohpEfRow] = []
    for key, values in parsed.items():
        if tuple(values) != expected_spins:
            raise LobsterCohpError("ICOHPLIST spin schema differs from COHPCAR")
        center_a, center_b, length, translation = metadata[key]
        rows.append(
            _IcohpEfRow(
                source_index=key[0],
                center_a=center_a,
                center_b=center_b,
                length=length,
                translation_b=translation,
                values=tuple((spin, values[spin]) for spin in expected_spins),
            )
        )
    if not rows:
        raise LobsterCohpError("ICOHPLIST.lobster contains no total pair interactions")
    return tuple(rows)


def _validate_ef_row_against_header(row: _IcohpEfRow, header: _InteractionHeader) -> None:
    if abs(row.length - header.length) > 1.0e-6:
        raise LobsterCohpError("COHPCAR and ICOHPLIST bond lengths disagree")
    if header.center_a.cell is not None and header.center_a.cell != (0, 0, 0):
        raise LobsterCohpError("COHPCAR first-center translation must be origin for ICOHP matching")
    if header.center_b.cell is not None and header.center_b.cell != row.translation_b:
        raise LobsterCohpError("COHPCAR and ICOHPLIST cell translations disagree")


def _header_is_orbital(header: _InteractionHeader) -> bool:
    return header.center_a.orbital is not None or header.center_b.orbital is not None


def _exact_zero_index(energies: tuple[float, ...]) -> int | None:
    matches = tuple(index for index, value in enumerate(energies) if abs(value) <= 1.0e-8)
    if len(matches) > 1:
        raise LobsterCohpError("COHPCAR energy grid contains duplicate Fermi-level points")
    return matches[0] if matches else None


def _validate_invocation_shape(invocation: ExternalToolInvocation) -> None:
    if invocation.tool.strip().casefold() != "lobster":
        raise LobsterCohpError("COHP intake requires external tool name 'lobster'")
    roles = frozenset(item.role for item in invocation.inputs)
    missing = _REQUIRED_INVOCATION_ROLES - roles
    if missing:
        raise LobsterCohpError(
            "LOBSTER invocation requires wavefunction, poscar, potcar, and lobsterin digests"
        )


def _validate_materialization_context(
    *,
    calculation: Calculation,
    snapshot: StructureSnapshot,
    method_fingerprint: MethodFingerprint,
    execution_attempt: ExecutionAttempt,
    wavecar_artifact: Artifact,
    atom_index_map_artifact: Artifact,
    prerequisite_input_artifacts: tuple[Artifact, ...],
    intake: CanonicalCohpIntake,
) -> None:
    if calculation.calculation_type is not CalculationType.LOBSTER_PREREQUISITE:
        raise LobsterCohpError("COHP Analysis requires LOBSTER_PREREQUISITE Calculation")
    if calculation.status is not CalculationScientificStatus.CONVERGED:
        raise LobsterCohpError("LOBSTER prerequisite Calculation must be scientifically converged")
    if calculation.input_structure_snapshot_id != snapshot.id:
        raise LobsterCohpError("LOBSTER prerequisite snapshot binding differs")
    if calculation.method_fingerprint_id != method_fingerprint.id:
        raise LobsterCohpError("LOBSTER prerequisite MethodFingerprint binding differs")
    if intake.result.structure_snapshot_id != snapshot.id:
        raise LobsterCohpError("canonical COHP result belongs to another StructureSnapshot")
    if execution_attempt.calculation_id != calculation.id:
        raise LobsterCohpError("LOBSTER WAVECAR ExecutionAttempt belongs to another Calculation")
    producer = wavecar_artifact.producer
    if not isinstance(producer, ExecutionAttemptProducerRef) or producer.id != execution_attempt.id:
        raise LobsterCohpError("WAVECAR producer does not match supplied ExecutionAttempt")
    atom_producer = atom_index_map_artifact.producer
    if not isinstance(atom_producer, CalculationProducerRef) or atom_producer.id != calculation.id:
        raise LobsterCohpError("atom-index-map must be produced by the prerequisite Calculation")
    if len(prerequisite_input_artifacts) != len(set(item.id for item in prerequisite_input_artifacts)):
        raise LobsterCohpError("LOBSTER prerequisite input Artifacts must be unique")
    if {item.artifact_type for item in prerequisite_input_artifacts} != _REQUIRED_PREREQUISITE_TYPES:
        raise LobsterCohpError("LOBSTER prerequisite inputs must exactly cover POSCAR/INCAR/KPOINTS/POTCAR.spec")
    for artifact in prerequisite_input_artifacts:
        producer = artifact.producer
        if not isinstance(producer, CalculationProducerRef) or producer.id != calculation.id:
            raise LobsterCohpError("LOBSTER prerequisite input Artifact belongs to another Calculation")
    _validate_invocation_shape(intake.invocation)


def _parse_atom_index_map(
    body: bytes,
    structure_snapshot_id: StructureSnapshotId,
) -> tuple[_AtomMapEntry, ...]:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LobsterCohpError("atom-index-map.json is not valid UTF-8 JSON") from error
    mapping = _mapping(payload, "atom-index-map.json")
    if mapping.get("format") != "ecatvasp-v03-atom-index-map" or mapping.get("version") != 1:
        raise LobsterCohpError("unsupported atom-index-map.json format/version")
    if mapping.get("structure_snapshot_id") != str(structure_snapshot_id):
        raise LobsterCohpError("atom-index-map.json belongs to another StructureSnapshot")
    for key in ("structure_content_sha256", "poscar_sha256"):
        raw = mapping.get(key)
        if not isinstance(raw, str):
            raise LobsterCohpError(f"atom-index-map.json {key} is missing")
        _normalized_sha256(raw, key)
    raw_entries = mapping.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise LobsterCohpError("atom-index-map.json entries must be a non-empty array")
    entries: list[_AtomMapEntry] = []
    seen_uids: set[UUID] = set()
    for expected_index, raw in enumerate(raw_entries):
        item = _mapping(raw, "atom-index-map entry")
        if item.get("poscar_index") != expected_index or item.get("vasp_ordinal") != expected_index + 1:
            raise LobsterCohpError("atom-index-map.json indices must be contiguous")
        try:
            atom_uid = AtomUid(UUID(str(item.get("atom_uid"))))
        except (ValueError, TypeError, AttributeError) as error:
            raise LobsterCohpError("atom-index-map.json atom_uid is invalid") from error
        if UUID(str(atom_uid)) in seen_uids:
            raise LobsterCohpError("atom-index-map.json atom_uid values must be unique")
        seen_uids.add(UUID(str(atom_uid)))
        element = item.get("element")
        if not isinstance(element, str) or not element.strip():
            raise LobsterCohpError("atom-index-map.json element is invalid")
        entries.append(_AtomMapEntry(atom_uid, element, expected_index + 1))
    species_order = mapping.get("species_order")
    species_counts = mapping.get("species_counts")
    if not isinstance(species_order, list) or not isinstance(species_counts, list):
        raise LobsterCohpError("atom-index-map.json species metadata is invalid")
    if len(species_order) != len(species_counts) or any(
        not isinstance(item, str) or not item.strip() for item in species_order
    ) or any(isinstance(item, bool) or not isinstance(item, int) or item < 1 for item in species_counts):
        raise LobsterCohpError("atom-index-map.json species metadata is invalid")
    expected_elements = tuple(
        element
        for element, count in zip(species_order, species_counts, strict=True)
        for _ in range(count)
    )
    if expected_elements != tuple(item.element for item in entries):
        raise LobsterCohpError("atom-index-map.json species order/counts differ from entries")
    return tuple(entries)


def _invocation_digest(invocation: ExternalToolInvocation, role: str) -> str:
    for item in invocation.inputs:
        if item.role == role:
            return item.sha256
    raise LobsterCohpError(f"LOBSTER invocation is missing {role} digest")


def _verify_local_artifact(
    root: Path,
    artifact: Artifact,
    expected_type: ArtifactType,
    label: str,
) -> Artifact:
    if artifact.artifact_type is not expected_type:
        raise LobsterCohpError(f"{label} has unexpected ArtifactType")
    _verified_bytes(root, artifact, label)
    return artifact


def _verified_bytes(root: Path, artifact: Artifact, label: str) -> bytes:
    if artifact.availability not in {ArtifactAvailability.LOCAL, ArtifactAvailability.BOTH}:
        raise LobsterCohpError(f"{label} must be locally available")
    if artifact.sha256 is None or artifact.size_bytes is None:
        raise LobsterCohpError(f"{label} requires exact size/SHA-256 metadata")
    path = _resolve_local_path(root, artifact, label)
    try:
        body = path.read_bytes()
    except OSError as error:
        raise LobsterCohpError(f"{label} cannot be read") from error
    if len(body) != artifact.size_bytes:
        raise LobsterCohpError(f"{label} local byte size changed")
    if hashlib.sha256(body).hexdigest() != artifact.sha256:
        raise LobsterCohpError(f"{label} local content hash changed")
    return body


def _write_analysis_bytes(
    root: Path,
    analysis: Analysis,
    filename: str,
    artifact_type: ArtifactType,
    body: bytes,
) -> Artifact:
    relative = Path("analyses") / str(analysis.id) / filename
    absolute = (root / relative).resolve()
    if not absolute.is_relative_to(root):
        raise LobsterCohpError("LOBSTER output path resolves outside project_root")
    absolute.parent.mkdir(parents=True, exist_ok=True)
    if absolute.exists():
        if not absolute.is_file() or absolute.read_bytes() != body:
            raise LobsterCohpError(f"{filename} already exists with different content")
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


def _validate_output(
    analysis: Analysis,
    artifact: Artifact,
    artifact_type: ArtifactType,
    filename: str,
) -> None:
    producer = artifact.producer
    if not isinstance(producer, AnalysisProducerRef) or producer.id != analysis.id:
        raise LobsterCohpError(f"{filename} producer does not match Analysis")
    if artifact.artifact_type is not artifact_type:
        raise LobsterCohpError(f"{filename} has unexpected ArtifactType")
    if PurePosixPath(artifact.local_path or "").name != filename:
        raise LobsterCohpError(f"{filename} Artifact has unexpected filename")


def _resolve_local_path(root: Path, artifact: Artifact, label: str) -> Path:
    if artifact.local_path is None:
        raise LobsterCohpError(f"{label} requires local_path")
    relative = PurePosixPath(artifact.local_path)
    if relative.is_absolute() or ".." in relative.parts or artifact.local_path != relative.as_posix():
        raise LobsterCohpError(f"{label} local_path must be normalized and relative")
    path = (root / Path(*relative.parts)).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise LobsterCohpError(f"{label} local file is missing or outside project_root")
    return path


def _decode_result(raw: object) -> CanonicalCohpResult:
    mapping = _mapping(raw, "canonical COHP result")
    energies_raw = mapping.get("energies_ev_relative_to_fermi")
    average_raw = mapping.get("average_series")
    interactions_raw = mapping.get("interactions")
    if not isinstance(energies_raw, list) or not isinstance(average_raw, list) or not isinstance(interactions_raw, list):
        raise LobsterCohpError("canonical COHP arrays are invalid")
    return CanonicalCohpResult(
        structure_snapshot_id=StructureSnapshotId(UUID(_string(mapping.get("structure_snapshot_id")))),
        energies_ev_relative_to_fermi=tuple(_number(value, "energy") for value in energies_raw),
        source_fermi_energy_ev=_number(mapping.get("source_fermi_energy_ev"), "source Fermi"),
        average_series=tuple(_decode_series(item) for item in average_raw),
        interactions=tuple(_decode_interaction(item) for item in interactions_raw),
        atom_index_map_sha256=_string(mapping.get("atom_index_map_sha256")),
        energy_reference=CohpEnergyReference(_string(mapping.get("energy_reference"))),
        contract_version=_integer(mapping.get("contract_version")),
    )


def _decode_series(raw: object) -> CohpSpinSeries:
    mapping = _mapping(raw, "COHP spin series")
    cohp_raw = mapping.get("cohp_values")
    icohp_raw = mapping.get("icohp_values")
    if not isinstance(cohp_raw, list) or not isinstance(icohp_raw, list):
        raise LobsterCohpError("COHP spin series arrays are invalid")
    ef_raw = mapping.get("icohp_at_fermi_ev")
    return CohpSpinSeries(
        spin=SpinChannel(_string(mapping.get("spin"))),
        cohp_values=tuple(_number(value, "COHP") for value in cohp_raw),
        icohp_values=tuple(_number(value, "ICOHP") for value in icohp_raw),
        icohp_at_fermi_ev=None if ef_raw is None else _number(ef_raw, "ICOHP at Fermi"),
    )


def _decode_interaction(raw: object) -> CohpInteraction:
    mapping = _mapping(raw, "COHP interaction")
    series_raw = mapping.get("series")
    if not isinstance(series_raw, list):
        raise LobsterCohpError("COHP interaction series must be an array")
    return CohpInteraction(
        source_index=_integer(mapping.get("source_index")),
        source_label=_string(mapping.get("source_label")),
        atom_uid_a=AtomUid(UUID(_string(mapping.get("atom_uid_a")))),
        atom_uid_b=AtomUid(UUID(_string(mapping.get("atom_uid_b")))),
        element_a=_string(mapping.get("element_a")),
        element_b=_string(mapping.get("element_b")),
        bond_length_angstrom=_number(mapping.get("bond_length_angstrom"), "bond length"),
        series=tuple(_decode_series(item) for item in series_raw),
        cell_a=_decode_cell(mapping.get("cell_a")),
        cell_b=_decode_cell(mapping.get("cell_b")),
        orbital_a=_optional_string(mapping.get("orbital_a")),
        orbital_b=_optional_string(mapping.get("orbital_b")),
    )


def _decode_cell(raw: object) -> tuple[int, int, int] | None:
    if raw is None:
        return None
    if not isinstance(raw, list) or len(raw) != 3 or any(
        isinstance(value, bool) or not isinstance(value, int) for value in raw
    ):
        raise LobsterCohpError("COHP cell translation is invalid")
    return cast(tuple[int, int, int], tuple(raw))


def _mapping(value: object, field_name: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise LobsterCohpError(f"{field_name} must be an object")
    return cast(dict[str, object], value)


def _string(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LobsterCohpError("LOBSTER string field is invalid")
    return value


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return _string(value)


def _integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise LobsterCohpError("LOBSTER integer field is invalid")
    return value


def _number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LobsterCohpError(f"{field_name} must be numeric")
    result = float(value)
    if not isfinite(result):
        raise LobsterCohpError(f"{field_name} must be finite")
    return result


def _normalized_sha256(value: str, field_name: str) -> str:
    normalized = value.lower()
    if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
        raise LobsterCohpError(f"{field_name} must be a 64-character hexadecimal SHA-256 digest")
    return normalized

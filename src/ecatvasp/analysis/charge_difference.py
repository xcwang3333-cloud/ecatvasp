"""Durable charge-density-difference analysis for v0.7 Block 5."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from pathlib import Path, PurePosixPath
from typing import cast

import ase  # type: ignore[import-untyped]
import numpy as np
from ase.calculators.vasp import VaspChargeDensity  # type: ignore[import-untyped]
from numpy.typing import NDArray

from ecatvasp.domain import (
    Analysis,
    AnalysisProducerRef,
    AnalysisStatus,
    AnalysisType,
    Artifact,
    ArtifactAvailability,
    ArtifactType,
    CalculationScientificStatus,
    CalculationType,
    ExecutionAttempt,
    ExecutionAttemptProducerRef,
    RetrievalPolicy,
    StructureSnapshot,
    canonical_json,
    canonical_sha256,
)
from ecatvasp.provenance import (
    DependencyKind,
    DependencyRecord,
    ProvenanceRecord,
    scientific_hash,
)
from ecatvasp.vasp.analysis_pipeline import (
    ChargeDifferenceTriplet,
    ChargeDifferenceTripletMember,
)
from ecatvasp.vasp.poscar import prepare_poscar

CHARGE_DIFFERENCE_TOOL_NAME = "ecatvasp.analysis.charge-difference"
CHARGE_DIFFERENCE_TOOL_VERSION = "1"
CANONICAL_CHARGE_DIFFERENCE_FORMAT = "ecatvasp-canonical-charge-difference"
CANONICAL_CHARGE_DIFFERENCE_VERSION = 1
DENSITY_UNIT = "1/angstrom^3"
DENSITY_AXIS_ORDER = "xyz"
DENSITY_DTYPE = "<f8"
DELTA_CONVENTION = "combined_minus_slab_minus_adsorbate"


class ChargeDifferenceAnalysisError(ValueError):
    """Raised when charge-density subtraction cannot be performed exactly."""


class ChargeDifferenceRole(StrEnum):
    """Semantic member roles in the frozen charge-difference triplet."""

    COMBINED = "combined"
    SLAB = "slab"
    ADSORBATE = "adsorbate"


@dataclass(frozen=True, slots=True)
class ChargeDifferenceSource:
    """Exact execution-produced CHGCAR source for one triplet member."""

    role: ChargeDifferenceRole
    execution_attempt: ExecutionAttempt
    chgcar_artifact: Artifact


@dataclass(frozen=True, slots=True)
class ChargeDifferenceMetadata:
    """Portable metadata for a durable physical-density difference grid."""

    triplet_contract_hash: str
    grid_shape_xyz: tuple[int, int, int]
    cell_volume_angstrom3: float
    voxel_volume_angstrom3: float
    density_unit: str
    axis_order: str
    dtype: str
    delta_convention: str
    density_sha256: str
    combined_electron_integral: float
    slab_electron_integral: float
    adsorbate_electron_integral: float
    delta_electron_integral: float
    density_min: float
    density_max: float
    ase_version: str
    contract_version: int = CANONICAL_CHARGE_DIFFERENCE_VERSION

    def __post_init__(self) -> None:
        if self.contract_version != CANONICAL_CHARGE_DIFFERENCE_VERSION:
            raise ChargeDifferenceAnalysisError(
                "unsupported canonical charge-difference contract version"
            )
        object.__setattr__(
            self,
            "triplet_contract_hash",
            _normalized_sha256(self.triplet_contract_hash, "triplet_contract_hash"),
        )
        object.__setattr__(
            self,
            "density_sha256",
            _normalized_sha256(self.density_sha256, "density_sha256"),
        )
        if len(self.grid_shape_xyz) != 3 or any(value < 1 for value in self.grid_shape_xyz):
            raise ChargeDifferenceAnalysisError("grid_shape_xyz requires three positive integers")
        _require_positive(self.cell_volume_angstrom3, "cell_volume_angstrom3")
        _require_positive(self.voxel_volume_angstrom3, "voxel_volume_angstrom3")
        for value, name in (
            (self.combined_electron_integral, "combined_electron_integral"),
            (self.slab_electron_integral, "slab_electron_integral"),
            (self.adsorbate_electron_integral, "adsorbate_electron_integral"),
            (self.delta_electron_integral, "delta_electron_integral"),
            (self.density_min, "density_min"),
            (self.density_max, "density_max"),
        ):
            _require_finite(value, name)
        if self.density_unit != DENSITY_UNIT:
            raise ChargeDifferenceAnalysisError("unsupported charge-density unit")
        if self.axis_order != DENSITY_AXIS_ORDER:
            raise ChargeDifferenceAnalysisError("unsupported charge-density axis order")
        if self.dtype != DENSITY_DTYPE:
            raise ChargeDifferenceAnalysisError("unsupported charge-density dtype")
        if self.delta_convention != DELTA_CONVENTION:
            raise ChargeDifferenceAnalysisError("unsupported charge-difference convention")
        if not self.ase_version.strip():
            raise ChargeDifferenceAnalysisError("ASE version must not be blank")

    @property
    def content_hash(self) -> str:
        """Return deterministic scientific metadata identity."""

        return canonical_sha256(self)


@dataclass(frozen=True, slots=True)
class DurableChargeDifference:
    """Durable charge-difference Analysis plus density and metadata Artifacts."""

    analysis: Analysis
    density_artifact: Artifact
    metadata_artifact: Artifact
    metadata: ChargeDifferenceMetadata
    provenance_records: tuple[ProvenanceRecord, ...]
    dependency_records: tuple[DependencyRecord, ...]


@dataclass(frozen=True, slots=True)
class LoadedChargeDifference:
    """Validated durable metadata plus physical delta-density array."""

    metadata: ChargeDifferenceMetadata
    density: NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class _ParsedDensity:
    density: NDArray[np.float64]
    cell_volume_angstrom3: float
    sha256: str
    artifact: Artifact


def materialize_charge_difference_analysis(
    *,
    project_root: Path | str,
    triplet: ChargeDifferenceTriplet,
    combined_source: ChargeDifferenceSource,
    slab_source: ChargeDifferenceSource,
    adsorbate_source: ChargeDifferenceSource,
) -> DurableChargeDifference:
    """Subtract exact compatible CHGCAR total-density grids and persist the result."""

    sources = _ordered_sources(combined_source, slab_source, adsorbate_source)
    members = _ordered_members(triplet)
    _validate_sources(members=members, sources=sources)
    root = Path(project_root).resolve()
    if not root.is_dir():
        raise ChargeDifferenceAnalysisError("project_root must be an existing directory")

    parsed = tuple(
        _parse_source_density(root=root, member=member, source=source)
        for member, source in zip(members, sources, strict=True)
    )
    combined, slab, adsorbate = parsed
    if slab.density.shape != combined.density.shape or (
        adsorbate.density.shape != combined.density.shape
    ):
        raise ChargeDifferenceAnalysisError(
            "charge-difference CHGCAR FFT grids must have identical dimensions"
        )
    if not np.isclose(
        slab.cell_volume_angstrom3,
        combined.cell_volume_angstrom3,
        rtol=0.0,
        atol=1.0e-9,
    ) or not np.isclose(
        adsorbate.cell_volume_angstrom3,
        combined.cell_volume_angstrom3,
        rtol=0.0,
        atol=1.0e-9,
    ):
        raise ChargeDifferenceAnalysisError(
            "charge-difference CHGCAR cells must have identical volume"
        )

    delta = np.asarray(
        combined.density - slab.density - adsorbate.density,
        dtype=np.float64,
    )
    if not np.all(np.isfinite(delta)):
        raise ChargeDifferenceAnalysisError("charge-difference density contains non-finite values")
    canonical_delta = np.ascontiguousarray(delta, dtype=np.dtype(DENSITY_DTYPE))
    density_body = canonical_delta.tobytes(order="C")
    density_hash = hashlib.sha256(density_body).hexdigest()
    grid_shape = cast(tuple[int, int, int], tuple(int(value) for value in delta.shape))
    grid_count = int(np.prod(grid_shape, dtype=np.int64))
    cell_volume = combined.cell_volume_angstrom3
    voxel_volume = cell_volume / grid_count

    metadata = ChargeDifferenceMetadata(
        triplet_contract_hash=triplet.contract_hash,
        grid_shape_xyz=grid_shape,
        cell_volume_angstrom3=cell_volume,
        voxel_volume_angstrom3=voxel_volume,
        density_unit=DENSITY_UNIT,
        axis_order=DENSITY_AXIS_ORDER,
        dtype=DENSITY_DTYPE,
        delta_convention=DELTA_CONVENTION,
        density_sha256=density_hash,
        combined_electron_integral=_integral(combined.density, voxel_volume),
        slab_electron_integral=_integral(slab.density, voxel_volume),
        adsorbate_electron_integral=_integral(adsorbate.density, voxel_volume),
        delta_electron_integral=_integral(delta, voxel_volume),
        density_min=float(np.min(delta)),
        density_max=float(np.max(delta)),
        ase_version=str(ase.__version__),
    )

    source_receipt = {
        "format": CANONICAL_CHARGE_DIFFERENCE_FORMAT,
        "version": CANONICAL_CHARGE_DIFFERENCE_VERSION,
        "triplet_contract_hash": triplet.contract_hash,
        "ase_parser": "ase.calculators.vasp.VaspChargeDensity",
        "ase_version": str(ase.__version__),
        "density_scaling": "chgcar_grid_values_divided_by_real_cell_volume",
        "delta_convention": DELTA_CONVENTION,
        "sources": tuple(
            {
                "role": source.role,
                "calculation_id": member.calculation.id,
                "structure_snapshot_id": member.snapshot.id,
                "method_fingerprint_id": member.fingerprint.id,
                "chgcar_artifact_id": parsed_item.artifact.id,
                "chgcar_sha256": parsed_item.sha256,
            }
            for member, source, parsed_item in zip(members, sources, parsed, strict=True)
        ),
    }
    source_receipt_hash = canonical_sha256(source_receipt)
    analysis = Analysis(
        project_id=triplet.combined.calculation.project_id,
        analysis_type=AnalysisType.CHARGE_DIFFERENCE,
        input_artifact_ids=tuple(item.artifact.id for item in parsed),
        status=AnalysisStatus.COMPLETED,
        tool=CHARGE_DIFFERENCE_TOOL_NAME,
        tool_version=CHARGE_DIFFERENCE_TOOL_VERSION,
        parameters_hash=source_receipt_hash,
    )
    density_artifact = _write_analysis_bytes(
        root=root,
        analysis=analysis,
        filename="charge-difference.f64",
        body=density_body,
    )
    payload = {
        "format": CANONICAL_CHARGE_DIFFERENCE_FORMAT,
        "version": CANONICAL_CHARGE_DIFFERENCE_VERSION,
        "analysis_id": analysis.id,
        "density_artifact_id": density_artifact.id,
        "source_receipt": source_receipt,
        "source_receipt_hash": source_receipt_hash,
        "metadata_content_hash": metadata.content_hash,
        "metadata": metadata,
    }
    metadata_artifact = _write_analysis_bytes(
        root=root,
        analysis=analysis,
        filename="canonical-charge-difference.json",
        body=(canonical_json(payload) + "\n").encode("utf-8"),
    )

    provenance_records = (
        ProvenanceRecord(
            subject_id=analysis.id,
            tool=CHARGE_DIFFERENCE_TOOL_NAME,
            tool_version=CHARGE_DIFFERENCE_TOOL_VERSION,
            parameters_hash=source_receipt_hash,
        ),
        ProvenanceRecord(
            subject_id=density_artifact.id,
            tool=CHARGE_DIFFERENCE_TOOL_NAME,
            tool_version=CHARGE_DIFFERENCE_TOOL_VERSION,
            parameters_hash=density_hash,
        ),
        ProvenanceRecord(
            subject_id=metadata_artifact.id,
            tool=CHARGE_DIFFERENCE_TOOL_NAME,
            tool_version=CHARGE_DIFFERENCE_TOOL_VERSION,
            parameters_hash=metadata_artifact.sha256,
        ),
    )
    dependency_records = _dependency_records(
        triplet=triplet,
        analysis=analysis,
        parsed=parsed,
        density_artifact=density_artifact,
        metadata_artifact=metadata_artifact,
    )
    return DurableChargeDifference(
        analysis=analysis,
        density_artifact=density_artifact,
        metadata_artifact=metadata_artifact,
        metadata=metadata,
        provenance_records=provenance_records,
        dependency_records=dependency_records,
    )


def load_charge_difference_artifacts(
    *,
    project_root: Path | str,
    analysis: Analysis,
    density_artifact: Artifact,
    metadata_artifact: Artifact,
) -> LoadedChargeDifference:
    """Reopen and validate one durable charge-density-difference dataset."""

    if analysis.analysis_type is not AnalysisType.CHARGE_DIFFERENCE:
        raise ChargeDifferenceAnalysisError(
            "charge-difference dataset requires AnalysisType.CHARGE_DIFFERENCE"
        )
    if analysis.status is not AnalysisStatus.COMPLETED:
        raise ChargeDifferenceAnalysisError("charge-difference Analysis must be completed")
    _validate_output(analysis, density_artifact, "charge-difference.f64")
    _validate_output(
        analysis,
        metadata_artifact,
        "canonical-charge-difference.json",
    )
    root = Path(project_root).resolve()
    density_body = _verified_local_bytes(root, density_artifact, "charge-difference density")
    metadata_body = _verified_local_bytes(root, metadata_artifact, "charge-difference metadata")
    try:
        raw_payload = json.loads(metadata_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ChargeDifferenceAnalysisError(
            "canonical charge-difference metadata is not valid UTF-8 JSON"
        ) from error
    payload = _mapping(raw_payload, "canonical charge-difference metadata")
    if payload.get("format") != CANONICAL_CHARGE_DIFFERENCE_FORMAT:
        raise ChargeDifferenceAnalysisError("charge-difference metadata format is unsupported")
    if payload.get("version") != CANONICAL_CHARGE_DIFFERENCE_VERSION:
        raise ChargeDifferenceAnalysisError("charge-difference metadata version is unsupported")
    if payload.get("analysis_id") != str(analysis.id):
        raise ChargeDifferenceAnalysisError("charge-difference metadata belongs to another Analysis")
    if payload.get("density_artifact_id") != str(density_artifact.id):
        raise ChargeDifferenceAnalysisError("charge-difference metadata references another density")
    receipt = _mapping(payload.get("source_receipt"), "source_receipt")
    receipt_hash = payload.get("source_receipt_hash")
    if receipt_hash != analysis.parameters_hash or canonical_sha256(receipt) != receipt_hash:
        raise ChargeDifferenceAnalysisError("charge-difference source receipt is inconsistent")
    if _receipt_input_ids(receipt) != tuple(
        str(item) for item in analysis.input_artifact_ids
    ):
        raise ChargeDifferenceAnalysisError(
            "charge-difference source receipt inputs differ from Analysis"
        )
    metadata = _decode_metadata(payload.get("metadata"))
    if payload.get("metadata_content_hash") != metadata.content_hash:
        raise ChargeDifferenceAnalysisError("charge-difference metadata content hash is inconsistent")
    if hashlib.sha256(density_body).hexdigest() != metadata.density_sha256:
        raise ChargeDifferenceAnalysisError("charge-difference density hash differs from metadata")
    expected_size = int(np.prod(metadata.grid_shape_xyz, dtype=np.int64)) * 8
    if len(density_body) != expected_size:
        raise ChargeDifferenceAnalysisError("charge-difference density byte size is inconsistent")
    density = np.frombuffer(density_body, dtype=np.dtype(DENSITY_DTYPE)).copy()
    density = density.reshape(metadata.grid_shape_xyz, order="C")
    if not np.all(np.isfinite(density)):
        raise ChargeDifferenceAnalysisError("charge-difference density contains non-finite values")
    if not np.isclose(float(np.min(density)), metadata.density_min, rtol=0.0, atol=1e-14):
        raise ChargeDifferenceAnalysisError("charge-difference density minimum differs from metadata")
    if not np.isclose(float(np.max(density)), metadata.density_max, rtol=0.0, atol=1e-14):
        raise ChargeDifferenceAnalysisError("charge-difference density maximum differs from metadata")
    return LoadedChargeDifference(metadata=metadata, density=density)


def _ordered_sources(
    combined: ChargeDifferenceSource,
    slab: ChargeDifferenceSource,
    adsorbate: ChargeDifferenceSource,
) -> tuple[ChargeDifferenceSource, ...]:
    sources = (combined, slab, adsorbate)
    expected = (
        ChargeDifferenceRole.COMBINED,
        ChargeDifferenceRole.SLAB,
        ChargeDifferenceRole.ADSORBATE,
    )
    if tuple(item.role for item in sources) != expected:
        raise ChargeDifferenceAnalysisError("charge-difference source roles are misordered")
    return sources


def _ordered_members(
    triplet: ChargeDifferenceTriplet,
) -> tuple[ChargeDifferenceTripletMember, ...]:
    return (triplet.combined, triplet.slab, triplet.adsorbate)


def _validate_sources(
    *,
    members: tuple[ChargeDifferenceTripletMember, ...],
    sources: tuple[ChargeDifferenceSource, ...],
) -> None:
    attempts = tuple(item.execution_attempt.id for item in sources)
    if len(attempts) != len(set(attempts)):
        raise ChargeDifferenceAnalysisError("charge-difference ExecutionAttempts must be distinct")
    artifacts = tuple(item.chgcar_artifact.id for item in sources)
    if len(artifacts) != len(set(artifacts)):
        raise ChargeDifferenceAnalysisError("charge-difference CHGCAR Artifacts must be distinct")
    for member, source in zip(members, sources, strict=True):
        calculation = member.calculation
        if calculation.calculation_type is not CalculationType.CHARGE_STATIC:
            raise ChargeDifferenceAnalysisError(
                "charge-difference source Calculation must be CHARGE_STATIC"
            )
        if calculation.status is not CalculationScientificStatus.CONVERGED:
            raise ChargeDifferenceAnalysisError(
                "charge-difference requires scientifically converged Calculations"
            )
        if source.execution_attempt.calculation_id != calculation.id:
            raise ChargeDifferenceAnalysisError(
                f"{source.role.value} ExecutionAttempt belongs to another Calculation"
            )
        producer = source.chgcar_artifact.producer
        if not isinstance(producer, ExecutionAttemptProducerRef):
            raise ChargeDifferenceAnalysisError(
                f"{source.role.value} CHGCAR must be produced by an ExecutionAttempt"
            )
        if producer.id != source.execution_attempt.id:
            raise ChargeDifferenceAnalysisError(
                f"{source.role.value} CHGCAR producer does not match ExecutionAttempt"
            )


def _parse_source_density(
    *,
    root: Path,
    member: ChargeDifferenceTripletMember,
    source: ChargeDifferenceSource,
) -> _ParsedDensity:
    artifact = source.chgcar_artifact
    if artifact.artifact_type is not ArtifactType.CHGCAR:
        raise ChargeDifferenceAnalysisError(
            f"{source.role.value} source requires ArtifactType.CHGCAR"
        )
    body = _verified_local_bytes(root, artifact, f"{source.role.value} CHGCAR")
    if artifact.sha256 is None:
        raise ChargeDifferenceAnalysisError("CHGCAR requires SHA-256 metadata")
    path = _resolve_local_path(root, artifact, f"{source.role.value} CHGCAR")
    try:
        parsed = VaspChargeDensity(str(path))
    except (OSError, RuntimeError, ValueError, IndexError) as error:
        raise ChargeDifferenceAnalysisError(
            f"{source.role.value} CHGCAR cannot be parsed by ASE"
        ) from error
    if len(parsed.chg) != 1 or len(parsed.atoms) != 1:
        raise ChargeDifferenceAnalysisError(
            f"{source.role.value} CHGCAR must contain exactly one static charge-density image"
        )
    density = np.asarray(parsed.chg[0], dtype=np.float64)
    if density.ndim != 3 or any(value < 1 for value in density.shape):
        raise ChargeDifferenceAnalysisError(
            f"{source.role.value} CHGCAR has an invalid FFT grid"
        )
    if not np.all(np.isfinite(density)):
        raise ChargeDifferenceAnalysisError(
            f"{source.role.value} CHGCAR density contains non-finite values"
        )
    atoms = parsed.atoms[0]
    _validate_parsed_structure(
        role=source.role,
        snapshot=member.snapshot,
        symbols=tuple(atoms.get_chemical_symbols()),
        cell=np.asarray(atoms.cell.array, dtype=np.float64),
        scaled_positions=np.asarray(atoms.get_scaled_positions(wrap=False), dtype=np.float64),
    )
    volume = float(atoms.get_volume())
    _require_positive(volume, f"{source.role.value} CHGCAR cell volume")
    if hashlib.sha256(body).hexdigest() != artifact.sha256.lower():
        raise ChargeDifferenceAnalysisError(
            f"{source.role.value} CHGCAR content changed during parsing"
        )
    return _ParsedDensity(
        density=np.ascontiguousarray(density),
        cell_volume_angstrom3=volume,
        sha256=artifact.sha256.lower(),
        artifact=artifact,
    )


def _validate_parsed_structure(
    *,
    role: ChargeDifferenceRole,
    snapshot: StructureSnapshot,
    symbols: tuple[str, ...],
    cell: NDArray[np.float64],
    scaled_positions: NDArray[np.float64],
) -> None:
    prepared = prepare_poscar(snapshot)
    expected_symbols = tuple(
        snapshot.sites[item.snapshot_index].element for item in prepared.index_map.entries
    )
    expected_positions = np.asarray(
        [
            snapshot.sites[item.snapshot_index].fractional_coords
            for item in prepared.index_map.entries
        ],
        dtype=np.float64,
    )
    expected_cell = np.asarray(snapshot.lattice.vectors, dtype=np.float64)
    if symbols != expected_symbols:
        raise ChargeDifferenceAnalysisError(
            f"{role.value} CHGCAR species/order differs from frozen snapshot POSCAR order"
        )
    if cell.shape != (3, 3) or not np.allclose(
        cell,
        expected_cell,
        rtol=0.0,
        atol=1.0e-9,
    ):
        raise ChargeDifferenceAnalysisError(
            f"{role.value} CHGCAR lattice differs from frozen snapshot"
        )
    if scaled_positions.shape != expected_positions.shape or not np.allclose(
        scaled_positions,
        expected_positions,
        rtol=0.0,
        atol=1.0e-9,
    ):
        raise ChargeDifferenceAnalysisError(
            f"{role.value} CHGCAR coordinates differ from frozen snapshot"
        )


def _integral(density: NDArray[np.float64], voxel_volume: float) -> float:
    value = float(np.sum(density, dtype=np.float64) * voxel_volume)
    _require_finite(value, "charge-density integral")
    return value


def _dependency_records(
    *,
    triplet: ChargeDifferenceTriplet,
    analysis: Analysis,
    parsed: tuple[_ParsedDensity, ...],
    density_artifact: Artifact,
    metadata_artifact: Artifact,
) -> tuple[DependencyRecord, ...]:
    members = _ordered_members(triplet)
    roles = (
        ChargeDifferenceRole.COMBINED,
        ChargeDifferenceRole.SLAB,
        ChargeDifferenceRole.ADSORBATE,
    )
    records: list[DependencyRecord] = []
    for role, member, parsed_item in zip(roles, members, parsed, strict=True):
        for suffix, upstream in (
            ("calculation", member.calculation),
            ("snapshot", member.snapshot),
            ("method_fingerprint", member.fingerprint),
            ("chgcar", parsed_item.artifact),
        ):
            records.append(
                DependencyRecord(
                    upstream_id=upstream.id,
                    downstream_id=analysis.id,
                    kind=DependencyKind.SCIENTIFIC,
                    role=f"{role.value}_{suffix}",
                    recorded_hash=scientific_hash(upstream),
                )
            )
    records.extend(
        (
            DependencyRecord(
                upstream_id=analysis.id,
                downstream_id=density_artifact.id,
                kind=DependencyKind.SCIENTIFIC,
                role="charge_difference_density",
                recorded_hash=scientific_hash(analysis),
            ),
            DependencyRecord(
                upstream_id=analysis.id,
                downstream_id=metadata_artifact.id,
                kind=DependencyKind.SCIENTIFIC,
                role="charge_difference_metadata",
                recorded_hash=scientific_hash(analysis),
            ),
        )
    )
    return tuple(records)


def _write_analysis_bytes(
    *,
    root: Path,
    analysis: Analysis,
    filename: str,
    body: bytes,
) -> Artifact:
    relative = Path("analyses") / str(analysis.id) / filename
    absolute = (root / relative).resolve()
    if not absolute.is_relative_to(root):
        raise ChargeDifferenceAnalysisError(
            "charge-difference output path resolves outside project_root"
        )
    absolute.parent.mkdir(parents=True, exist_ok=True)
    if absolute.exists():
        if not absolute.is_file() or absolute.read_bytes() != body:
            raise ChargeDifferenceAnalysisError(
                f"{filename} already exists with different content"
            )
    else:
        temporary = absolute.with_name(f".{absolute.name}.tmp")
        try:
            temporary.write_bytes(body)
            os.replace(temporary, absolute)
        finally:
            if temporary.exists():
                temporary.unlink()
    return Artifact(
        artifact_type=ArtifactType.DERIVED_DATASET,
        producer=AnalysisProducerRef(analysis.id),
        availability=ArtifactAvailability.LOCAL,
        retrieval_policy=RetrievalPolicy.ALWAYS,
        local_path=relative.as_posix(),
        size_bytes=len(body),
        sha256=hashlib.sha256(body).hexdigest(),
    )


def _validate_output(analysis: Analysis, artifact: Artifact, filename: str) -> None:
    producer = artifact.producer
    if not isinstance(producer, AnalysisProducerRef) or producer.id != analysis.id:
        raise ChargeDifferenceAnalysisError(
            f"{filename} producer does not match charge-difference Analysis"
        )
    if artifact.artifact_type is not ArtifactType.DERIVED_DATASET:
        raise ChargeDifferenceAnalysisError(f"{filename} must be DERIVED_DATASET")
    if PurePosixPath(artifact.local_path or "").name != filename:
        raise ChargeDifferenceAnalysisError(f"{filename} Artifact has unexpected filename")


def _verified_local_bytes(root: Path, artifact: Artifact, label: str) -> bytes:
    if artifact.availability not in {ArtifactAvailability.LOCAL, ArtifactAvailability.BOTH}:
        raise ChargeDifferenceAnalysisError(f"{label} must be locally available")
    if artifact.sha256 is None or artifact.size_bytes is None:
        raise ChargeDifferenceAnalysisError(f"{label} requires exact size/SHA-256 metadata")
    path = _resolve_local_path(root, artifact, label)
    try:
        body = path.read_bytes()
    except OSError as error:
        raise ChargeDifferenceAnalysisError(f"{label} cannot be read") from error
    if len(body) != artifact.size_bytes:
        raise ChargeDifferenceAnalysisError(f"{label} local byte size changed")
    if hashlib.sha256(body).hexdigest() != artifact.sha256.lower():
        raise ChargeDifferenceAnalysisError(f"{label} local content hash changed")
    return body


def _resolve_local_path(root: Path, artifact: Artifact, label: str) -> Path:
    if artifact.local_path is None:
        raise ChargeDifferenceAnalysisError(f"{label} requires local_path")
    relative = PurePosixPath(artifact.local_path)
    if (
        relative.is_absolute()
        or artifact.local_path != relative.as_posix()
        or ".." in relative.parts
        or artifact.local_path in {"", "."}
    ):
        raise ChargeDifferenceAnalysisError(
            f"{label} local_path must be normalized and relative"
        )
    path = (root / Path(*relative.parts)).resolve()
    if not path.is_relative_to(root):
        raise ChargeDifferenceAnalysisError(f"{label} local_path resolves outside project_root")
    if not path.is_file():
        raise ChargeDifferenceAnalysisError(f"{label} local file is missing")
    return path


def _receipt_input_ids(receipt: dict[str, object]) -> tuple[str, ...]:
    raw_sources = receipt.get("sources")
    if not isinstance(raw_sources, list):
        raise ChargeDifferenceAnalysisError("charge-difference receipt sources must be an array")
    result: list[str] = []
    for raw in raw_sources:
        source = _mapping(raw, "charge-difference source")
        artifact_id = source.get("chgcar_artifact_id")
        if not isinstance(artifact_id, str) or not artifact_id.strip():
            raise ChargeDifferenceAnalysisError("charge-difference source artifact id is invalid")
        result.append(artifact_id)
    return tuple(result)


def _decode_metadata(raw: object) -> ChargeDifferenceMetadata:
    mapping = _mapping(raw, "charge-difference metadata")
    shape_raw = mapping.get("grid_shape_xyz")
    if not isinstance(shape_raw, list) or len(shape_raw) != 3 or any(
        isinstance(value, bool) or not isinstance(value, int) for value in shape_raw
    ):
        raise ChargeDifferenceAnalysisError("grid_shape_xyz is invalid")
    return ChargeDifferenceMetadata(
        triplet_contract_hash=_string(mapping.get("triplet_contract_hash")),
        grid_shape_xyz=cast(tuple[int, int, int], tuple(shape_raw)),
        cell_volume_angstrom3=_number(
            mapping.get("cell_volume_angstrom3"),
            "cell_volume_angstrom3",
        ),
        voxel_volume_angstrom3=_number(
            mapping.get("voxel_volume_angstrom3"),
            "voxel_volume_angstrom3",
        ),
        density_unit=_string(mapping.get("density_unit")),
        axis_order=_string(mapping.get("axis_order")),
        dtype=_string(mapping.get("dtype")),
        delta_convention=_string(mapping.get("delta_convention")),
        density_sha256=_string(mapping.get("density_sha256")),
        combined_electron_integral=_number(
            mapping.get("combined_electron_integral"),
            "combined_electron_integral",
        ),
        slab_electron_integral=_number(
            mapping.get("slab_electron_integral"),
            "slab_electron_integral",
        ),
        adsorbate_electron_integral=_number(
            mapping.get("adsorbate_electron_integral"),
            "adsorbate_electron_integral",
        ),
        delta_electron_integral=_number(
            mapping.get("delta_electron_integral"),
            "delta_electron_integral",
        ),
        density_min=_number(mapping.get("density_min"), "density_min"),
        density_max=_number(mapping.get("density_max"), "density_max"),
        ase_version=_string(mapping.get("ase_version")),
        contract_version=_integer(mapping.get("contract_version")),
    )


def _mapping(value: object, field_name: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ChargeDifferenceAnalysisError(f"{field_name} must be an object")
    return cast(dict[str, object], value)


def _string(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ChargeDifferenceAnalysisError("charge-difference string field is invalid")
    return value


def _integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ChargeDifferenceAnalysisError("charge-difference integer field is invalid")
    return value


def _number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ChargeDifferenceAnalysisError(f"{field_name} must be numeric")
    result = float(value)
    _require_finite(result, field_name)
    return result


def _normalized_sha256(value: str, field_name: str) -> str:
    normalized = value.lower()
    valid = len(normalized) == 64 and all(
        character in "0123456789abcdef" for character in normalized
    )
    if not valid:
        raise ChargeDifferenceAnalysisError(
            f"{field_name} must be a 64-character hexadecimal SHA-256 digest"
        )
    return normalized


def _require_finite(value: float, field_name: str) -> None:
    if not isfinite(value):
        raise ChargeDifferenceAnalysisError(f"{field_name} must be finite")


def _require_positive(value: float, field_name: str) -> None:
    if not isfinite(value) or value <= 0:
        raise ChargeDifferenceAnalysisError(f"{field_name} must be finite and positive")

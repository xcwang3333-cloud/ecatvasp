"""Parameterized electronic band-center descriptors for v0.7 Block 7."""

from __future__ import annotations

import hashlib
import json
import os
from bisect import bisect_left
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from pathlib import Path, PurePosixPath
from typing import cast
from uuid import UUID

from ecatvasp.analysis.dos_materialization import load_canonical_dos_artifact
from ecatvasp.analysis.electronic import (
    CanonicalDosResult,
    DosSeries,
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

BAND_CENTER_TOOL_NAME = "ecatvasp.analysis.band-center"
BAND_CENTER_TOOL_VERSION = "1"
CANONICAL_BAND_CENTER_FORMAT = "ecatvasp-canonical-band-center"
CANONICAL_BAND_CENTER_VERSION = 1


class BandCenterError(ValueError):
    """Raised when an electronic descriptor cannot be derived without guessing."""


class BandCenterKind(StrEnum):
    """Band-center descriptor family."""

    BAND = "band"
    P_BAND = "p_band"
    D_BAND = "d_band"


class BandCenterSpinMode(StrEnum):
    """Explicit spin-selection semantics for one descriptor."""

    TOTAL = "total"
    UP = "up"
    DOWN = "down"
    SUM = "sum"


class BandCenterEnergyReference(StrEnum):
    """Energy frame used for the requested integration window and result."""

    VASP_NATIVE = "vasp_native"
    FERMI_RELATIVE = "fermi_relative"


class BandCenterIntegrationRule(StrEnum):
    """Numerical quadrature contract for descriptor moments."""

    TRAPEZOID_LINEAR_ENDPOINTS = "trapezoid_linear_endpoints"


class BandCenterNormalization(StrEnum):
    """Normalization convention for the reported center."""

    DOS_WEIGHTED_FIRST_MOMENT = "dos_weighted_first_moment"


@dataclass(frozen=True, slots=True)
class BandCenterSelector:
    """System, permanent-atom, or element selector for a DOS descriptor."""

    scope: ProjectionScope
    spin: BandCenterSpinMode
    atom_uid: AtomUid | None = None
    element: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.spin, BandCenterSpinMode):
            raise BandCenterError("unsupported band-center spin mode")
        if self.scope is ProjectionScope.SYSTEM:
            if self.atom_uid is not None or self.element is not None:
                raise BandCenterError("system selector forbids atom_uid and element")
            return
        if self.scope is ProjectionScope.ATOM:
            if self.atom_uid is None or self.element is None or not self.element.strip():
                raise BandCenterError("atom selector requires atom_uid and element")
            return
        if self.scope is ProjectionScope.ELEMENT:
            if self.atom_uid is not None or self.element is None or not self.element.strip():
                raise BandCenterError("element selector requires element and forbids atom_uid")
            return
        raise BandCenterError("unsupported band-center projection scope")


@dataclass(frozen=True, slots=True)
class BandCenterParameters:
    """Complete scientific parameterization of one band-center calculation."""

    kind: BandCenterKind
    selector: BandCenterSelector
    energy_reference: BandCenterEnergyReference
    window_lower_ev: float
    window_upper_ev: float
    integration_rule: BandCenterIntegrationRule = (
        BandCenterIntegrationRule.TRAPEZOID_LINEAR_ENDPOINTS
    )
    normalization: BandCenterNormalization = (
        BandCenterNormalization.DOS_WEIGHTED_FIRST_MOMENT
    )

    def __post_init__(self) -> None:
        if not isinstance(self.kind, BandCenterKind):
            raise BandCenterError("unsupported band-center kind")
        if not isinstance(self.energy_reference, BandCenterEnergyReference):
            raise BandCenterError("unsupported band-center energy reference")
        if not isinstance(self.integration_rule, BandCenterIntegrationRule):
            raise BandCenterError("unsupported band-center integration rule")
        if not isinstance(self.normalization, BandCenterNormalization):
            raise BandCenterError("unsupported band-center normalization")
        if not isfinite(self.window_lower_ev) or not isfinite(self.window_upper_ev):
            raise BandCenterError("band-center integration window must be finite")
        if self.window_upper_ev <= self.window_lower_ev:
            raise BandCenterError("band-center integration window must have positive width")
        if self.kind is not BandCenterKind.BAND and self.selector.scope is ProjectionScope.SYSTEM:
            raise BandCenterError("p/d-band centers require atom- or element-projected DOS")

    @property
    def content_hash(self) -> str:
        """Return deterministic parameter identity independent of an upstream dataset."""

        return canonical_sha256(self)


@dataclass(frozen=True, slots=True)
class BandCenterResult:
    """Auditable first-moment descriptor over an exact canonical DOS artifact."""

    structure_snapshot_id: StructureSnapshotId
    parameters: BandCenterParameters
    center_ev: float
    zeroth_moment_states: float
    first_moment_ev_states: float
    quadrature_point_count: int
    contributing_series_count: int
    source_dos_content_hash: str
    source_artifact_sha256: str
    contract_version: int = CANONICAL_BAND_CENTER_VERSION

    def __post_init__(self) -> None:
        if self.contract_version != CANONICAL_BAND_CENTER_VERSION:
            raise BandCenterError("unsupported canonical band-center contract version")
        for value, field_name in (
            (self.center_ev, "center_ev"),
            (self.zeroth_moment_states, "zeroth_moment_states"),
            (self.first_moment_ev_states, "first_moment_ev_states"),
        ):
            if not isfinite(value):
                raise BandCenterError(f"{field_name} must be finite")
        if self.zeroth_moment_states <= 0.0:
            raise BandCenterError("zeroth DOS moment must be strictly positive")
        if self.quadrature_point_count < 2:
            raise BandCenterError("band-center quadrature requires at least two points")
        if self.contributing_series_count < 1:
            raise BandCenterError("band-center result requires contributing DOS series")
        object.__setattr__(
            self,
            "source_dos_content_hash",
            _normalized_sha256(self.source_dos_content_hash, "source_dos_content_hash"),
        )
        object.__setattr__(
            self,
            "source_artifact_sha256",
            _normalized_sha256(self.source_artifact_sha256, "source_artifact_sha256"),
        )

    @property
    def content_hash(self) -> str:
        """Return deterministic result identity."""

        return canonical_sha256(self)


@dataclass(frozen=True, slots=True)
class DurableBandCenter:
    """Durable Analysis/Artifact/provenance chain for one descriptor."""

    analysis: Analysis
    artifact: Artifact
    result: BandCenterResult
    provenance_records: tuple[ProvenanceRecord, ...]
    dependency_records: tuple[DependencyRecord, ...]


def calculate_band_center(
    *,
    source: CanonicalDosResult,
    source_artifact_sha256: str,
    parameters: BandCenterParameters,
) -> BandCenterResult:
    """Calculate one parameterized band center from immutable canonical DOS facts."""

    artifact_hash = _normalized_sha256(
        source_artifact_sha256,
        "source_artifact_sha256",
    )
    energies = _energy_axis(source, parameters.energy_reference)
    density, contribution_count = _selected_density(source, parameters)
    window_energies, window_density = _clip_window(
        energies=energies,
        values=density,
        lower=parameters.window_lower_ev,
        upper=parameters.window_upper_ev,
    )
    zeroth = _trapezoid(window_energies, window_density)
    first_values = tuple(
        energy * value
        for energy, value in zip(window_energies, window_density, strict=True)
    )
    first = _trapezoid(window_energies, first_values)
    if not isfinite(zeroth) or zeroth <= 0.0:
        raise BandCenterError(
            "selected DOS has a non-positive zeroth moment; "
            "values are not clipped or absolutized"
        )
    if not isfinite(first):
        raise BandCenterError("selected DOS first moment is not finite")
    center = first / zeroth
    if not isfinite(center):
        raise BandCenterError("band center is not finite")
    return BandCenterResult(
        structure_snapshot_id=source.structure_snapshot_id,
        parameters=parameters,
        center_ev=center,
        zeroth_moment_states=zeroth,
        first_moment_ev_states=first,
        quadrature_point_count=len(window_energies),
        contributing_series_count=contribution_count,
        source_dos_content_hash=source.content_hash,
        source_artifact_sha256=artifact_hash,
    )


def materialize_band_center_analysis(
    *,
    project_root: Path | str,
    source_analysis: Analysis,
    source_artifact: Artifact,
    parameters: BandCenterParameters,
) -> DurableBandCenter:
    """Derive and persist one BAND_CENTER Analysis from a durable canonical DOS artifact."""

    _validate_source_contract(
        source_analysis=source_analysis,
        source_artifact=source_artifact,
    )
    root = Path(project_root).resolve()
    if not root.is_dir():
        raise BandCenterError("project_root must be an existing directory")
    source = load_canonical_dos_artifact(
        project_root=root,
        analysis=source_analysis,
        artifact=source_artifact,
    )
    if source_artifact.sha256 is None:
        raise BandCenterError("source canonical DOS Artifact requires SHA-256")
    result = calculate_band_center(
        source=source,
        source_artifact_sha256=source_artifact.sha256,
        parameters=parameters,
    )
    source_receipt = {
        "format": CANONICAL_BAND_CENTER_FORMAT,
        "version": CANONICAL_BAND_CENTER_VERSION,
        "source_analysis_id": source_analysis.id,
        "source_artifact_id": source_artifact.id,
        "source_artifact_sha256": source_artifact.sha256,
        "source_dos_content_hash": source.content_hash,
        "parameters": parameters,
        "parameters_content_hash": parameters.content_hash,
    }
    source_receipt_hash = canonical_sha256(source_receipt)
    analysis = Analysis(
        project_id=source_analysis.project_id,
        analysis_type=AnalysisType.BAND_CENTER,
        input_artifact_ids=(source_artifact.id,),
        status=AnalysisStatus.COMPLETED,
        tool=BAND_CENTER_TOOL_NAME,
        tool_version=BAND_CENTER_TOOL_VERSION,
        parameters_hash=source_receipt_hash,
    )
    payload = {
        "format": CANONICAL_BAND_CENTER_FORMAT,
        "version": CANONICAL_BAND_CENTER_VERSION,
        "analysis_id": analysis.id,
        "source_receipt": source_receipt,
        "source_receipt_hash": source_receipt_hash,
        "result_content_hash": result.content_hash,
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
            tool=BAND_CENTER_TOOL_NAME,
            tool_version=BAND_CENTER_TOOL_VERSION,
            parameters_hash=source_receipt_hash,
        ),
        ProvenanceRecord(
            subject_id=artifact.id,
            tool=BAND_CENTER_TOOL_NAME,
            tool_version=BAND_CENTER_TOOL_VERSION,
            parameters_hash=artifact.sha256,
        ),
    )
    dependency_records = (
        DependencyRecord(
            upstream_id=source_artifact.id,
            downstream_id=analysis.id,
            kind=DependencyKind.SCIENTIFIC,
            role="canonical_dos",
            recorded_hash=scientific_hash(source_artifact),
        ),
        DependencyRecord(
            upstream_id=analysis.id,
            downstream_id=artifact.id,
            kind=DependencyKind.SCIENTIFIC,
            role="canonical_band_center",
            recorded_hash=scientific_hash(analysis),
        ),
    )
    return DurableBandCenter(
        analysis=analysis,
        artifact=artifact,
        result=result,
        provenance_records=provenance_records,
        dependency_records=dependency_records,
    )


def load_band_center_artifact(
    *,
    project_root: Path | str,
    source_analysis: Analysis,
    source_artifact: Artifact,
    analysis: Analysis,
    artifact: Artifact,
) -> BandCenterResult:
    """Reopen a descriptor and verify its exact canonical-DOS scientific parent."""

    _validate_source_contract(
        source_analysis=source_analysis,
        source_artifact=source_artifact,
    )
    if analysis.analysis_type is not AnalysisType.BAND_CENTER:
        raise BandCenterError("band-center artifact requires AnalysisType.BAND_CENTER")
    if analysis.status is not AnalysisStatus.COMPLETED:
        raise BandCenterError("band-center Analysis must be completed")
    if analysis.input_artifact_ids != (source_artifact.id,):
        raise BandCenterError(
            "band-center Analysis input differs from source canonical DOS Artifact"
        )
    if (
        analysis.tool != BAND_CENTER_TOOL_NAME
        or analysis.tool_version != BAND_CENTER_TOOL_VERSION
    ):
        raise BandCenterError("band-center Analysis tool/version is unsupported")
    _validate_output_artifact(analysis=analysis, artifact=artifact)

    root = Path(project_root).resolve()
    source = load_canonical_dos_artifact(
        project_root=root,
        analysis=source_analysis,
        artifact=source_artifact,
    )
    payload = _read_output_payload(root=root, artifact=artifact)
    if payload.get("format") != CANONICAL_BAND_CENTER_FORMAT:
        raise BandCenterError("canonical band-center Artifact format is unsupported")
    if payload.get("version") != CANONICAL_BAND_CENTER_VERSION:
        raise BandCenterError("canonical band-center Artifact version is unsupported")
    if payload.get("analysis_id") != str(analysis.id):
        raise BandCenterError("canonical band-center Artifact belongs to another Analysis")
    receipt = _mapping(payload.get("source_receipt"), "source_receipt")
    receipt_hash = _string(
        payload.get("source_receipt_hash"),
        "source_receipt_hash",
    )
    if receipt_hash != analysis.parameters_hash or canonical_sha256(receipt) != receipt_hash:
        raise BandCenterError("canonical band-center source receipt differs from Analysis")
    if _uuid(receipt.get("source_analysis_id"), "source_analysis_id") != source_analysis.id:
        raise BandCenterError("canonical band-center source Analysis differs")
    if _uuid(receipt.get("source_artifact_id"), "source_artifact_id") != source_artifact.id:
        raise BandCenterError("canonical band-center source Artifact differs")
    if source_artifact.sha256 is None:
        raise BandCenterError("source canonical DOS Artifact requires SHA-256")
    if receipt.get("source_artifact_sha256") != source_artifact.sha256:
        raise BandCenterError("canonical band-center source Artifact hash differs")
    if receipt.get("source_dos_content_hash") != source.content_hash:
        raise BandCenterError("canonical band-center source DOS content hash differs")

    parameters = _decode_parameters(receipt.get("parameters"))
    if receipt.get("parameters_content_hash") != parameters.content_hash:
        raise BandCenterError("canonical band-center parameter hash is inconsistent")
    expected = calculate_band_center(
        source=source,
        source_artifact_sha256=source_artifact.sha256,
        parameters=parameters,
    )
    decoded = _decode_result(payload.get("result"))
    if payload.get("result_content_hash") != decoded.content_hash:
        raise BandCenterError("canonical band-center result hash is inconsistent")
    if decoded != expected:
        raise BandCenterError("canonical band-center result differs from recomputed source facts")
    return decoded


def _selected_density(
    source: CanonicalDosResult,
    parameters: BandCenterParameters,
) -> tuple[tuple[float, ...], int]:
    system = tuple(
        item for item in source.series if item.scope is ProjectionScope.SYSTEM
    )
    available_spins = frozenset(item.spin for item in system)
    selected_spins = _resolve_spins(parameters.selector.spin, available_spins)

    if parameters.selector.scope is ProjectionScope.SYSTEM:
        if parameters.kind is not BandCenterKind.BAND:
            raise BandCenterError(
                "system DOS does not provide orbital-resolved p/d projections"
            )
        selected = tuple(item for item in system if item.spin in selected_spins)
        return _sum_series(
            selected,
            expected_length=len(source.energy_axis.energies_ev),
        )

    projected = tuple(
        item for item in source.series if item.scope is ProjectionScope.ATOM
    )
    if parameters.selector.scope is ProjectionScope.ATOM:
        projected = tuple(
            item
            for item in projected
            if item.atom_uid == parameters.selector.atom_uid
            and item.element == parameters.selector.element
        )
    else:
        projected = tuple(
            item for item in projected if item.element == parameters.selector.element
        )
    projected = tuple(item for item in projected if item.spin in selected_spins)
    if not projected:
        raise BandCenterError("band-center selector matches no atom-projected DOS series")

    target_l = _target_angular_momentum(parameters.kind)
    if target_l is not None:
        projected = tuple(
            item
            for item in projected
            if item.orbital is not None and item.orbital.angular_momentum == target_l
        )
        if not projected:
            raise BandCenterError("requested p/d projection is absent from canonical DOS")
    elif any(item.orbital is None for item in projected) and any(
        item.orbital is not None for item in projected
    ):
        raise BandCenterError(
            "atom-total and orbital-resolved projections coexist; "
            "generic band aggregation is ambiguous"
        )
    return _sum_series(
        projected,
        expected_length=len(source.energy_axis.energies_ev),
    )


def _resolve_spins(
    mode: BandCenterSpinMode,
    available: frozenset[SpinChannel],
) -> frozenset[SpinChannel]:
    if available == frozenset({SpinChannel.TOTAL}):
        if mode is not BandCenterSpinMode.TOTAL:
            raise BandCenterError(
                "unpolarized DOS supports only explicit TOTAL spin selection"
            )
        return frozenset({SpinChannel.TOTAL})
    if available == frozenset({SpinChannel.UP, SpinChannel.DOWN}):
        if mode is BandCenterSpinMode.TOTAL:
            raise BandCenterError(
                "spin-polarized DOS has no TOTAL channel; use UP, DOWN, or SUM"
            )
        if mode is BandCenterSpinMode.UP:
            return frozenset({SpinChannel.UP})
        if mode is BandCenterSpinMode.DOWN:
            return frozenset({SpinChannel.DOWN})
        if mode is BandCenterSpinMode.SUM:
            return frozenset({SpinChannel.UP, SpinChannel.DOWN})
    raise BandCenterError("canonical DOS spin schema is unsupported")


def _target_angular_momentum(kind: BandCenterKind) -> int | None:
    if kind is BandCenterKind.P_BAND:
        return 1
    if kind is BandCenterKind.D_BAND:
        return 2
    return None


def _sum_series(
    series: tuple[DosSeries, ...],
    *,
    expected_length: int,
) -> tuple[tuple[float, ...], int]:
    if not series:
        raise BandCenterError("band-center selector matches no DOS series")
    values = [0.0] * expected_length
    for item in series:
        if len(item.values) != expected_length:
            raise BandCenterError(
                "selected DOS series does not use the canonical energy grid"
            )
        for index, value in enumerate(item.values):
            values[index] += value
    if not all(isfinite(value) for value in values):
        raise BandCenterError("aggregated DOS contains non-finite values")
    return tuple(values), len(series)


def _energy_axis(
    source: CanonicalDosResult,
    reference: BandCenterEnergyReference,
) -> tuple[float, ...]:
    if reference is BandCenterEnergyReference.VASP_NATIVE:
        return source.energy_axis.energies_ev
    if reference is BandCenterEnergyReference.FERMI_RELATIVE:
        return source.energy_axis.relative_to_fermi()
    raise BandCenterError("unsupported descriptor energy reference")


def _clip_window(
    *,
    energies: tuple[float, ...],
    values: tuple[float, ...],
    lower: float,
    upper: float,
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    if len(energies) != len(values):
        raise BandCenterError("DOS energy/value lengths differ")
    if lower < energies[0] or upper > energies[-1]:
        raise BandCenterError(
            "band-center integration window must lie inside the DOS energy range"
        )
    interior = tuple(value for value in energies if lower < value < upper)
    points = (lower, *interior, upper)
    densities = tuple(
        _linear_value(energies, values, point)
        for point in points
    )
    return points, densities


def _linear_value(
    energies: tuple[float, ...],
    values: tuple[float, ...],
    target: float,
) -> float:
    index = bisect_left(energies, target)
    if index < len(energies) and energies[index] == target:
        return values[index]
    if index == 0 or index == len(energies):
        raise BandCenterError(
            "descriptor endpoint interpolation would require extrapolation"
        )
    left_x = energies[index - 1]
    right_x = energies[index]
    fraction = (target - left_x) / (right_x - left_x)
    return values[index - 1] + fraction * (values[index] - values[index - 1])


def _trapezoid(
    energies: tuple[float, ...],
    values: tuple[float, ...],
) -> float:
    total = 0.0
    for index in range(len(energies) - 1):
        width = energies[index + 1] - energies[index]
        total += 0.5 * width * (values[index] + values[index + 1])
    return total


def _validate_source_contract(
    *,
    source_analysis: Analysis,
    source_artifact: Artifact,
) -> None:
    if source_analysis.analysis_type is not AnalysisType.DOS:
        raise BandCenterError(
            "electronic descriptor requires canonical AnalysisType.DOS source"
        )
    if source_analysis.status is not AnalysisStatus.COMPLETED:
        raise BandCenterError("source canonical DOS Analysis must be completed")
    if (
        not isinstance(source_artifact.producer, AnalysisProducerRef)
        or source_artifact.producer.id != source_analysis.id
    ):
        raise BandCenterError(
            "source canonical DOS Artifact producer differs from source Analysis"
        )
    if source_artifact.artifact_type is not ArtifactType.DERIVED_DATASET:
        raise BandCenterError("source canonical DOS Artifact must be DERIVED_DATASET")
    if source_artifact.sha256 is None:
        raise BandCenterError("source canonical DOS Artifact requires SHA-256")


def _write_result_artifact(
    *,
    root: Path,
    analysis: Analysis,
    payload: object,
) -> Artifact:
    relative = Path("analyses") / str(analysis.id) / "canonical-band-center.json"
    absolute = (root / relative).resolve()
    if not absolute.is_relative_to(root):
        raise BandCenterError("canonical band-center path resolves outside project_root")
    text = canonical_json(payload) + "\n"
    absolute.parent.mkdir(parents=True, exist_ok=True)
    if absolute.exists():
        if not absolute.is_file():
            raise BandCenterError("canonical band-center path is not a regular file")
        if absolute.read_text(encoding="utf-8") != text:
            raise BandCenterError(
                "canonical band-center path already has different content"
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


def _validate_output_artifact(
    *,
    analysis: Analysis,
    artifact: Artifact,
) -> None:
    if (
        not isinstance(artifact.producer, AnalysisProducerRef)
        or artifact.producer.id != analysis.id
    ):
        raise BandCenterError(
            "canonical band-center Artifact producer differs from Analysis"
        )
    if artifact.artifact_type is not ArtifactType.DERIVED_DATASET:
        raise BandCenterError("canonical band-center Artifact must be DERIVED_DATASET")
    if artifact.availability not in {
        ArtifactAvailability.LOCAL,
        ArtifactAvailability.BOTH,
    }:
        raise BandCenterError(
            "canonical band-center Artifact must be locally available"
        )
    if (
        artifact.local_path is None
        or PurePosixPath(artifact.local_path).name != "canonical-band-center.json"
    ):
        raise BandCenterError(
            "canonical band-center Artifact has unexpected local filename"
        )
    if artifact.sha256 is None or artifact.size_bytes is None:
        raise BandCenterError(
            "canonical band-center Artifact requires hash and byte size"
        )


def _read_output_payload(
    *,
    root: Path,
    artifact: Artifact,
) -> dict[str, object]:
    if artifact.local_path is None:
        raise BandCenterError("canonical band-center Artifact requires local_path")
    relative = PurePosixPath(artifact.local_path)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or artifact.local_path != relative.as_posix()
    ):
        raise BandCenterError(
            "canonical band-center local_path must be normalized and relative"
        )
    path = (root / Path(*relative.parts)).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise BandCenterError("canonical band-center local file is missing")
    try:
        body = path.read_bytes()
    except OSError as error:
        raise BandCenterError(
            "canonical band-center Artifact cannot be read"
        ) from error
    if len(body) != artifact.size_bytes:
        raise BandCenterError("canonical band-center Artifact byte size changed")
    if hashlib.sha256(body).hexdigest() != artifact.sha256:
        raise BandCenterError("canonical band-center Artifact content hash changed")
    try:
        raw = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BandCenterError(
            "canonical band-center Artifact is not valid UTF-8 JSON"
        ) from error
    return _mapping(raw, "canonical band-center Artifact")


def _decode_result(raw: object) -> BandCenterResult:
    mapping = _mapping(raw, "band-center result")
    try:
        snapshot_id = StructureSnapshotId(
            UUID(
                _string(
                    mapping.get("structure_snapshot_id"),
                    "structure_snapshot_id",
                )
            )
        )
        return BandCenterResult(
            structure_snapshot_id=snapshot_id,
            parameters=_decode_parameters(mapping.get("parameters")),
            center_ev=_number(mapping.get("center_ev"), "center_ev"),
            zeroth_moment_states=_number(
                mapping.get("zeroth_moment_states"),
                "zeroth_moment_states",
            ),
            first_moment_ev_states=_number(
                mapping.get("first_moment_ev_states"),
                "first_moment_ev_states",
            ),
            quadrature_point_count=_integer(
                mapping.get("quadrature_point_count"),
                "quadrature_point_count",
            ),
            contributing_series_count=_integer(
                mapping.get("contributing_series_count"),
                "contributing_series_count",
            ),
            source_dos_content_hash=_string(
                mapping.get("source_dos_content_hash"),
                "source_dos_content_hash",
            ),
            source_artifact_sha256=_string(
                mapping.get("source_artifact_sha256"),
                "source_artifact_sha256",
            ),
            contract_version=_integer(
                mapping.get("contract_version"),
                "contract_version",
            ),
        )
    except ValueError as error:
        if isinstance(error, BandCenterError):
            raise
        raise BandCenterError(
            "canonical band-center result contains invalid fields"
        ) from error


def _decode_parameters(raw: object) -> BandCenterParameters:
    mapping = _mapping(raw, "band-center parameters")
    selector_raw = _mapping(mapping.get("selector"), "band-center selector")
    try:
        raw_uid = selector_raw.get("atom_uid")
        atom_uid = None
        if raw_uid is not None:
            atom_uid = AtomUid(UUID(_string(raw_uid, "atom_uid")))
        raw_element = selector_raw.get("element")
        element = None
        if raw_element is not None:
            element = _string(raw_element, "element")
        selector = BandCenterSelector(
            scope=ProjectionScope(_string(selector_raw.get("scope"), "scope")),
            spin=BandCenterSpinMode(_string(selector_raw.get("spin"), "spin")),
            atom_uid=atom_uid,
            element=element,
        )
        return BandCenterParameters(
            kind=BandCenterKind(_string(mapping.get("kind"), "kind")),
            selector=selector,
            energy_reference=BandCenterEnergyReference(
                _string(mapping.get("energy_reference"), "energy_reference")
            ),
            window_lower_ev=_number(
                mapping.get("window_lower_ev"),
                "window_lower_ev",
            ),
            window_upper_ev=_number(
                mapping.get("window_upper_ev"),
                "window_upper_ev",
            ),
            integration_rule=BandCenterIntegrationRule(
                _string(mapping.get("integration_rule"), "integration_rule")
            ),
            normalization=BandCenterNormalization(
                _string(mapping.get("normalization"), "normalization")
            ),
        )
    except ValueError as error:
        if isinstance(error, BandCenterError):
            raise
        raise BandCenterError(
            "band-center parameters contain invalid UUID or enum values"
        ) from error


def _mapping(value: object, field_name: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(
        not isinstance(key, str) for key in value
    ):
        raise BandCenterError(f"{field_name} must be an object")
    return cast(dict[str, object], value)


def _string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BandCenterError(f"{field_name} must be a non-empty string")
    return value


def _number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BandCenterError(f"{field_name} must be numeric")
    result = float(value)
    if not isfinite(result):
        raise BandCenterError(f"{field_name} must be finite")
    return result


def _integer(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise BandCenterError(f"{field_name} must be an integer")
    return value


def _uuid(value: object, field_name: str) -> UUID:
    try:
        return UUID(_string(value, field_name))
    except ValueError as error:
        raise BandCenterError(f"{field_name} must be a UUID") from error


def _normalized_sha256(value: str, field_name: str) -> str:
    normalized = value.lower()
    invalid_hex = any(
        character not in "0123456789abcdef"
        for character in normalized
    )
    if len(normalized) != 64 or invalid_hex:
        raise BandCenterError(
            f"{field_name} must be a 64-character hexadecimal SHA-256 digest"
        )
    return normalized

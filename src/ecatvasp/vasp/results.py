"""Parser-neutral scientific result contracts for VASP outputs.

v0.5 Block 1 defines immutable value semantics only. This module performs no
file I/O, VASP parsing, convergence classification, Calculation status mutation,
or relaxed-structure promotion.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from math import isfinite

from ecatvasp.domain import Analysis, AnalysisType, ArtifactId, ArtifactType, CalculationType

VASP_RESULT_DOCUMENT_FORMAT = "ecatvasp-vasp-scientific-result"
VASP_RESULT_DOCUMENT_VERSION = 1


class VaspResultContractError(ValueError):
    """Raised when scientific-result contract data is ambiguous or inconsistent."""


class VaspResultSourceRole(StrEnum):
    """Stable semantic roles for raw VASP artifacts consumed by result parsing."""

    OUTCAR = "outcar"
    OSZICAR = "oszicar"
    CONTCAR = "contcar"
    VASPRUN_XML = "vasprun_xml"


class ConvergenceVerdict(StrEnum):
    """Scientific verdict vocabulary independent from Calculation lifecycle state."""

    CONVERGED = "converged"
    UNCONVERGED = "unconverged"
    INDETERMINATE = "indeterminate"
    NOT_APPLICABLE = "not_applicable"


_SOURCE_ARTIFACT_TYPES: dict[VaspResultSourceRole, ArtifactType] = {
    VaspResultSourceRole.OUTCAR: ArtifactType.OUTCAR,
    VaspResultSourceRole.OSZICAR: ArtifactType.OSZICAR,
    VaspResultSourceRole.CONTCAR: ArtifactType.CONTCAR,
    VaspResultSourceRole.VASPRUN_XML: ArtifactType.VASPRUN_XML,
}


def result_source_artifact_type(role: VaspResultSourceRole) -> ArtifactType:
    """Return the only ArtifactType valid for one scientific result source role."""

    return _SOURCE_ARTIFACT_TYPES[role]


def _normalized_sha256(value: str, field_name: str) -> str:
    normalized = value.lower()
    valid_hex = all(character in "0123456789abcdef" for character in normalized)
    if len(normalized) != 64 or not valid_hex:
        raise VaspResultContractError(
            f"{field_name} must be a 64-character hexadecimal SHA-256 digest"
        )
    return normalized


def _validate_optional_finite(value: float | None, field_name: str) -> None:
    if value is not None and not isfinite(value):
        raise VaspResultContractError(f"{field_name} must be finite when present")


def _validate_optional_nonnegative(value: int | None, field_name: str) -> None:
    if value is not None and value < 0:
        raise VaspResultContractError(f"{field_name} must not be negative")


def _validate_codes(values: tuple[str, ...], field_name: str) -> None:
    if any(not value.strip() for value in values):
        raise VaspResultContractError(f"{field_name} must not contain blank values")
    if len(values) != len(set(values)):
        raise VaspResultContractError(f"{field_name} must contain unique values")


@dataclass(frozen=True, slots=True)
class VaspResultSource:
    """Exact content-addressed raw Artifact consumed by a scientific result parse."""

    role: VaspResultSourceRole
    artifact_id: ArtifactId
    artifact_type: ArtifactType
    sha256: str

    def __post_init__(self) -> None:
        expected_type = result_source_artifact_type(self.role)
        if self.artifact_type is not expected_type:
            raise VaspResultContractError(
                f"{self.role.value} source requires ArtifactType.{expected_type.name}"
            )
        object.__setattr__(self, "sha256", _normalized_sha256(self.sha256, "sha256"))


@dataclass(frozen=True, slots=True)
class VaspEnergySummary:
    """Explicit VASP energy semantics; no field is an implicit generic total energy."""

    free_energy_toten_ev: float | None = None
    energy_without_entropy_ev: float | None = None
    energy_sigma0_ev: float | None = None
    fermi_energy_ev: float | None = None

    def __post_init__(self) -> None:
        _validate_optional_finite(self.free_energy_toten_ev, "free_energy_toten_ev")
        _validate_optional_finite(
            self.energy_without_entropy_ev,
            "energy_without_entropy_ev",
        )
        _validate_optional_finite(self.energy_sigma0_ev, "energy_sigma0_ev")
        _validate_optional_finite(self.fermi_energy_ev, "fermi_energy_ev")


@dataclass(frozen=True, slots=True)
class VaspResultDocument:
    """Normalized parser output containing facts but no convergence verdict."""

    calculation_type: CalculationType
    sources: tuple[VaspResultSource, ...]
    energies: VaspEnergySummary = field(default_factory=VaspEnergySummary)
    vasp_version: str | None = None
    ionic_steps: int | None = None
    electronic_steps: int | None = None
    termination_observed: bool | None = None
    evidence_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.sources:
            raise VaspResultContractError("VaspResultDocument requires at least one raw source")
        roles = tuple(source.role for source in self.sources)
        if len(roles) != len(set(roles)):
            raise VaspResultContractError("VASP result source roles must be unique")
        artifact_ids = tuple(source.artifact_id for source in self.sources)
        if len(artifact_ids) != len(set(artifact_ids)):
            raise VaspResultContractError("VASP result source Artifact ids must be unique")
        if VaspResultSourceRole.OUTCAR not in roles:
            raise VaspResultContractError("VaspResultDocument requires an OUTCAR source")
        if self.vasp_version is not None and not self.vasp_version.strip():
            raise VaspResultContractError("vasp_version must not be blank when present")
        _validate_optional_nonnegative(self.ionic_steps, "ionic_steps")
        _validate_optional_nonnegative(self.electronic_steps, "electronic_steps")
        _validate_codes(self.evidence_codes, "evidence_codes")


@dataclass(frozen=True, slots=True)
class VaspConvergenceAssessment:
    """Recipe-aware scientific verdict kept separate from parsed VASP facts."""

    calculation_type: CalculationType
    electronic: ConvergenceVerdict
    ionic: ConvergenceVerdict
    overall: ConvergenceVerdict
    evidence_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_codes(self.evidence_codes, "evidence_codes")


def validate_result_parse_analysis(analysis: Analysis) -> None:
    """Validate the durable Analysis identity used for one result parse operation."""

    if analysis.analysis_type is not AnalysisType.RESULT_PARSE:
        raise VaspResultContractError("result parsing requires AnalysisType.RESULT_PARSE")
    if not analysis.input_artifact_ids:
        raise VaspResultContractError("result parsing requires at least one input Artifact")
    if analysis.tool is None or analysis.tool_version is None:
        raise VaspResultContractError(
            "result parsing Analysis requires explicit tool and tool_version provenance"
        )

"""Parser-neutral scientific result contracts for VASP outputs.

The contracts carry explicit scientific semantics only. File parsing, convergence
classification, lifecycle mutation, and relaxed-structure promotion remain in
separate adapters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from math import isfinite, sqrt

from ecatvasp.domain import (
    Analysis,
    AnalysisType,
    ArtifactId,
    ArtifactType,
    CalculationType,
)
from ecatvasp.domain.ids import AtomUid

VASP_RESULT_DOCUMENT_FORMAT = "ecatvasp-vasp-scientific-result"
VASP_RESULT_DOCUMENT_VERSION = 3

Vector3 = tuple[float, float, float]


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


class VaspFrequencyModeKind(StrEnum):
    """Whether a VASP normal mode is real or explicitly labelled imaginary."""

    REAL = "real"
    IMAGINARY = "imaginary"


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


def _validate_vector(value: Vector3, field_name: str) -> None:
    if len(value) != 3 or any(not isfinite(component) for component in value):
        raise VaspResultContractError(
            f"{field_name} must contain exactly three finite components"
        )


def _validate_nonnegative_finite(value: float, field_name: str) -> None:
    if not isfinite(value) or value < 0:
        raise VaspResultContractError(f"{field_name} must be finite and non-negative")


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
class VaspSiteForce:
    """Final Cartesian force vector bound to permanent atom identity."""

    atom_uid: AtomUid
    vector_ev_per_angstrom: Vector3

    def __post_init__(self) -> None:
        _validate_vector(self.vector_ev_per_angstrom, "vector_ev_per_angstrom")

    @property
    def norm_ev_per_angstrom(self) -> float:
        """Return Euclidean force magnitude in eV/angstrom."""

        x, y, z = self.vector_ev_per_angstrom
        return sqrt(x * x + y * y + z * z)


@dataclass(frozen=True, slots=True)
class VaspForceDataset:
    """Final VASP force block in exact POSCAR/VASP atom order."""

    site_forces: tuple[VaspSiteForce, ...]
    max_force_ev_per_angstrom: float = field(init=False)

    def __post_init__(self) -> None:
        if not self.site_forces:
            raise VaspResultContractError("force dataset requires at least one site")
        _validate_unique_atom_uids(self.site_forces, "site_forces")
        object.__setattr__(
            self,
            "max_force_ev_per_angstrom",
            max(item.norm_ev_per_angstrom for item in self.site_forces),
        )


@dataclass(frozen=True, slots=True)
class VaspSiteScalarMagnetization:
    """One site-projected collinear spin moment in Bohr magnetons."""

    atom_uid: AtomUid
    projected_moment_mu_b: float

    def __post_init__(self) -> None:
        _validate_optional_finite(self.projected_moment_mu_b, "projected_moment_mu_b")


@dataclass(frozen=True, slots=True)
class VaspSiteVectorMagnetization:
    """One site-projected noncollinear spin-moment vector in the VASP spinor basis."""

    atom_uid: AtomUid
    projected_moment_mu_b: Vector3

    def __post_init__(self) -> None:
        _validate_vector(self.projected_moment_mu_b, "projected_moment_mu_b")


_UidBoundResult = VaspSiteForce | VaspSiteScalarMagnetization | VaspSiteVectorMagnetization


def _validate_unique_atom_uids(
    values: tuple[_UidBoundResult, ...],
    field_name: str,
) -> None:
    atom_uids = tuple(value.atom_uid for value in values)
    if len(atom_uids) != len(set(atom_uids)):
        raise VaspResultContractError(f"{field_name} must reference unique atom_uids")


@dataclass(frozen=True, slots=True)
class VaspCollinearMagnetization:
    """Collinear cell-integrated and site-projected spin magnetization."""

    site_moments: tuple[VaspSiteScalarMagnetization, ...] = ()
    projected_total_mu_b: float | None = None
    cell_total_mu_b: float | None = None

    def __post_init__(self) -> None:
        _validate_unique_atom_uids(self.site_moments, "site_moments")
        _validate_optional_finite(self.projected_total_mu_b, "projected_total_mu_b")
        _validate_optional_finite(self.cell_total_mu_b, "cell_total_mu_b")
        if self.site_moments and self.projected_total_mu_b is None:
            raise VaspResultContractError(
                "site-projected collinear moments require projected_total_mu_b"
            )
        if not self.site_moments and self.projected_total_mu_b is not None:
            raise VaspResultContractError(
                "projected_total_mu_b requires site-projected collinear moments"
            )
        if not self.site_moments and self.cell_total_mu_b is None:
            raise VaspResultContractError(
                "collinear magnetization requires cell or site-projected evidence"
            )


@dataclass(frozen=True, slots=True)
class VaspNoncollinearMagnetization:
    """Noncollinear spin-moment vectors in the VASP spinor basis."""

    site_moments: tuple[VaspSiteVectorMagnetization, ...] = ()
    projected_total_mu_b: Vector3 | None = None
    cell_total_mu_b: Vector3 | None = None

    def __post_init__(self) -> None:
        _validate_unique_atom_uids(self.site_moments, "site_moments")
        if self.projected_total_mu_b is not None:
            _validate_vector(self.projected_total_mu_b, "projected_total_mu_b")
        if self.cell_total_mu_b is not None:
            _validate_vector(self.cell_total_mu_b, "cell_total_mu_b")
        if self.site_moments and self.projected_total_mu_b is None:
            raise VaspResultContractError(
                "site-projected noncollinear moments require projected_total_mu_b"
            )
        if not self.site_moments and self.projected_total_mu_b is not None:
            raise VaspResultContractError(
                "projected_total_mu_b requires site-projected noncollinear moments"
            )
        if not self.site_moments and self.cell_total_mu_b is None:
            raise VaspResultContractError(
                "noncollinear magnetization requires cell or site-projected evidence"
            )


VaspMagnetization = VaspCollinearMagnetization | VaspNoncollinearMagnetization


@dataclass(frozen=True, slots=True)
class VaspFrequencyEigenvector:
    """One standard dynamical-matrix eigenvector component set bound to atom_uid."""

    atom_uid: AtomUid
    components: Vector3

    def __post_init__(self) -> None:
        _validate_vector(self.components, "components")


@dataclass(frozen=True, slots=True)
class VaspFrequencyMode:
    """One VASP normal mode with explicit real/imaginary frequency semantics."""

    mode_index: int
    kind: VaspFrequencyModeKind
    frequency_thz: float
    angular_frequency_2pi_thz: float
    wavenumber_cm_inverse: float
    energy_mev: float
    eigenvectors: tuple[VaspFrequencyEigenvector, ...]

    def __post_init__(self) -> None:
        if self.mode_index < 1:
            raise VaspResultContractError("frequency mode_index must be positive")
        _validate_nonnegative_finite(self.frequency_thz, "frequency_thz")
        _validate_nonnegative_finite(
            self.angular_frequency_2pi_thz,
            "angular_frequency_2pi_thz",
        )
        _validate_nonnegative_finite(self.wavenumber_cm_inverse, "wavenumber_cm_inverse")
        _validate_nonnegative_finite(self.energy_mev, "energy_mev")
        if not self.eigenvectors:
            raise VaspResultContractError("frequency mode requires eigenvectors")
        atom_uids = tuple(item.atom_uid for item in self.eigenvectors)
        if len(atom_uids) != len(set(atom_uids)):
            raise VaspResultContractError("frequency mode eigenvectors require unique atom_uids")


@dataclass(frozen=True, slots=True)
class VaspFrequencyDataset:
    """Complete Γ-point finite-difference mode set for one exact frequency recipe."""

    atom_uids: tuple[AtomUid, ...]
    displaced_atom_uids: tuple[AtomUid, ...]
    modes: tuple[VaspFrequencyMode, ...]
    imaginary_mode_count: int = field(init=False)

    def __post_init__(self) -> None:
        if not self.atom_uids:
            raise VaspResultContractError("frequency dataset requires at least one atom_uid")
        if len(self.atom_uids) != len(set(self.atom_uids)):
            raise VaspResultContractError("frequency dataset atom_uids must be unique")
        if not self.displaced_atom_uids:
            raise VaspResultContractError("frequency dataset requires displaced_atom_uids")
        if len(self.displaced_atom_uids) != len(set(self.displaced_atom_uids)):
            raise VaspResultContractError("displaced_atom_uids must be unique")
        if any(atom_uid not in self.atom_uids for atom_uid in self.displaced_atom_uids):
            raise VaspResultContractError("displaced_atom_uids must be a subset of atom_uids")
        expected_modes = 3 * len(self.displaced_atom_uids)
        if len(self.modes) != expected_modes:
            raise VaspResultContractError(
                "frequency mode count must equal three times the displaced atom count"
            )
        indices = tuple(mode.mode_index for mode in self.modes)
        if indices != tuple(range(1, expected_modes + 1)):
            raise VaspResultContractError("frequency mode indices must be contiguous from 1")
        for mode in self.modes:
            mode_uids = tuple(item.atom_uid for item in mode.eigenvectors)
            if mode_uids != self.atom_uids:
                raise VaspResultContractError(
                    "frequency mode eigenvectors must follow exact VASP atom_uid order"
                )
        object.__setattr__(
            self,
            "imaginary_mode_count",
            sum(mode.kind is VaspFrequencyModeKind.IMAGINARY for mode in self.modes),
        )

    @property
    def degrees_of_freedom(self) -> int:
        """Return the exact finite-difference degrees of freedom represented by the modes."""

        return 3 * len(self.displaced_atom_uids)


@dataclass(frozen=True, slots=True)
class VaspResultDocument:
    """Normalized parser output containing facts but no convergence verdict."""

    calculation_type: CalculationType
    sources: tuple[VaspResultSource, ...]
    energies: VaspEnergySummary = field(default_factory=VaspEnergySummary)
    forces: VaspForceDataset | None = None
    magnetization: VaspMagnetization | None = None
    frequencies: VaspFrequencyDataset | None = None
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
        if self.frequencies is not None and self.calculation_type not in {
            CalculationType.FREQUENCY,
            CalculationType.GAS_FREQUENCY,
        }:
            raise VaspResultContractError(
                "frequency data requires a frequency CalculationType"
            )
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

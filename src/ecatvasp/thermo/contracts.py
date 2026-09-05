"""Scientific thermochemistry contracts for v0.8.

This module defines immutable identity and result value objects only. It does not parse
VASP outputs, choose vibrational modes, apply empirical corrections, or persist a second
workflow state machine. All policy choices that can change a thermochemical result are
explicit members of the scientific identity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from math import isfinite

from ecatvasp.domain import AtomUid, canonical_sha256

ONE_BAR_PA = 100_000.0
ONE_ATM_PA = 101_325.0


class ThermochemistryContractError(ValueError):
    """Raised when thermochemistry semantics are incomplete or internally inconsistent."""


class ThermochemistrySubjectKind(StrEnum):
    """Physical subject whose thermochemical correction is being constructed."""

    SURFACE = "surface"
    ADSORBATE = "adsorbate"
    GAS = "gas"


class ThermochemicalStandardState(StrEnum):
    """Reference-state convention kept separate from the actual evaluation pressure."""

    SURFACE_FIXED_CELL = "surface_fixed_cell"
    IDEAL_GAS_1_BAR = "ideal_gas_1_bar"
    IDEAL_GAS_1_ATM = "ideal_gas_1_atm"


class ElectronicEnergyKind(StrEnum):
    """Explicit VASP electronic-energy semantic consumed by thermochemistry."""

    SIGMA_ZERO = "energy_sigma0_ev"
    WITHOUT_ENTROPY = "energy_without_entropy_ev"
    TOTEN = "free_energy_toten_ev"


class ImaginaryModePolicy(StrEnum):
    """How explicitly imaginary VASP modes are handled by a later thermochemistry adapter."""

    REJECT_ANY = "reject_any"
    EXCLUDE_EXPLICIT = "exclude_explicit"


class LowFrequencyPolicy(StrEnum):
    """How real modes below an explicit cutoff are handled."""

    REJECT_BELOW_CUTOFF = "reject_below_cutoff"
    EXCLUDE_EXPLICIT = "exclude_explicit"


class ModeExclusionReason(StrEnum):
    """Auditable reason for excluding one raw VASP mode from harmonic thermochemistry."""

    IMAGINARY = "imaginary"
    LOW_FREQUENCY = "low_frequency"
    CONSTRAINED = "constrained"
    TRANSLATIONAL = "translational"
    ROTATIONAL = "rotational"


class GasGeometryKind(StrEnum):
    """Rigid-rotor geometry class used by ideal-gas rotational thermochemistry."""

    MONATOMIC = "monatomic"
    LINEAR = "linear"
    NONLINEAR = "nonlinear"


class ElectronicEntropyPolicy(StrEnum):
    """Electronic entropy treatment; VASP smearing entropy is not silently reused here."""

    NEGLECTED = "neglected"
    SPIN_DEGENERACY = "spin_degeneracy"


class ThermochemistryCorrectionKind(StrEnum):
    """Typed additive correction family; every term remains visible in the result."""

    DFT_REFERENCE = "dft_reference"
    EXPERIMENTAL_REFERENCE = "experimental_reference"
    PHASE_CHANGE = "phase_change"
    SOLVATION = "solvation"
    USER_DECLARED = "user_declared"


def _require_finite(value: float, field_name: str) -> None:
    if not isfinite(value):
        raise ThermochemistryContractError(f"{field_name} must be finite")


def _require_nonnegative(value: float, field_name: str) -> None:
    _require_finite(value, field_name)
    if value < 0:
        raise ThermochemistryContractError(f"{field_name} must be non-negative")


def _require_positive(value: float, field_name: str) -> None:
    _require_finite(value, field_name)
    if value <= 0:
        raise ThermochemistryContractError(f"{field_name} must be positive")


def _require_text(value: str, field_name: str) -> None:
    if not value.strip():
        raise ThermochemistryContractError(f"{field_name} must not be blank")


@dataclass(frozen=True, slots=True)
class ModeExclusion:
    """Explicit mode-level policy decision; mode indices are raw VASP one-based indices."""

    mode_index: int
    reason: ModeExclusionReason
    note: str | None = None

    def __post_init__(self) -> None:
        if self.mode_index < 1:
            raise ThermochemistryContractError("mode_index must be positive")
        if self.note is not None:
            _require_text(self.note, "note")


@dataclass(frozen=True, slots=True)
class VibrationalModePolicy:
    """All method choices that turn raw VASP modes into accepted harmonic modes."""

    frequency_cutoff_cm_inverse: float
    imaginary_mode_policy: ImaginaryModePolicy
    low_frequency_policy: LowFrequencyPolicy
    exclusions: tuple[ModeExclusion, ...] = ()

    def __post_init__(self) -> None:
        _require_positive(self.frequency_cutoff_cm_inverse, "frequency_cutoff_cm_inverse")
        ordered = tuple(
            sorted(
                self.exclusions,
                key=lambda item: (item.mode_index, item.reason.value),
            )
        )
        indices = tuple(item.mode_index for item in ordered)
        if len(indices) != len(set(indices)):
            raise ThermochemistryContractError("a vibrational mode may be excluded only once")
        if (
            self.imaginary_mode_policy is ImaginaryModePolicy.REJECT_ANY
            and any(item.reason is ModeExclusionReason.IMAGINARY for item in ordered)
        ):
            raise ThermochemistryContractError(
                "REJECT_ANY imaginary policy cannot carry imaginary-mode exclusions"
            )
        if (
            self.low_frequency_policy is LowFrequencyPolicy.REJECT_BELOW_CUTOFF
            and any(item.reason is ModeExclusionReason.LOW_FREQUENCY for item in ordered)
        ):
            raise ThermochemistryContractError(
                "REJECT_BELOW_CUTOFF cannot carry low-frequency exclusions"
            )
        object.__setattr__(self, "exclusions", ordered)


@dataclass(frozen=True, slots=True)
class ThermochemicalConditions:
    """Temperature, actual pressure, and explicit standard-state convention."""

    temperature_k: float
    standard_state: ThermochemicalStandardState
    pressure_pa: float | None = None

    def __post_init__(self) -> None:
        _require_positive(self.temperature_k, "temperature_k")
        gas_state = self.standard_state in {
            ThermochemicalStandardState.IDEAL_GAS_1_BAR,
            ThermochemicalStandardState.IDEAL_GAS_1_ATM,
        }
        if gas_state:
            if self.pressure_pa is None:
                raise ThermochemistryContractError(
                    "ideal-gas thermochemistry requires an explicit evaluation pressure"
                )
            _require_positive(self.pressure_pa, "pressure_pa")
        elif self.pressure_pa is not None:
            raise ThermochemistryContractError(
                "surface fixed-cell thermochemistry must not carry a gas pressure"
            )

    @property
    def standard_pressure_pa(self) -> float | None:
        """Return the pressure represented by the selected standard-state convention."""

        if self.standard_state is ThermochemicalStandardState.IDEAL_GAS_1_BAR:
            return ONE_BAR_PA
        if self.standard_state is ThermochemicalStandardState.IDEAL_GAS_1_ATM:
            return ONE_ATM_PA
        return None


@dataclass(frozen=True, slots=True)
class GasAtomicMass:
    """Exact atom-UID-bound mass used for gas translation and rotation."""

    atom_uid: AtomUid
    mass_amu: float
    isotopologue_label: str | None = None

    def __post_init__(self) -> None:
        _require_positive(self.mass_amu, "mass_amu")
        if self.isotopologue_label is not None:
            _require_text(self.isotopologue_label, "isotopologue_label")


@dataclass(frozen=True, slots=True)
class GasMoleculeModel:
    """Explicit rigid-rotor/electronic/mass metadata never guessed from a filename."""

    geometry_kind: GasGeometryKind
    symmetry_number: int
    spin_multiplicity: int
    atomic_masses: tuple[GasAtomicMass, ...]

    def __post_init__(self) -> None:
        if self.symmetry_number < 1:
            raise ThermochemistryContractError("symmetry_number must be positive")
        if self.spin_multiplicity < 1:
            raise ThermochemistryContractError("spin_multiplicity must be positive")
        if not self.atomic_masses:
            raise ThermochemistryContractError("gas model requires explicit atomic masses")
        masses = tuple(sorted(self.atomic_masses, key=lambda item: str(item.atom_uid)))
        atom_uids = tuple(item.atom_uid for item in masses)
        if len(atom_uids) != len(set(atom_uids)):
            raise ThermochemistryContractError("gas atomic masses require unique atom_uids")
        if self.geometry_kind is GasGeometryKind.MONATOMIC and len(masses) != 1:
            raise ThermochemistryContractError("monatomic gas model requires exactly one atom")
        if self.geometry_kind is GasGeometryKind.LINEAR and len(masses) < 2:
            raise ThermochemistryContractError("linear gas model requires at least two atoms")
        if self.geometry_kind is GasGeometryKind.NONLINEAR and len(masses) < 3:
            raise ThermochemistryContractError("nonlinear gas model requires at least three atoms")
        object.__setattr__(self, "atomic_masses", masses)


@dataclass(frozen=True, slots=True)
class ThermochemistryCorrection:
    """One visible additive correction with a versioned policy identity."""

    kind: ThermochemistryCorrectionKind
    label: str
    value_ev: float
    policy_id: str
    policy_version: str

    def __post_init__(self) -> None:
        _require_text(self.label, "label")
        _require_text(self.policy_id, "policy_id")
        _require_text(self.policy_version, "policy_version")
        _require_finite(self.value_ev, "value_ev")


@dataclass(frozen=True, slots=True)
class ThermochemistryIdentity:
    """Deterministic method identity for one thermochemistry Analysis."""

    subject_kind: ThermochemistrySubjectKind
    conditions: ThermochemicalConditions
    electronic_energy_kind: ElectronicEnergyKind
    electronic_entropy_policy: ElectronicEntropyPolicy
    vibrational_policy: VibrationalModePolicy | None
    gas_model: GasMoleculeModel | None = None
    corrections: tuple[ThermochemistryCorrection, ...] = ()

    def __post_init__(self) -> None:
        gas_state = self.conditions.standard_state in {
            ThermochemicalStandardState.IDEAL_GAS_1_BAR,
            ThermochemicalStandardState.IDEAL_GAS_1_ATM,
        }
        if self.subject_kind is ThermochemistrySubjectKind.GAS:
            if not gas_state:
                raise ThermochemistryContractError(
                    "gas subject requires an ideal-gas standard state"
                )
            if self.gas_model is None:
                raise ThermochemistryContractError("gas subject requires an explicit gas_model")
            if self.vibrational_policy is None:
                raise ThermochemistryContractError(
                    "gas subject requires an explicit vibrational mode policy"
                )
        else:
            if gas_state:
                raise ThermochemistryContractError(
                    "surface/adsorbate subject requires SURFACE_FIXED_CELL standard state"
                )
            if self.gas_model is not None:
                raise ThermochemistryContractError("non-gas subject must not carry gas_model")
            if (
                self.subject_kind is ThermochemistrySubjectKind.ADSORBATE
                and self.vibrational_policy is None
            ):
                raise ThermochemistryContractError(
                    "adsorbate thermochemistry requires an explicit vibrational mode policy"
                )
        if (
            self.electronic_entropy_policy is ElectronicEntropyPolicy.SPIN_DEGENERACY
            and self.gas_model is None
        ):
            raise ThermochemistryContractError(
                "spin-degeneracy electronic entropy requires explicit gas molecular metadata"
            )
        corrections = tuple(
            sorted(
                self.corrections,
                key=lambda item: (
                    item.kind.value,
                    item.policy_id,
                    item.policy_version,
                    item.label,
                ),
            )
        )
        correction_keys = tuple(
            (item.kind, item.policy_id, item.policy_version, item.label)
            for item in corrections
        )
        if len(correction_keys) != len(set(correction_keys)):
            raise ThermochemistryContractError(
                "thermochemistry correction identities must be unique"
            )
        object.__setattr__(self, "corrections", corrections)

    @property
    def parameters_hash(self) -> str:
        """Return the exact policy/condition hash suitable for Analysis.parameters_hash."""

        return canonical_sha256(self)


@dataclass(frozen=True, slots=True)
class ThermochemistryModeSelection:
    """Concrete accepted/excluded mode set recorded in a thermochemistry result artifact."""

    accepted_mode_indices: tuple[int, ...]
    excluded_modes: tuple[ModeExclusion, ...] = ()

    def __post_init__(self) -> None:
        if not self.accepted_mode_indices:
            raise ThermochemistryContractError(
                "thermochemistry requires at least one accepted mode"
            )
        if any(index < 1 for index in self.accepted_mode_indices):
            raise ThermochemistryContractError("accepted mode indices must be positive")
        accepted = tuple(sorted(self.accepted_mode_indices))
        if len(accepted) != len(set(accepted)):
            raise ThermochemistryContractError("accepted mode indices must be unique")
        excluded = tuple(
            sorted(
                self.excluded_modes,
                key=lambda item: (item.mode_index, item.reason.value),
            )
        )
        excluded_indices = tuple(item.mode_index for item in excluded)
        if len(excluded_indices) != len(set(excluded_indices)):
            raise ThermochemistryContractError("excluded mode indices must be unique")
        if set(accepted) & set(excluded_indices):
            raise ThermochemistryContractError("a mode cannot be both accepted and excluded")
        object.__setattr__(self, "accepted_mode_indices", accepted)
        object.__setattr__(self, "excluded_modes", excluded)


@dataclass(frozen=True, slots=True)
class ThermochemistryComponents:
    """Auditable thermochemical components; no component is hidden in a single correction."""

    electronic_energy_ev: float
    zpe_ev: float = 0.0
    vibrational_thermal_energy_ev: float = 0.0
    translational_thermal_energy_ev: float = 0.0
    rotational_thermal_energy_ev: float = 0.0
    pv_ev: float = 0.0
    vibrational_entropy_ev_per_k: float = 0.0
    translational_entropy_ev_per_k: float = 0.0
    rotational_entropy_ev_per_k: float = 0.0
    electronic_entropy_ev_per_k: float = 0.0
    corrections: tuple[ThermochemistryCorrection, ...] = ()

    def __post_init__(self) -> None:
        _require_finite(self.electronic_energy_ev, "electronic_energy_ev")
        nonnegative_values = (
            (self.zpe_ev, "zpe_ev"),
            (self.vibrational_thermal_energy_ev, "vibrational_thermal_energy_ev"),
            (self.translational_thermal_energy_ev, "translational_thermal_energy_ev"),
            (self.rotational_thermal_energy_ev, "rotational_thermal_energy_ev"),
            (self.pv_ev, "pv_ev"),
            (self.vibrational_entropy_ev_per_k, "vibrational_entropy_ev_per_k"),
            (self.translational_entropy_ev_per_k, "translational_entropy_ev_per_k"),
            (self.rotational_entropy_ev_per_k, "rotational_entropy_ev_per_k"),
            (self.electronic_entropy_ev_per_k, "electronic_entropy_ev_per_k"),
        )
        for value, field_name in nonnegative_values:
            _require_nonnegative(value, field_name)
        corrections = tuple(
            sorted(
                self.corrections,
                key=lambda item: (
                    item.kind.value,
                    item.policy_id,
                    item.policy_version,
                    item.label,
                ),
            )
        )
        object.__setattr__(self, "corrections", corrections)

    @property
    def total_entropy_ev_per_k(self) -> float:
        """Return the explicit entropy-component sum."""

        return (
            self.vibrational_entropy_ev_per_k
            + self.translational_entropy_ev_per_k
            + self.rotational_entropy_ev_per_k
            + self.electronic_entropy_ev_per_k
        )

    @property
    def total_correction_ev(self) -> float:
        """Return the visible additive correction sum without mutating source terms."""

        return sum(item.value_ev for item in self.corrections)


@dataclass(frozen=True, slots=True)
class ThermochemistryResult:
    """Canonical thermochemistry dataset content produced by a THERMOCHEMISTRY Analysis."""

    identity: ThermochemistryIdentity
    components: ThermochemistryComponents
    mode_selection: ThermochemistryModeSelection | None = None
    result_hash: str = field(init=False)

    def __post_init__(self) -> None:
        policy = self.identity.vibrational_policy
        selection = self.mode_selection
        if policy is None and selection is not None:
            raise ThermochemistryContractError(
                "mode selection requires a vibrational policy in the thermochemistry identity"
            )
        if policy is not None and selection is None:
            raise ThermochemistryContractError(
                "vibrational thermochemistry requires an explicit concrete mode selection"
            )
        if (
            policy is not None
            and selection is not None
            and selection.excluded_modes != policy.exclusions
        ):
            raise ThermochemistryContractError(
                "result mode exclusions must exactly match the identity policy"
            )
        if self.components.corrections != self.identity.corrections:
            raise ThermochemistryContractError(
                "result correction terms must exactly match the identity corrections"
            )
        object.__setattr__(
            self,
            "result_hash",
            canonical_sha256(
                {
                    "identity": self.identity,
                    "components": self.components,
                    "mode_selection": self.mode_selection,
                }
            ),
        )

    @property
    def gibbs_free_energy_ev(self) -> float:
        """Assemble G while preserving every component separately in the dataset."""

        components = self.components
        thermal_energy = (
            components.vibrational_thermal_energy_ev
            + components.translational_thermal_energy_ev
            + components.rotational_thermal_energy_ev
        )
        return (
            components.electronic_energy_ev
            + components.zpe_ev
            + thermal_energy
            + components.pv_ev
            - self.identity.conditions.temperature_k * components.total_entropy_ev_per_k
            + components.total_correction_ev
        )

"""Ideal-gas reference thermochemistry for the v0.8 Block 3 registry.

This layer consumes exact GAS_FREQUENCY parser facts and explicit molecular metadata.
It never infers species, geometry class, symmetry number, spin multiplicity, isotopic
masses, or standard state from a filename or chemical formula string.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
from math import exp, expm1, isfinite, log, log1p, pi
from pathlib import Path, PurePosixPath

import numpy as np

from ecatvasp.domain import (
    Analysis,
    AnalysisProducerRef,
    AnalysisStatus,
    AnalysisType,
    Artifact,
    ArtifactAvailability,
    ArtifactType,
    Calculation,
    CalculationScientificStatus,
    CalculationType,
    MethodFingerprint,
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
from ecatvasp.thermo.contracts import (
    ElectronicEnergyKind,
    ElectronicEntropyPolicy,
    GasGeometryKind,
    ImaginaryModePolicy,
    LowFrequencyPolicy,
    ModeExclusionReason,
    ThermochemistryComponents,
    ThermochemistryIdentity,
    ThermochemistryModeSelection,
    ThermochemistryResult,
    ThermochemistrySubjectKind,
)
from ecatvasp.vasp.results import (
    VASP_RESULT_DOCUMENT_FORMAT,
    VASP_RESULT_DOCUMENT_VERSION,
    VaspFrequencyMode,
    VaspFrequencyModeKind,
    VaspResultDocument,
)

IDEAL_GAS_THERMOCHEMISTRY_TOOL_NAME = "ecatvasp.thermo.ideal-gas-reference"
IDEAL_GAS_THERMOCHEMISTRY_TOOL_VERSION = "1"
CANONICAL_IDEAL_GAS_THERMOCHEMISTRY_FORMAT = "ecatvasp-canonical-ideal-gas-thermochemistry"
CANONICAL_IDEAL_GAS_THERMOCHEMISTRY_VERSION = 1

BOLTZMANN_J_PER_K = 1.380649e-23
BOLTZMANN_EV_PER_K = 8.617333262145e-5
PLANCK_J_S = 6.62607015e-34
ATOMIC_MASS_UNIT_KG = 1.66053906892e-27
JOULE_PER_EV = 1.602176634e-19
ANGSTROM_M = 1.0e-10
_LINEARITY_RELATIVE_TOLERANCE = 1.0e-6


class GasThermochemistryError(ValueError):
    """Raised when an ideal-gas reference cannot be evaluated without guessing."""


class GasReferenceSpecies(StrEnum):
    """Initial explicit gas-reference vocabulary for electrocatalysis."""

    H2 = "H2"
    H2O = "H2O"
    O2 = "O2"
    CO = "CO"
    CO2 = "CO2"


_EXPECTED_COMPOSITION: dict[GasReferenceSpecies, tuple[tuple[str, int], ...]] = {
    GasReferenceSpecies.H2: (("H", 2),),
    GasReferenceSpecies.H2O: (("H", 2), ("O", 1)),
    GasReferenceSpecies.O2: (("O", 2),),
    GasReferenceSpecies.CO: (("C", 1), ("O", 1)),
    GasReferenceSpecies.CO2: (("C", 1), ("O", 2)),
}


@dataclass(frozen=True, slots=True)
class GasReferenceDefinition:
    """Registry identity for one molecular reference, never a manually stored Gibbs number."""

    species: GasReferenceSpecies
    state_label: str = "electronic_ground_state"

    def __post_init__(self) -> None:
        if not self.state_label.strip():
            raise GasThermochemistryError("gas reference state_label must not be blank")

    @property
    def expected_composition(self) -> tuple[tuple[str, int], ...]:
        """Return the registry composition used only to validate the explicit source structure."""

        return _EXPECTED_COMPOSITION[self.species]

    @property
    def content_hash(self) -> str:
        """Return deterministic registry identity for provenance receipts."""

        return canonical_sha256(self)


INITIAL_GAS_REFERENCE_REGISTRY = tuple(
    GasReferenceDefinition(species)
    for species in (
        GasReferenceSpecies.H2,
        GasReferenceSpecies.H2O,
        GasReferenceSpecies.O2,
        GasReferenceSpecies.CO,
        GasReferenceSpecies.CO2,
    )
)


@dataclass(frozen=True, slots=True)
class GasRigidRotorEvidence:
    """Geometry evidence reconstructed from the exact snapshot and explicit masses."""

    total_mass_amu: float
    principal_moments_kg_m2: tuple[float, float, float]
    geometry_kind: GasGeometryKind

    def __post_init__(self) -> None:
        if not isfinite(self.total_mass_amu) or self.total_mass_amu <= 0.0:
            raise GasThermochemistryError("total gas mass must be finite and positive")
        if len(self.principal_moments_kg_m2) != 3 or any(
            not isfinite(value) or value < 0.0 for value in self.principal_moments_kg_m2
        ):
            raise GasThermochemistryError("principal moments must be three finite non-negative values")


@dataclass(frozen=True, slots=True)
class DurableGasThermochemistry:
    """Durable gas-reference THERMOCHEMISTRY Analysis and provenance chain."""

    analysis: Analysis
    artifact: Artifact
    reference: GasReferenceDefinition
    rotor_evidence: GasRigidRotorEvidence
    result: ThermochemistryResult
    provenance_records: tuple[ProvenanceRecord, ...]
    dependency_records: tuple[DependencyRecord, ...]


def calculate_ideal_gas_thermochemistry(
    *,
    reference: GasReferenceDefinition,
    structure_snapshot: StructureSnapshot,
    source_result: VaspResultDocument,
    identity: ThermochemistryIdentity,
) -> tuple[ThermochemistryResult, GasRigidRotorEvidence]:
    """Evaluate one ideal-gas reference from exact molecular structure and VASP facts."""

    _validate_gas_identity(identity)
    if source_result.calculation_type is not CalculationType.GAS_FREQUENCY:
        raise GasThermochemistryError(
            "ideal-gas thermochemistry requires CalculationType.GAS_FREQUENCY"
        )
    frequencies = source_result.frequencies
    if frequencies is None:
        raise GasThermochemistryError("gas-frequency VASP result has no frequency dataset")
    snapshot_uids = tuple(site.atom_uid for site in structure_snapshot.sites)
    if set(frequencies.atom_uids) != set(snapshot_uids):
        raise GasThermochemistryError(
            "gas frequency atom_uids differ from the exact StructureSnapshot"
        )
    if set(frequencies.displaced_atom_uids) != set(snapshot_uids):
        raise GasThermochemistryError(
            "gas thermochemistry requires finite differences for every molecular atom"
        )
    _validate_reference_composition(reference=reference, snapshot=structure_snapshot)

    gas_model = identity.gas_model
    if gas_model is None:
        raise GasThermochemistryError("gas thermochemistry requires explicit gas_model")
    mass_by_uid = {item.atom_uid: item.mass_amu for item in gas_model.atomic_masses}
    if set(mass_by_uid) != set(snapshot_uids):
        raise GasThermochemistryError(
            "explicit gas atomic masses must cover exactly the StructureSnapshot atom_uids"
        )
    rotor_evidence = _rigid_rotor_evidence(
        snapshot=structure_snapshot,
        mass_by_uid=mass_by_uid,
        geometry_kind=gas_model.geometry_kind,
    )
    selection = _select_gas_modes(
        modes=frequencies.modes,
        identity=identity,
        atom_count=len(snapshot_uids),
    )
    electronic_energy = _electronic_energy(source_result, identity.electronic_energy_kind)
    temperature = identity.conditions.temperature_k
    pressure = identity.conditions.pressure_pa
    if pressure is None:
        raise GasThermochemistryError("ideal-gas thermochemistry requires explicit pressure")

    zpe_ev = 0.0
    vibrational_thermal_energy_ev = 0.0
    vibrational_entropy_ev_per_k = 0.0
    by_index = {mode.mode_index: mode for mode in frequencies.modes}
    for mode_index in selection.accepted_mode_indices:
        mode = by_index[mode_index]
        zpe, thermal, entropy = _harmonic_mode_terms(
            energy_ev=mode.energy_mev / 1000.0,
            temperature_k=temperature,
        )
        zpe_ev += zpe
        vibrational_thermal_energy_ev += thermal
        vibrational_entropy_ev_per_k += entropy

    translational_energy_ev, translational_entropy_ev_per_k = _translation_terms(
        total_mass_amu=rotor_evidence.total_mass_amu,
        temperature_k=temperature,
        pressure_pa=pressure,
    )
    rotational_energy_ev, rotational_entropy_ev_per_k = _rotation_terms(
        evidence=rotor_evidence,
        symmetry_number=gas_model.symmetry_number,
        temperature_k=temperature,
    )
    electronic_entropy_ev_per_k = _electronic_entropy(
        policy=identity.electronic_entropy_policy,
        spin_multiplicity=gas_model.spin_multiplicity,
    )
    components = ThermochemistryComponents(
        electronic_energy_ev=electronic_energy,
        zpe_ev=zpe_ev,
        vibrational_thermal_energy_ev=vibrational_thermal_energy_ev,
        translational_thermal_energy_ev=translational_energy_ev,
        rotational_thermal_energy_ev=rotational_energy_ev,
        pv_ev=BOLTZMANN_EV_PER_K * temperature,
        vibrational_entropy_ev_per_k=vibrational_entropy_ev_per_k,
        translational_entropy_ev_per_k=translational_entropy_ev_per_k,
        rotational_entropy_ev_per_k=rotational_entropy_ev_per_k,
        electronic_entropy_ev_per_k=electronic_entropy_ev_per_k,
    )
    return (
        ThermochemistryResult(
            identity=identity,
            components=components,
            mode_selection=selection,
        ),
        rotor_evidence,
    )


def materialize_ideal_gas_thermochemistry(
    *,
    project_root: Path | str,
    reference: GasReferenceDefinition,
    calculation: Calculation,
    method_fingerprint: MethodFingerprint,
    structure_snapshot: StructureSnapshot,
    source_analysis: Analysis,
    source_artifact: Artifact,
    source_result: VaspResultDocument,
    identity: ThermochemistryIdentity,
) -> DurableGasThermochemistry:
    """Persist one exact ideal-gas registry reference through the existing Analysis DAG."""

    _validate_source_contract(
        calculation=calculation,
        method_fingerprint=method_fingerprint,
        structure_snapshot=structure_snapshot,
        source_analysis=source_analysis,
        source_artifact=source_artifact,
        source_result=source_result,
    )
    root = Path(project_root).resolve()
    if not root.is_dir():
        raise GasThermochemistryError("project_root must be an existing directory")
    _verify_parsed_result_artifact(
        root=root,
        calculation=calculation,
        source_analysis=source_analysis,
        source_artifact=source_artifact,
        source_result=source_result,
    )
    result, rotor_evidence = calculate_ideal_gas_thermochemistry(
        reference=reference,
        structure_snapshot=structure_snapshot,
        source_result=source_result,
        identity=identity,
    )
    if source_artifact.sha256 is None:
        raise GasThermochemistryError("parsed-result Artifact requires SHA-256")
    source_receipt = {
        "format": CANONICAL_IDEAL_GAS_THERMOCHEMISTRY_FORMAT,
        "version": CANONICAL_IDEAL_GAS_THERMOCHEMISTRY_VERSION,
        "reference": reference,
        "reference_content_hash": reference.content_hash,
        "calculation_id": calculation.id,
        "structure_snapshot_id": structure_snapshot.id,
        "method_fingerprint_id": method_fingerprint.id,
        "source_analysis_id": source_analysis.id,
        "source_artifact_id": source_artifact.id,
        "source_artifact_sha256": source_artifact.sha256,
        "source_result_hash": canonical_sha256(source_result),
        "thermochemistry_identity": identity,
        "thermochemistry_parameters_hash": identity.parameters_hash,
        "rotor_evidence": rotor_evidence,
    }
    source_receipt_hash = canonical_sha256(source_receipt)
    analysis = Analysis(
        project_id=calculation.project_id,
        analysis_type=AnalysisType.THERMOCHEMISTRY,
        input_artifact_ids=(source_artifact.id,),
        status=AnalysisStatus.COMPLETED,
        tool=IDEAL_GAS_THERMOCHEMISTRY_TOOL_NAME,
        tool_version=IDEAL_GAS_THERMOCHEMISTRY_TOOL_VERSION,
        parameters_hash=source_receipt_hash,
    )
    payload = {
        "format": CANONICAL_IDEAL_GAS_THERMOCHEMISTRY_FORMAT,
        "version": CANONICAL_IDEAL_GAS_THERMOCHEMISTRY_VERSION,
        "analysis_id": analysis.id,
        "source_receipt": source_receipt,
        "source_receipt_hash": source_receipt_hash,
        "result_hash": result.result_hash,
        "result": result,
    }
    artifact = _write_result_artifact(root=root, analysis=analysis, payload=payload)
    provenance_records = (
        ProvenanceRecord(
            subject_id=analysis.id,
            tool=IDEAL_GAS_THERMOCHEMISTRY_TOOL_NAME,
            tool_version=IDEAL_GAS_THERMOCHEMISTRY_TOOL_VERSION,
            parameters_hash=analysis.parameters_hash,
            method_fingerprint_id=method_fingerprint.id,
        ),
        ProvenanceRecord(
            subject_id=artifact.id,
            tool=IDEAL_GAS_THERMOCHEMISTRY_TOOL_NAME,
            tool_version=IDEAL_GAS_THERMOCHEMISTRY_TOOL_VERSION,
            parameters_hash=artifact.sha256,
            method_fingerprint_id=method_fingerprint.id,
        ),
    )
    dependency_records = (
        DependencyRecord(
            upstream_id=calculation.id,
            downstream_id=analysis.id,
            kind=DependencyKind.SCIENTIFIC,
            role="gas_frequency_calculation",
            recorded_hash=scientific_hash(calculation),
        ),
        DependencyRecord(
            upstream_id=method_fingerprint.id,
            downstream_id=analysis.id,
            kind=DependencyKind.SCIENTIFIC,
            role="method_fingerprint",
            recorded_hash=scientific_hash(method_fingerprint),
        ),
        DependencyRecord(
            upstream_id=structure_snapshot.id,
            downstream_id=analysis.id,
            kind=DependencyKind.SCIENTIFIC,
            role="gas_structure_snapshot",
            recorded_hash=scientific_hash(structure_snapshot),
        ),
        DependencyRecord(
            upstream_id=source_analysis.id,
            downstream_id=analysis.id,
            kind=DependencyKind.SCIENTIFIC,
            role="parsed_result_analysis",
            recorded_hash=scientific_hash(source_analysis),
        ),
        DependencyRecord(
            upstream_id=source_artifact.id,
            downstream_id=analysis.id,
            kind=DependencyKind.SCIENTIFIC,
            role="parsed_gas_frequency_result",
            recorded_hash=scientific_hash(source_artifact),
        ),
        DependencyRecord(
            upstream_id=analysis.id,
            downstream_id=artifact.id,
            kind=DependencyKind.SCIENTIFIC,
            role="ideal_gas_thermochemistry",
            recorded_hash=scientific_hash(analysis),
        ),
    )
    return DurableGasThermochemistry(
        analysis=analysis,
        artifact=artifact,
        reference=reference,
        rotor_evidence=rotor_evidence,
        result=result,
        provenance_records=provenance_records,
        dependency_records=dependency_records,
    )


def _validate_gas_identity(identity: ThermochemistryIdentity) -> None:
    if identity.subject_kind is not ThermochemistrySubjectKind.GAS:
        raise GasThermochemistryError("ideal-gas thermochemistry requires GAS subject_kind")
    if identity.gas_model is None:
        raise GasThermochemistryError("ideal-gas thermochemistry requires explicit gas_model")
    if identity.vibrational_policy is None:
        raise GasThermochemistryError("ideal-gas thermochemistry requires vibrational policy")
    if identity.corrections:
        raise GasThermochemistryError(
            "Block 3 does not apply correction policies; use the explicit correction layer"
        )
    if identity.gas_model.geometry_kind is GasGeometryKind.MONATOMIC:
        raise GasThermochemistryError(
            "Block 3 registry contains molecular references only; monatomic gas is not supported"
        )


def _validate_reference_composition(
    *, reference: GasReferenceDefinition, snapshot: StructureSnapshot
) -> None:
    actual = Counter(site.element for site in snapshot.sites)
    expected = Counter(dict(reference.expected_composition))
    if actual != expected:
        raise GasThermochemistryError(
            f"{reference.species.value} registry reference composition differs from StructureSnapshot"
        )


def _electronic_energy(source_result: VaspResultDocument, kind: ElectronicEnergyKind) -> float:
    values = {
        ElectronicEnergyKind.SIGMA_ZERO: source_result.energies.energy_sigma0_ev,
        ElectronicEnergyKind.WITHOUT_ENTROPY: source_result.energies.energy_without_entropy_ev,
        ElectronicEnergyKind.TOTEN: source_result.energies.free_energy_toten_ev,
    }
    try:
        value = values[kind]
    except KeyError as error:
        raise GasThermochemistryError("unsupported electronic-energy semantic") from error
    if value is None:
        raise GasThermochemistryError(
            f"selected VASP electronic-energy semantic is missing: {kind.value}"
        )
    if not isfinite(value):
        raise GasThermochemistryError("selected electronic energy must be finite")
    return value


def _select_gas_modes(
    *,
    modes: tuple[VaspFrequencyMode, ...],
    identity: ThermochemistryIdentity,
    atom_count: int,
) -> ThermochemistryModeSelection:
    policy = identity.vibrational_policy
    gas_model = identity.gas_model
    if policy is None or gas_model is None:
        raise GasThermochemistryError("gas mode selection requires explicit policies")
    by_index = {mode.mode_index: mode for mode in modes}
    exclusions = {item.mode_index: item for item in policy.exclusions}
    missing = tuple(index for index in exclusions if index not in by_index)
    if missing:
        raise GasThermochemistryError(
            f"gas mode exclusions reference absent raw VASP modes: {missing}"
        )
    if any(item.reason is ModeExclusionReason.CONSTRAINED for item in policy.exclusions):
        raise GasThermochemistryError(
            "gas-reference thermochemistry requires an unconstrained molecular frequency set"
        )
    translation_indices = {
        item.mode_index
        for item in policy.exclusions
        if item.reason is ModeExclusionReason.TRANSLATIONAL
    }
    rotation_indices = {
        item.mode_index
        for item in policy.exclusions
        if item.reason is ModeExclusionReason.ROTATIONAL
    }
    expected_rotations = 2 if gas_model.geometry_kind is GasGeometryKind.LINEAR else 3
    if len(translation_indices) != 3:
        raise GasThermochemistryError(
            "molecular gas thermochemistry requires exactly three explicit translational exclusions"
        )
    if len(rotation_indices) != expected_rotations:
        raise GasThermochemistryError(
            f"{gas_model.geometry_kind.value} gas requires exactly {expected_rotations} rotational exclusions"
        )
    rigid_body_indices = translation_indices | rotation_indices

    for exclusion in policy.exclusions:
        mode = by_index[exclusion.mode_index]
        if (
            exclusion.reason is ModeExclusionReason.IMAGINARY
            and mode.kind is not VaspFrequencyModeKind.IMAGINARY
        ):
            raise GasThermochemistryError(
                "IMAGINARY exclusion must reference an explicitly imaginary VASP mode"
            )
        if exclusion.reason is ModeExclusionReason.LOW_FREQUENCY and not (
            mode.kind is VaspFrequencyModeKind.REAL
            and mode.wavenumber_cm_inverse < policy.frequency_cutoff_cm_inverse
        ):
            raise GasThermochemistryError(
                "LOW_FREQUENCY exclusion must reference a real mode below the cutoff"
            )

    imaginary = tuple(mode for mode in modes if mode.kind is VaspFrequencyModeKind.IMAGINARY)
    if policy.imaginary_mode_policy is ImaginaryModePolicy.REJECT_ANY:
        if imaginary:
            raise GasThermochemistryError("imaginary VASP modes violate REJECT_ANY gas policy")
    else:
        unexcluded = tuple(
            mode.mode_index for mode in imaginary if mode.mode_index not in exclusions
        )
        if unexcluded:
            raise GasThermochemistryError(
                f"imaginary gas modes require explicit exclusions: {unexcluded}"
            )

    candidate_low_real = tuple(
        mode
        for mode in modes
        if mode.kind is VaspFrequencyModeKind.REAL
        and mode.mode_index not in rigid_body_indices
        and mode.wavenumber_cm_inverse < policy.frequency_cutoff_cm_inverse
    )
    if policy.low_frequency_policy is LowFrequencyPolicy.REJECT_BELOW_CUTOFF:
        if candidate_low_real:
            raise GasThermochemistryError(
                "real vibrational gas modes below cutoff violate selected policy"
            )
    else:
        unexcluded_low = tuple(
            mode.mode_index
            for mode in candidate_low_real
            if mode.mode_index not in exclusions
        )
        if unexcluded_low:
            raise GasThermochemistryError(
                f"low-frequency gas vibrations require explicit exclusions: {unexcluded_low}"
            )

    accepted = tuple(
        mode.mode_index
        for mode in modes
        if mode.kind is VaspFrequencyModeKind.REAL
        and mode.mode_index not in exclusions
        and mode.wavenumber_cm_inverse >= policy.frequency_cutoff_cm_inverse
    )
    if not accepted:
        raise GasThermochemistryError("no accepted molecular vibrational modes remain")
    maximum_vibrations = 3 * atom_count - 3 - expected_rotations
    if len(accepted) > maximum_vibrations:
        raise GasThermochemistryError(
            "accepted gas vibrational modes exceed rigid-molecule degrees of freedom"
        )
    for mode_index in accepted:
        if by_index[mode_index].energy_mev <= 0.0:
            raise GasThermochemistryError("accepted gas vibrational mode requires positive energy")
    return ThermochemistryModeSelection(
        accepted_mode_indices=accepted,
        excluded_modes=policy.exclusions,
    )


def _harmonic_mode_terms(
    *, energy_ev: float, temperature_k: float
) -> tuple[float, float, float]:
    if not isfinite(energy_ev) or energy_ev <= 0.0:
        raise GasThermochemistryError("harmonic gas mode energy must be finite and positive")
    x = energy_ev / (BOLTZMANN_EV_PER_K * temperature_k)
    if not isfinite(x) or x <= 0.0:
        raise GasThermochemistryError("dimensionless gas vibrational frequency must be positive")
    zpe = 0.5 * energy_ev
    if x > 700.0:
        return zpe, 0.0, 0.0
    denominator = expm1(x)
    thermal = energy_ev / denominator
    entropy = BOLTZMANN_EV_PER_K * (x / denominator - log1p(-exp(-x)))
    if not all(isfinite(value) and value >= 0.0 for value in (zpe, thermal, entropy)):
        raise GasThermochemistryError("gas harmonic thermochemistry produced invalid components")
    return zpe, thermal, entropy


def _translation_terms(
    *, total_mass_amu: float, temperature_k: float, pressure_pa: float
) -> tuple[float, float]:
    if not isfinite(pressure_pa) or pressure_pa <= 0.0:
        raise GasThermochemistryError("gas pressure must be finite and positive")
    mass_kg = total_mass_amu * ATOMIC_MASS_UNIT_KG
    thermal_j = BOLTZMANN_J_PER_K * temperature_k
    log_q = 1.5 * log(2.0 * pi * mass_kg * thermal_j / (PLANCK_J_S**2)) + log(
        thermal_j / pressure_pa
    )
    entropy_ev_per_k = (BOLTZMANN_J_PER_K / JOULE_PER_EV) * (log_q + 2.5)
    energy_ev = 1.5 * BOLTZMANN_EV_PER_K * temperature_k
    if not isfinite(entropy_ev_per_k) or entropy_ev_per_k < 0.0:
        raise GasThermochemistryError("ideal-gas translational entropy is invalid")
    return energy_ev, entropy_ev_per_k


def _rotation_terms(
    *, evidence: GasRigidRotorEvidence, symmetry_number: int, temperature_k: float
) -> tuple[float, float]:
    if symmetry_number < 1:
        raise GasThermochemistryError("gas symmetry number must be positive")
    moments = evidence.principal_moments_kg_m2
    thermal_j = BOLTZMANN_J_PER_K * temperature_k
    if evidence.geometry_kind is GasGeometryKind.LINEAR:
        rotational_moment = 0.5 * (moments[1] + moments[2])
        if rotational_moment <= 0.0:
            raise GasThermochemistryError("linear gas requires positive rotational moment")
        log_q = log(
            8.0
            * pi**2
            * rotational_moment
            * thermal_j
            / (symmetry_number * PLANCK_J_S**2)
        )
        entropy = (BOLTZMANN_J_PER_K / JOULE_PER_EV) * (log_q + 1.0)
        energy = BOLTZMANN_EV_PER_K * temperature_k
    elif evidence.geometry_kind is GasGeometryKind.NONLINEAR:
        if any(moment <= 0.0 for moment in moments):
            raise GasThermochemistryError("nonlinear gas requires three positive principal moments")
        log_q = (
            0.5 * log(pi)
            - log(float(symmetry_number))
            + 1.5 * log(8.0 * pi**2 * thermal_j / (PLANCK_J_S**2))
            + 0.5 * sum(log(moment) for moment in moments)
        )
        entropy = (BOLTZMANN_J_PER_K / JOULE_PER_EV) * (log_q + 1.5)
        energy = 1.5 * BOLTZMANN_EV_PER_K * temperature_k
    else:
        raise GasThermochemistryError("monatomic rotation is outside the Block 3 molecular registry")
    if not isfinite(entropy) or entropy < 0.0:
        raise GasThermochemistryError("ideal-gas rotational entropy is invalid")
    return energy, entropy


def _electronic_entropy(
    *, policy: ElectronicEntropyPolicy, spin_multiplicity: int
) -> float:
    if policy is ElectronicEntropyPolicy.NEGLECTED:
        return 0.0
    if policy is ElectronicEntropyPolicy.SPIN_DEGENERACY:
        return BOLTZMANN_EV_PER_K * log(float(spin_multiplicity))
    raise GasThermochemistryError("unsupported electronic entropy policy")


def _rigid_rotor_evidence(
    *,
    snapshot: StructureSnapshot,
    mass_by_uid: dict[object, float],
    geometry_kind: GasGeometryKind,
) -> GasRigidRotorEvidence:
    if geometry_kind is GasGeometryKind.MONATOMIC:
        raise GasThermochemistryError("monatomic gas is outside the initial Block 3 registry")
    positions = _unwrapped_cartesian_positions_m(snapshot)
    masses = tuple(mass_by_uid[site.atom_uid] for site in snapshot.sites)
    total_mass_amu = sum(masses)
    if not isfinite(total_mass_amu) or total_mass_amu <= 0.0:
        raise GasThermochemistryError("gas total mass must be finite and positive")
    total_mass_kg = total_mass_amu * ATOMIC_MASS_UNIT_KG
    center = np.zeros(3, dtype=float)
    for site, mass_amu in zip(snapshot.sites, masses, strict=True):
        center += mass_amu * np.asarray(positions[site.atom_uid], dtype=float)
    center /= total_mass_amu

    inertia = np.zeros((3, 3), dtype=float)
    for site, mass_amu in zip(snapshot.sites, masses, strict=True):
        displacement = np.asarray(positions[site.atom_uid], dtype=float) - center
        mass_kg = mass_amu * ATOMIC_MASS_UNIT_KG
        radius_sq = float(np.dot(displacement, displacement))
        inertia += mass_kg * (
            radius_sq * np.eye(3, dtype=float) - np.outer(displacement, displacement)
        )
    moments_array = np.linalg.eigvalsh(inertia)
    scale = float(max(abs(value) for value in moments_array))
    if scale <= 0.0 or not isfinite(scale):
        raise GasThermochemistryError("molecular geometry has no finite rotational inertia")
    moments = tuple(
        0.0 if abs(float(value)) <= scale * 1.0e-12 else float(value)
        for value in moments_array
    )
    if any(value < 0.0 for value in moments):
        raise GasThermochemistryError("molecular inertia tensor is not positive semidefinite")
    relative_minimum = moments[0] / moments[2]
    if geometry_kind is GasGeometryKind.LINEAR:
        if relative_minimum > _LINEARITY_RELATIVE_TOLERANCE:
            raise GasThermochemistryError(
                "explicit LINEAR gas model disagrees with StructureSnapshot geometry"
            )
    elif geometry_kind is GasGeometryKind.NONLINEAR:
        if relative_minimum <= _LINEARITY_RELATIVE_TOLERANCE:
            raise GasThermochemistryError(
                "explicit NONLINEAR gas model disagrees with StructureSnapshot geometry"
            )
    else:
        raise GasThermochemistryError("unsupported molecular geometry kind")
    return GasRigidRotorEvidence(
        total_mass_amu=total_mass_amu,
        principal_moments_kg_m2=moments,
        geometry_kind=geometry_kind,
    )


def _unwrapped_cartesian_positions_m(
    snapshot: StructureSnapshot,
) -> dict[object, tuple[float, float, float]]:
    anchor = snapshot.sites[0].fractional_coords
    vectors = snapshot.lattice.vectors
    positions: dict[object, tuple[float, float, float]] = {}
    for site in snapshot.sites:
        delta = [
            site.fractional_coords[index] - anchor[index]
            for index in range(3)
        ]
        for index, periodic in enumerate(snapshot.periodic):
            if periodic:
                delta[index] -= round(delta[index])
        fractional = tuple(anchor[index] + delta[index] for index in range(3))
        cartesian_angstrom = tuple(
            sum(fractional[axis] * vectors[axis][component] for axis in range(3))
            for component in range(3)
        )
        positions[site.atom_uid] = tuple(value * ANGSTROM_M for value in cartesian_angstrom)
    return positions


def _validate_source_contract(
    *,
    calculation: Calculation,
    method_fingerprint: MethodFingerprint,
    structure_snapshot: StructureSnapshot,
    source_analysis: Analysis,
    source_artifact: Artifact,
    source_result: VaspResultDocument,
) -> None:
    if calculation.calculation_type is not CalculationType.GAS_FREQUENCY:
        raise GasThermochemistryError("Block 3 requires a GAS_FREQUENCY Calculation")
    if calculation.status is not CalculationScientificStatus.CONVERGED:
        raise GasThermochemistryError("gas-frequency Calculation must be scientifically CONVERGED")
    if method_fingerprint.id != calculation.method_fingerprint_id:
        raise GasThermochemistryError("MethodFingerprint differs from gas Calculation identity")
    if method_fingerprint.recipe.recipe_id != calculation.recipe_id:
        raise GasThermochemistryError("MethodFingerprint recipe differs from gas Calculation recipe")
    if structure_snapshot.id != calculation.input_structure_snapshot_id:
        raise GasThermochemistryError("StructureSnapshot differs from gas Calculation input")
    if source_analysis.project_id != calculation.project_id:
        raise GasThermochemistryError("parsed gas Analysis belongs to another project")
    if source_analysis.analysis_type is not AnalysisType.RESULT_PARSE:
        raise GasThermochemistryError("gas source Analysis must be RESULT_PARSE")
    if source_analysis.status is not AnalysisStatus.COMPLETED:
        raise GasThermochemistryError("parsed gas Analysis must be completed")
    if source_artifact.artifact_type is not ArtifactType.PARSED_RESULT:
        raise GasThermochemistryError("gas source Artifact must be PARSED_RESULT")
    if (
        not isinstance(source_artifact.producer, AnalysisProducerRef)
        or source_artifact.producer.id != source_analysis.id
    ):
        raise GasThermochemistryError("parsed gas Artifact producer differs from Analysis")
    if source_artifact.availability not in {
        ArtifactAvailability.LOCAL,
        ArtifactAvailability.BOTH,
    }:
        raise GasThermochemistryError("parsed gas Artifact must be locally available")
    if source_artifact.local_path is None:
        raise GasThermochemistryError("parsed gas Artifact requires local_path")
    if source_artifact.sha256 is None or source_artifact.size_bytes is None:
        raise GasThermochemistryError("parsed gas Artifact requires hash and byte size")
    if source_result.calculation_type is not calculation.calculation_type:
        raise GasThermochemistryError("parsed gas VASP result CalculationType differs")


def _verify_parsed_result_artifact(
    *,
    root: Path,
    calculation: Calculation,
    source_analysis: Analysis,
    source_artifact: Artifact,
    source_result: VaspResultDocument,
) -> None:
    if source_artifact.local_path is None:
        raise GasThermochemistryError("parsed gas Artifact requires local_path")
    relative = PurePosixPath(source_artifact.local_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise GasThermochemistryError("parsed gas Artifact path must be project-relative")
    absolute = (root / Path(*relative.parts)).resolve()
    if not absolute.is_relative_to(root) or not absolute.is_file():
        raise GasThermochemistryError("parsed gas Artifact file is unavailable")
    body = absolute.read_bytes()
    if source_artifact.size_bytes != len(body):
        raise GasThermochemistryError("parsed gas Artifact byte size differs")
    if source_artifact.sha256 != hashlib.sha256(body).hexdigest():
        raise GasThermochemistryError("parsed gas Artifact SHA-256 differs")
    try:
        raw_payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GasThermochemistryError("parsed gas Artifact is not valid UTF-8 JSON") from error
    payload = _mapping(raw_payload, "parsed gas payload")
    if payload.get("format") != VASP_RESULT_DOCUMENT_FORMAT:
        raise GasThermochemistryError("parsed gas Artifact format is unsupported")
    if payload.get("version") != VASP_RESULT_DOCUMENT_VERSION:
        raise GasThermochemistryError("parsed gas Artifact version is unsupported")
    if payload.get("calculation_id") != str(calculation.id):
        raise GasThermochemistryError("parsed gas Artifact belongs to another Calculation")
    if payload.get("analysis_id") != str(source_analysis.id):
        raise GasThermochemistryError("parsed gas Artifact belongs to another Analysis")
    if canonical_sha256(payload.get("result")) != canonical_sha256(source_result):
        raise GasThermochemistryError(
            "in-memory gas VASP result differs from durable parsed-result Artifact"
        )


def _write_result_artifact(
    *, root: Path, analysis: Analysis, payload: object
) -> Artifact:
    relative = Path("analyses") / str(analysis.id) / "canonical-ideal-gas-thermochemistry.json"
    absolute = (root / relative).resolve()
    if not absolute.is_relative_to(root):
        raise GasThermochemistryError("ideal-gas output path resolves outside project_root")
    text = canonical_json(payload) + "\n"
    absolute.parent.mkdir(parents=True, exist_ok=True)
    if absolute.exists():
        if not absolute.is_file():
            raise GasThermochemistryError("ideal-gas output path is not a regular file")
        if absolute.read_text(encoding="utf-8") != text:
            raise GasThermochemistryError("ideal-gas output already exists with different content")
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
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise GasThermochemistryError(f"{field_name} must be a JSON object")
    return value

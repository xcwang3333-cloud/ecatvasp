"""Strict reconstruction of canonical v0.8 thermochemistry value objects.

Canonical thermochemistry Artifacts use the scientific ``canonical_json`` representation, which is
plain JSON rather than the ProjectStore tagged codec.  Block 7 must therefore reconstruct the frozen
v0.8 domain objects before passing them to reaction/CHE authorities.  This module performs that
reconstruction and verifies the deterministic hashes; it contains no thermodynamic equations.
"""

from __future__ import annotations

from typing import TypeVar, cast
from uuid import UUID

from ecatvasp.api.application import ApplicationServiceError
from ecatvasp.domain import ArtifactId, AtomUid, canonical_sha256
from ecatvasp.thermo import (
    CorrectionEvidence,
    CorrectionEvidenceKind,
    ElectronicEnergyKind,
    ElectronicEntropyPolicy,
    GasAtomicMass,
    GasGeometryKind,
    GasMoleculeModel,
    GasReferenceAdjustmentIdentity,
    GasReferenceDefinition,
    GasReferenceSpecies,
    ImaginaryModePolicy,
    LowFrequencyPolicy,
    ModeExclusion,
    ModeExclusionReason,
    ReferenceCorrectionPolicy,
    ReferencePhase,
    ReferenceThermochemistryResult,
    ThermochemicalConditions,
    ThermochemicalStandardState,
    ThermochemistryComponents,
    ThermochemistryCorrection,
    ThermochemistryCorrectionKind,
    ThermochemistryIdentity,
    ThermochemistryModeSelection,
    ThermochemistryResult,
    ThermochemistrySubjectKind,
    VibrationalModePolicy,
)

_EnumT = TypeVar("_EnumT")


def decode_thermochemistry_result(value: object) -> ThermochemistryResult:
    """Reconstruct one canonical ``ThermochemistryResult`` and verify its self hash."""

    raw = _mapping(value, "thermochemistry result")
    result = ThermochemistryResult(
        identity=_identity(_required_mapping(raw, "identity")),
        components=_components(_required_mapping(raw, "components")),
        mode_selection=(
            None
            if raw.get("mode_selection") is None
            else _mode_selection(_mapping(raw.get("mode_selection"), "mode_selection"))
        ),
    )
    expected_hash = _required_string(raw, "result_hash")
    if result.result_hash != expected_hash or canonical_sha256(result) != canonical_sha256(raw):
        raise ApplicationServiceError("canonical thermochemistry result hash is inconsistent")
    return result


def decode_reference_result(value: object) -> ReferenceThermochemistryResult:
    """Reconstruct one canonical corrected molecular-reference result and verify its self hash."""

    raw = _mapping(value, "reference thermochemistry result")
    result = ReferenceThermochemistryResult(
        adjustment=_reference_adjustment(_required_mapping(raw, "adjustment")),
        source_result_hash=_required_string(raw, "source_result_hash"),
        source_gibbs_free_energy_ev=_required_number(raw, "source_gibbs_free_energy_ev"),
    )
    if result.corrected_gibbs_free_energy_ev != _required_number(
        raw,
        "corrected_gibbs_free_energy_ev",
    ):
        raise ApplicationServiceError(
            "canonical corrected molecular-reference Gibbs energy is inconsistent"
        )
    expected_hash = _required_string(raw, "result_hash")
    if result.result_hash != expected_hash or canonical_sha256(result) != canonical_sha256(raw):
        raise ApplicationServiceError("canonical reference thermochemistry result hash is inconsistent")
    return result


def decode_gas_reference(value: object) -> GasReferenceDefinition:
    raw = _mapping(value, "gas reference")
    reference = GasReferenceDefinition(
        species=_enum(GasReferenceSpecies, raw, "species"),
        state_label=_required_string(raw, "state_label"),
    )
    if canonical_sha256(reference) != canonical_sha256(raw):
        raise ApplicationServiceError("canonical gas reference identity is inconsistent")
    return reference


def _identity(raw: dict[str, object]) -> ThermochemistryIdentity:
    corrections = tuple(
        _correction(_mapping(item, "thermochemistry correction"))
        for item in _list(raw.get("corrections", []), "corrections")
    )
    gas_model_raw = raw.get("gas_model")
    vibrational_raw = raw.get("vibrational_policy")
    return ThermochemistryIdentity(
        subject_kind=_enum(ThermochemistrySubjectKind, raw, "subject_kind"),
        conditions=_conditions(_required_mapping(raw, "conditions")),
        electronic_energy_kind=_enum(ElectronicEnergyKind, raw, "electronic_energy_kind"),
        electronic_entropy_policy=_enum(
            ElectronicEntropyPolicy,
            raw,
            "electronic_entropy_policy",
        ),
        vibrational_policy=(
            None
            if vibrational_raw is None
            else _vibrational_policy(_mapping(vibrational_raw, "vibrational_policy"))
        ),
        gas_model=None if gas_model_raw is None else _gas_model(_mapping(gas_model_raw, "gas_model")),
        corrections=corrections,
    )


def _conditions(raw: dict[str, object]) -> ThermochemicalConditions:
    pressure = raw.get("pressure_pa")
    return ThermochemicalConditions(
        temperature_k=_required_number(raw, "temperature_k"),
        standard_state=_enum(ThermochemicalStandardState, raw, "standard_state"),
        pressure_pa=None if pressure is None else _number(pressure, "pressure_pa"),
    )


def _vibrational_policy(raw: dict[str, object]) -> VibrationalModePolicy:
    exclusions = tuple(
        _mode_exclusion(_mapping(item, "mode exclusion"))
        for item in _list(raw.get("exclusions", []), "exclusions")
    )
    return VibrationalModePolicy(
        frequency_cutoff_cm_inverse=_required_number(raw, "frequency_cutoff_cm_inverse"),
        imaginary_mode_policy=_enum(ImaginaryModePolicy, raw, "imaginary_mode_policy"),
        low_frequency_policy=_enum(LowFrequencyPolicy, raw, "low_frequency_policy"),
        exclusions=exclusions,
    )


def _mode_exclusion(raw: dict[str, object]) -> ModeExclusion:
    note = raw.get("note")
    return ModeExclusion(
        mode_index=_required_int(raw, "mode_index"),
        reason=_enum(ModeExclusionReason, raw, "reason"),
        note=None if note is None else _string(note, "note"),
    )


def _gas_model(raw: dict[str, object]) -> GasMoleculeModel:
    masses = tuple(
        _gas_atomic_mass(_mapping(item, "gas atomic mass"))
        for item in _list(raw.get("atomic_masses"), "atomic_masses")
    )
    return GasMoleculeModel(
        geometry_kind=_enum(GasGeometryKind, raw, "geometry_kind"),
        symmetry_number=_required_int(raw, "symmetry_number"),
        spin_multiplicity=_required_int(raw, "spin_multiplicity"),
        atomic_masses=masses,
    )


def _gas_atomic_mass(raw: dict[str, object]) -> GasAtomicMass:
    label = raw.get("isotopologue_label")
    return GasAtomicMass(
        atom_uid=AtomUid(_uuid(_required_string(raw, "atom_uid"), "atom_uid")),
        mass_amu=_required_number(raw, "mass_amu"),
        isotopologue_label=(
            None if label is None else _string(label, "isotopologue_label")
        ),
    )


def _correction(raw: dict[str, object]) -> ThermochemistryCorrection:
    return ThermochemistryCorrection(
        kind=_enum(ThermochemistryCorrectionKind, raw, "kind"),
        label=_required_string(raw, "label"),
        value_ev=_required_number(raw, "value_ev"),
        policy_id=_required_string(raw, "policy_id"),
        policy_version=_required_string(raw, "policy_version"),
    )


def _components(raw: dict[str, object]) -> ThermochemistryComponents:
    corrections = tuple(
        _correction(_mapping(item, "component correction"))
        for item in _list(raw.get("corrections", []), "component corrections")
    )
    return ThermochemistryComponents(
        electronic_energy_ev=_required_number(raw, "electronic_energy_ev"),
        zpe_ev=_required_number(raw, "zpe_ev"),
        vibrational_thermal_energy_ev=_required_number(raw, "vibrational_thermal_energy_ev"),
        translational_thermal_energy_ev=_required_number(
            raw,
            "translational_thermal_energy_ev",
        ),
        rotational_thermal_energy_ev=_required_number(raw, "rotational_thermal_energy_ev"),
        pv_ev=_required_number(raw, "pv_ev"),
        vibrational_entropy_ev_per_k=_required_number(raw, "vibrational_entropy_ev_per_k"),
        translational_entropy_ev_per_k=_required_number(
            raw,
            "translational_entropy_ev_per_k",
        ),
        rotational_entropy_ev_per_k=_required_number(raw, "rotational_entropy_ev_per_k"),
        electronic_entropy_ev_per_k=_required_number(raw, "electronic_entropy_ev_per_k"),
        corrections=corrections,
    )


def _mode_selection(raw: dict[str, object]) -> ThermochemistryModeSelection:
    accepted = tuple(
        _int(item, "accepted_mode_indices item")
        for item in _list(raw.get("accepted_mode_indices"), "accepted_mode_indices")
    )
    excluded = tuple(
        _mode_exclusion(_mapping(item, "excluded mode"))
        for item in _list(raw.get("excluded_modes", []), "excluded_modes")
    )
    return ThermochemistryModeSelection(
        accepted_mode_indices=accepted,
        excluded_modes=excluded,
    )


def _reference_adjustment(raw: dict[str, object]) -> GasReferenceAdjustmentIdentity:
    policies = tuple(
        _reference_policy(_mapping(item, "reference correction policy"))
        for item in _list(raw.get("policies"), "reference correction policies")
    )
    return GasReferenceAdjustmentIdentity(
        reference=decode_gas_reference(raw.get("reference")),
        target_phase=_enum(ReferencePhase, raw, "target_phase"),
        policies=policies,
    )


def _reference_policy(raw: dict[str, object]) -> ReferenceCorrectionPolicy:
    note = raw.get("note")
    return ReferenceCorrectionPolicy(
        correction=_correction(_required_mapping(raw, "correction")),
        evidence=_evidence(_required_mapping(raw, "evidence")),
        note=None if note is None else _string(note, "note"),
    )


def _evidence(raw: dict[str, object]) -> CorrectionEvidence:
    citation = raw.get("citation")
    artifact_id = raw.get("artifact_id")
    artifact_sha = raw.get("artifact_sha256")
    return CorrectionEvidence(
        kind=_enum(CorrectionEvidenceKind, raw, "kind"),
        source_id=_required_string(raw, "source_id"),
        source_version=_required_string(raw, "source_version"),
        citation=None if citation is None else _string(citation, "citation"),
        artifact_id=(
            None
            if artifact_id is None
            else ArtifactId(_uuid(_string(artifact_id, "artifact_id"), "artifact_id"))
        ),
        artifact_sha256=(
            None if artifact_sha is None else _string(artifact_sha, "artifact_sha256")
        ),
    )


def _mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ApplicationServiceError(f"{label} must be a JSON object")
    return cast(dict[str, object], value)


def _required_mapping(raw: dict[str, object], field: str) -> dict[str, object]:
    if field not in raw:
        raise ApplicationServiceError(f"canonical thermochemistry payload lacks {field}")
    return _mapping(raw[field], field)


def _list(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise ApplicationServiceError(f"{label} must be a JSON array")
    return cast(list[object], value)


def _required_string(raw: dict[str, object], field: str) -> str:
    if field not in raw:
        raise ApplicationServiceError(f"canonical thermochemistry payload lacks {field}")
    return _string(raw[field], field)


def _string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ApplicationServiceError(f"{field} must be a non-blank string")
    return value


def _required_number(raw: dict[str, object], field: str) -> float:
    if field not in raw:
        raise ApplicationServiceError(f"canonical thermochemistry payload lacks {field}")
    return _number(raw[field], field)


def _number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ApplicationServiceError(f"{field} must be numeric")
    return float(value)


def _required_int(raw: dict[str, object], field: str) -> int:
    if field not in raw:
        raise ApplicationServiceError(f"canonical thermochemistry payload lacks {field}")
    return _int(raw[field], field)


def _int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ApplicationServiceError(f"{field} must be an integer")
    return value


def _enum(enum_type: type[_EnumT], raw: dict[str, object], field: str) -> _EnumT:
    value = _required_string(raw, field)
    try:
        return cast(_EnumT, enum_type(value))
    except ValueError as error:
        raise ApplicationServiceError(f"{field} has an unsupported canonical value") from error


def _uuid(value: str, field: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as error:
        raise ApplicationServiceError(f"{field} must be a UUID") from error


__all__ = [
    "decode_gas_reference",
    "decode_reference_result",
    "decode_thermochemistry_result",
]

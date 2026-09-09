"""Canonical thermochemistry reopen and reaction-source binding for v1.1 Block 7.

This module reconstructs typed v0.8 thermochemistry objects only from verified ProjectStore
Artifacts. It never accepts user-supplied energies, source hashes, convergence verdicts, or paths.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast
from uuid import UUID

from ecatvasp.api.application import ApplicationServiceError
from ecatvasp.api.thermochemistry_workspace_support import (
    CanonicalAnalysisPayload,
    canonical_analysis_payload,
    observed_artifact_state,
    require_analysis,
    require_artifact,
    thermochemistry_projection,
)
from ecatvasp.domain import (
    Analysis,
    AnalysisId,
    Artifact,
    ArtifactId,
    AtomUid,
    canonical_sha256,
)
from ecatvasp.storage import ProjectBundle, ProjectStore
from ecatvasp.thermo import (
    HARMONIC_THERMOCHEMISTRY_TOOL_NAME,
    HARMONIC_THERMOCHEMISTRY_TOOL_VERSION,
    IDEAL_GAS_THERMOCHEMISTRY_TOOL_NAME,
    IDEAL_GAS_THERMOCHEMISTRY_TOOL_VERSION,
    REFERENCE_CORRECTION_TOOL_NAME,
    REFERENCE_CORRECTION_TOOL_VERSION,
    BoundGasReferenceThermochemistry,
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
    MolecularReferenceReactionSource,
    ReactionSourceArtifactBinding,
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
    ThermochemistryReactionSource,
    ThermochemistryResult,
    ThermochemistrySubjectKind,
    VibrationalModePolicy,
)
from ecatvasp.workflow import (
    ThermochemistryAnalysisScientificState,
    WorkflowStepReadiness,
)


@dataclass(frozen=True, slots=True)
class ResolvedSurfaceReactionSource:
    analysis: Analysis
    artifact: Artifact
    source: ThermochemistryReactionSource
    binding: ReactionSourceArtifactBinding


@dataclass(frozen=True, slots=True)
class ResolvedMolecularReactionSource:
    analysis: Analysis
    artifact: Artifact
    source: MolecularReferenceReactionSource
    binding: ReactionSourceArtifactBinding

    @property
    def raw(self) -> BoundGasReferenceThermochemistry:
        return self.source.raw

    @property
    def corrected(self) -> ReferenceThermochemistryResult | None:
        return self.source.corrected


def resolve_surface_reaction_source(
    *,
    store: ProjectStore,
    analysis_id: AnalysisId,
    species_key: str,
    expected_subject: ThermochemistrySubjectKind,
) -> ResolvedSurfaceReactionSource:
    if expected_subject not in {
        ThermochemistrySubjectKind.SURFACE,
        ThermochemistrySubjectKind.ADSORBATE,
    }:
        raise ApplicationServiceError(
            "surface reaction source subject must be surface/adsorbate"
        )
    bundle = store.open()
    analysis = require_analysis(bundle, analysis_id)
    _require_current_source(store=store, bundle=bundle, analysis=analysis)
    if (analysis.tool, analysis.tool_version) != (
        HARMONIC_THERMOCHEMISTRY_TOOL_NAME,
        HARMONIC_THERMOCHEMISTRY_TOOL_VERSION,
    ):
        raise ApplicationServiceError(
            "surface reaction source must be canonical harmonic thermochemistry"
        )
    canonical = canonical_analysis_payload(
        root=store.root,
        bundle=bundle,
        analysis=analysis,
    )
    result = decode_thermochemistry_result(canonical.payload)
    if result.identity.subject_kind is not expected_subject:
        raise ApplicationServiceError(
            f"reaction role requires {expected_subject.value} thermochemistry"
        )
    source = ThermochemistryReactionSource(species_key=species_key, result=result)
    return ResolvedSurfaceReactionSource(
        analysis=analysis,
        artifact=canonical.artifact,
        source=source,
        binding=ReactionSourceArtifactBinding(
            species_key=species_key,
            analysis=analysis,
            artifact=canonical.artifact,
        ),
    )


def resolve_molecular_reaction_source(
    *,
    store: ProjectStore,
    analysis_id: AnalysisId,
    species_key: str,
    expected_species: GasReferenceSpecies,
) -> ResolvedMolecularReactionSource:
    bundle = store.open()
    analysis = require_analysis(bundle, analysis_id)
    _require_current_source(store=store, bundle=bundle, analysis=analysis)
    if (analysis.tool, analysis.tool_version) == (
        IDEAL_GAS_THERMOCHEMISTRY_TOOL_NAME,
        IDEAL_GAS_THERMOCHEMISTRY_TOOL_VERSION,
    ):
        canonical = canonical_analysis_payload(
            root=store.root,
            bundle=bundle,
            analysis=analysis,
        )
        raw = _decode_bound_gas_reference(canonical)
        corrected = None
    elif (analysis.tool, analysis.tool_version) == (
        REFERENCE_CORRECTION_TOOL_NAME,
        REFERENCE_CORRECTION_TOOL_VERSION,
    ):
        canonical = canonical_analysis_payload(
            root=store.root,
            bundle=bundle,
            analysis=analysis,
        )
        corrected = decode_reference_thermochemistry_result(canonical.payload)
        receipt = _mapping(
            canonical.payload.get("source_receipt"),
            "reference source_receipt",
        )
        raw_analysis_id = _analysis_id(
            receipt.get("source_analysis_id"),
            "source_analysis_id",
        )
        raw_artifact_id = _artifact_id(
            receipt.get("source_artifact_id"),
            "source_artifact_id",
        )
        raw_analysis = require_analysis(bundle, raw_analysis_id)
        raw_artifact = require_artifact(bundle, raw_artifact_id)
        _require_current_source(store=store, bundle=bundle, analysis=raw_analysis)
        if (raw_analysis.tool, raw_analysis.tool_version) != (
            IDEAL_GAS_THERMOCHEMISTRY_TOOL_NAME,
            IDEAL_GAS_THERMOCHEMISTRY_TOOL_VERSION,
        ):
            raise ApplicationServiceError(
                "corrected molecular reference must derive from canonical ideal-gas thermochemistry"
            )
        raw_canonical = canonical_analysis_payload(
            root=store.root,
            bundle=bundle,
            analysis=raw_analysis,
        )
        if raw_canonical.artifact != raw_artifact:
            raise ApplicationServiceError(
                "corrected molecular reference points to another raw Artifact"
            )
        raw = _decode_bound_gas_reference(raw_canonical)
        if corrected.adjustment.reference != raw.reference:
            raise ApplicationServiceError(
                "corrected molecular reference species differs from raw source"
            )
        if corrected.source_result_hash != raw.result.result_hash:
            raise ApplicationServiceError(
                "corrected molecular reference result hash differs from raw source"
            )
        if corrected.source_gibbs_free_energy_ev != raw.result.gibbs_free_energy_ev:
            raise ApplicationServiceError(
                "corrected molecular reference Gibbs energy differs from raw source"
            )
    else:
        raise ApplicationServiceError(
            "molecular reaction source must be raw ideal-gas or corrected reference thermochemistry"
        )
    if raw.reference.species is not expected_species:
        raise ApplicationServiceError(
            f"reaction role requires molecular reference {expected_species.value}"
        )
    source = MolecularReferenceReactionSource(
        species_key=species_key,
        raw=raw,
        corrected=corrected,
    )
    return ResolvedMolecularReactionSource(
        analysis=analysis,
        artifact=canonical.artifact,
        source=source,
        binding=ReactionSourceArtifactBinding(
            species_key=species_key,
            analysis=analysis,
            artifact=canonical.artifact,
        ),
    )


def decode_thermochemistry_result(payload: dict[str, object]) -> ThermochemistryResult:
    result_raw = _mapping(payload.get("result"), "thermochemistry result")
    identity = _decode_identity(
        _mapping(result_raw.get("identity"), "thermochemistry identity")
    )
    components = _decode_components(
        _mapping(result_raw.get("components"), "thermochemistry components")
    )
    selection_raw = result_raw.get("mode_selection")
    selection = (
        None
        if selection_raw is None
        else _decode_mode_selection(
            _mapping(selection_raw, "thermochemistry mode_selection")
        )
    )
    result = ThermochemistryResult(
        identity=identity,
        components=components,
        mode_selection=selection,
    )
    expected_hash = _required_string(result_raw, "result_hash")
    if result.result_hash != expected_hash or payload.get("result_hash") != expected_hash:
        raise ApplicationServiceError(
            "canonical thermochemistry typed result hash differs"
        )
    if canonical_sha256(result) != canonical_sha256(result_raw):
        raise ApplicationServiceError(
            "canonical thermochemistry typed reopen differs from Artifact payload"
        )
    return result


def decode_reference_thermochemistry_result(
    payload: dict[str, object],
) -> ReferenceThermochemistryResult:
    raw = _mapping(payload.get("result"), "reference thermochemistry result")
    result = ReferenceThermochemistryResult(
        adjustment=_decode_adjustment(
            _mapping(raw.get("adjustment"), "reference adjustment")
        ),
        source_result_hash=_required_string(raw, "source_result_hash"),
        source_gibbs_free_energy_ev=_required_float(
            raw,
            "source_gibbs_free_energy_ev",
        ),
    )
    expected_hash = _required_string(raw, "result_hash")
    if result.result_hash != expected_hash or payload.get("result_hash") != expected_hash:
        raise ApplicationServiceError(
            "canonical reference thermochemistry typed result hash differs"
        )
    if canonical_sha256(result) != canonical_sha256(raw):
        raise ApplicationServiceError(
            "canonical reference typed reopen differs from Artifact payload"
        )
    return result


def _require_current_source(
    *,
    store: ProjectStore,
    bundle: ProjectBundle,
    analysis: Analysis,
) -> None:
    observations, invalid_ids = observed_artifact_state(store.root, bundle)
    projection = thermochemistry_projection(
        store=store,
        bundle=bundle,
        analysis=analysis,
        current_hash_overrides=observations,
        invalid_ids=invalid_ids,
    )
    if (
        projection.scientific_state is not ThermochemistryAnalysisScientificState.COMPLETED
        or projection.readiness is not WorkflowStepReadiness.SATISFIED
    ):
        reasons = ",".join(projection.reason_codes) or "unknown"
        raise ApplicationServiceError(
            "reaction source thermochemistry is not current/satisfied: " + reasons
        )


def _decode_bound_gas_reference(
    canonical: CanonicalAnalysisPayload,
) -> BoundGasReferenceThermochemistry:
    receipt = _mapping(canonical.payload.get("source_receipt"), "gas source_receipt")
    reference = _decode_reference(_mapping(receipt.get("reference"), "gas reference"))
    result = decode_thermochemistry_result(canonical.payload)
    if result.identity.subject_kind is not ThermochemistrySubjectKind.GAS:
        raise ApplicationServiceError(
            "ideal-gas reaction source has non-gas thermochemistry result"
        )
    if receipt.get("reference_content_hash") != reference.content_hash:
        raise ApplicationServiceError(
            "gas reference content hash differs from canonical reference"
        )
    return BoundGasReferenceThermochemistry(reference=reference, result=result)


def _decode_identity(raw: dict[str, object]) -> ThermochemistryIdentity:
    vibrational_raw = raw.get("vibrational_policy")
    gas_raw = raw.get("gas_model")
    return ThermochemistryIdentity(
        subject_kind=ThermochemistrySubjectKind(
            _required_string(raw, "subject_kind")
        ),
        conditions=_decode_conditions(_mapping(raw.get("conditions"), "conditions")),
        electronic_energy_kind=ElectronicEnergyKind(
            _required_string(raw, "electronic_energy_kind")
        ),
        electronic_entropy_policy=ElectronicEntropyPolicy(
            _required_string(raw, "electronic_entropy_policy")
        ),
        vibrational_policy=(
            None
            if vibrational_raw is None
            else _decode_vibrational_policy(
                _mapping(vibrational_raw, "vibrational_policy")
            )
        ),
        gas_model=(
            None
            if gas_raw is None
            else _decode_gas_model(_mapping(gas_raw, "gas_model"))
        ),
        corrections=tuple(
            _decode_correction(_mapping(item, "thermochemistry correction"))
            for item in _sequence(raw.get("corrections", []), "corrections")
        ),
    )


def _decode_conditions(raw: dict[str, object]) -> ThermochemicalConditions:
    pressure = raw.get("pressure_pa")
    return ThermochemicalConditions(
        temperature_k=_required_float(raw, "temperature_k"),
        standard_state=ThermochemicalStandardState(
            _required_string(raw, "standard_state")
        ),
        pressure_pa=(
            None if pressure is None else _finite_number(pressure, "pressure_pa")
        ),
    )


def _decode_vibrational_policy(raw: dict[str, object]) -> VibrationalModePolicy:
    return VibrationalModePolicy(
        frequency_cutoff_cm_inverse=_required_float(
            raw,
            "frequency_cutoff_cm_inverse",
        ),
        imaginary_mode_policy=ImaginaryModePolicy(
            _required_string(raw, "imaginary_mode_policy")
        ),
        low_frequency_policy=LowFrequencyPolicy(
            _required_string(raw, "low_frequency_policy")
        ),
        exclusions=tuple(
            _decode_mode_exclusion(_mapping(item, "mode exclusion"))
            for item in _sequence(raw.get("exclusions", []), "exclusions")
        ),
    )


def _decode_mode_exclusion(raw: dict[str, object]) -> ModeExclusion:
    note = raw.get("note")
    return ModeExclusion(
        mode_index=_required_int(raw, "mode_index"),
        reason=ModeExclusionReason(_required_string(raw, "reason")),
        note=None if note is None else _string(note, "note"),
    )


def _decode_gas_model(raw: dict[str, object]) -> GasMoleculeModel:
    return GasMoleculeModel(
        geometry_kind=GasGeometryKind(_required_string(raw, "geometry_kind")),
        symmetry_number=_required_int(raw, "symmetry_number"),
        spin_multiplicity=_required_int(raw, "spin_multiplicity"),
        atomic_masses=tuple(
            _decode_gas_mass(_mapping(item, "gas atomic mass"))
            for item in _sequence(raw.get("atomic_masses"), "atomic_masses")
        ),
    )


def _decode_gas_mass(raw: dict[str, object]) -> GasAtomicMass:
    label = raw.get("isotopologue_label")
    return GasAtomicMass(
        atom_uid=AtomUid(UUID(_required_string(raw, "atom_uid"))),
        mass_amu=_required_float(raw, "mass_amu"),
        isotopologue_label=(
            None if label is None else _string(label, "isotopologue_label")
        ),
    )


def _decode_correction(raw: dict[str, object]) -> ThermochemistryCorrection:
    return ThermochemistryCorrection(
        kind=ThermochemistryCorrectionKind(_required_string(raw, "kind")),
        label=_required_string(raw, "label"),
        value_ev=_required_float(raw, "value_ev"),
        policy_id=_required_string(raw, "policy_id"),
        policy_version=_required_string(raw, "policy_version"),
    )


def _decode_components(raw: dict[str, object]) -> ThermochemistryComponents:
    return ThermochemistryComponents(
        electronic_energy_ev=_required_float(raw, "electronic_energy_ev"),
        zpe_ev=_required_float(raw, "zpe_ev"),
        vibrational_thermal_energy_ev=_required_float(
            raw,
            "vibrational_thermal_energy_ev",
        ),
        translational_thermal_energy_ev=_required_float(
            raw,
            "translational_thermal_energy_ev",
        ),
        rotational_thermal_energy_ev=_required_float(
            raw,
            "rotational_thermal_energy_ev",
        ),
        pv_ev=_required_float(raw, "pv_ev"),
        vibrational_entropy_ev_per_k=_required_float(
            raw,
            "vibrational_entropy_ev_per_k",
        ),
        translational_entropy_ev_per_k=_required_float(
            raw,
            "translational_entropy_ev_per_k",
        ),
        rotational_entropy_ev_per_k=_required_float(
            raw,
            "rotational_entropy_ev_per_k",
        ),
        electronic_entropy_ev_per_k=_required_float(
            raw,
            "electronic_entropy_ev_per_k",
        ),
        corrections=tuple(
            _decode_correction(_mapping(item, "component correction"))
            for item in _sequence(raw.get("corrections", []), "corrections")
        ),
    )


def _decode_mode_selection(raw: dict[str, object]) -> ThermochemistryModeSelection:
    return ThermochemistryModeSelection(
        accepted_mode_indices=tuple(
            _integer(item, "accepted mode index")
            for item in _sequence(
                raw.get("accepted_mode_indices"),
                "accepted_mode_indices",
            )
        ),
        excluded_modes=tuple(
            _decode_mode_exclusion(_mapping(item, "excluded mode"))
            for item in _sequence(raw.get("excluded_modes", []), "excluded_modes")
        ),
    )


def _decode_reference(raw: dict[str, object]) -> GasReferenceDefinition:
    state_label = raw.get("state_label", "electronic_ground_state")
    return GasReferenceDefinition(
        species=GasReferenceSpecies(_required_string(raw, "species")),
        state_label=_string(state_label, "state_label"),
    )


def _decode_adjustment(raw: dict[str, object]) -> GasReferenceAdjustmentIdentity:
    return GasReferenceAdjustmentIdentity(
        reference=_decode_reference(
            _mapping(raw.get("reference"), "adjustment reference")
        ),
        target_phase=ReferencePhase(_required_string(raw, "target_phase")),
        policies=tuple(
            _decode_reference_policy(_mapping(item, "reference correction policy"))
            for item in _sequence(raw.get("policies"), "reference correction policies")
        ),
    )


def _decode_reference_policy(raw: dict[str, object]) -> ReferenceCorrectionPolicy:
    note = raw.get("note")
    return ReferenceCorrectionPolicy(
        correction=_decode_correction(
            _mapping(raw.get("correction"), "reference correction")
        ),
        evidence=_decode_correction_evidence(
            _mapping(raw.get("evidence"), "correction evidence")
        ),
        note=None if note is None else _string(note, "note"),
    )


def _decode_correction_evidence(raw: dict[str, object]) -> CorrectionEvidence:
    citation = raw.get("citation")
    artifact_id = raw.get("artifact_id")
    artifact_sha = raw.get("artifact_sha256")
    return CorrectionEvidence(
        kind=CorrectionEvidenceKind(_required_string(raw, "kind")),
        source_id=_required_string(raw, "source_id"),
        source_version=_required_string(raw, "source_version"),
        citation=None if citation is None else _string(citation, "citation"),
        artifact_id=(
            None
            if artifact_id is None
            else ArtifactId(UUID(_string(artifact_id, "artifact_id")))
        ),
        artifact_sha256=(
            None if artifact_sha is None else _string(artifact_sha, "artifact_sha256")
        ),
    )


def _analysis_id(value: object, field_name: str) -> AnalysisId:
    return AnalysisId(UUID(_string(value, field_name)))


def _artifact_id(value: object, field_name: str) -> ArtifactId:
    return ArtifactId(UUID(_string(value, field_name)))


def _mapping(value: object, field_name: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ApplicationServiceError(
            f"{field_name} must be a string-keyed mapping"
        )
    return cast(dict[str, object], value)


def _sequence(value: object, field_name: str) -> list[object]:
    if not isinstance(value, list):
        raise ApplicationServiceError(f"{field_name} must be a list")
    return cast(list[object], value)


def _string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ApplicationServiceError(f"{field_name} must be a non-blank string")
    return value


def _required_string(raw: dict[str, object], field_name: str) -> str:
    return _string(raw.get(field_name), field_name)


def _finite_number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ApplicationServiceError(f"{field_name} must be numeric")
    result = float(value)
    if result != result or result in {float("inf"), float("-inf")}:
        raise ApplicationServiceError(f"{field_name} must be finite")
    return result


def _required_float(raw: dict[str, object], field_name: str) -> float:
    return _finite_number(raw.get(field_name), field_name)


def _integer(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ApplicationServiceError(f"{field_name} must be an integer")
    return value


def _required_int(raw: dict[str, object], field_name: str) -> int:
    return _integer(raw.get(field_name), field_name)


__all__ = [
    "ResolvedMolecularReactionSource",
    "ResolvedSurfaceReactionSource",
    "decode_reference_thermochemistry_result",
    "decode_thermochemistry_result",
    "resolve_molecular_reaction_source",
    "resolve_surface_reaction_source",
]

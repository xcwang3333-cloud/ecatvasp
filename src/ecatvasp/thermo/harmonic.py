"""Harmonic surface/adsorbate thermochemistry from durable VASP frequency facts.

Block 2 deliberately excludes ideal-gas translation/rotation and gas-reference semantics.
It consumes an exact v0.5 parsed-result Artifact, applies an explicit vibrational policy,
and materializes a component-resolved THERMOCHEMISTRY Analysis/DERIVED_DATASET chain.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from math import exp, expm1, isfinite, log1p
from pathlib import Path, PurePosixPath

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

HARMONIC_THERMOCHEMISTRY_TOOL_NAME = "ecatvasp.thermo.harmonic-surface-adsorbate"
HARMONIC_THERMOCHEMISTRY_TOOL_VERSION = "1"
CANONICAL_HARMONIC_THERMOCHEMISTRY_FORMAT = "ecatvasp-canonical-harmonic-thermochemistry"
CANONICAL_HARMONIC_THERMOCHEMISTRY_VERSION = 1
BOLTZMANN_EV_PER_K = 8.617333262145e-5


class HarmonicThermochemistryError(ValueError):
    """Raised when harmonic thermochemistry cannot be evaluated without guessing."""


@dataclass(frozen=True, slots=True)
class DurableHarmonicThermochemistry:
    """Durable THERMOCHEMISTRY Analysis and its exact derived dataset/provenance graph."""

    analysis: Analysis
    artifact: Artifact
    result: ThermochemistryResult
    provenance_records: tuple[ProvenanceRecord, ...]
    dependency_records: tuple[DependencyRecord, ...]


def calculate_harmonic_thermochemistry(
    *,
    source_result: VaspResultDocument,
    identity: ThermochemistryIdentity,
) -> ThermochemistryResult:
    """Evaluate harmonic surface/adsorbate thermochemistry from immutable parser facts."""

    _validate_block2_identity(identity)
    if source_result.calculation_type is not CalculationType.FREQUENCY:
        raise HarmonicThermochemistryError(
            "Block 2 harmonic thermochemistry requires CalculationType.FREQUENCY"
        )
    frequencies = source_result.frequencies
    if frequencies is None:
        raise HarmonicThermochemistryError("frequency VASP result has no frequency dataset")
    electronic_energy = _electronic_energy(source_result, identity.electronic_energy_kind)
    selection = _select_modes(frequencies.modes, identity)

    temperature = identity.conditions.temperature_k
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

    components = ThermochemistryComponents(
        electronic_energy_ev=electronic_energy,
        zpe_ev=zpe_ev,
        vibrational_thermal_energy_ev=vibrational_thermal_energy_ev,
        vibrational_entropy_ev_per_k=vibrational_entropy_ev_per_k,
    )
    return ThermochemistryResult(
        identity=identity,
        components=components,
        mode_selection=selection,
    )


def materialize_harmonic_thermochemistry(
    *,
    project_root: Path | str,
    calculation: Calculation,
    method_fingerprint: MethodFingerprint,
    structure_snapshot: StructureSnapshot,
    source_analysis: Analysis,
    source_artifact: Artifact,
    source_result: VaspResultDocument,
    identity: ThermochemistryIdentity,
) -> DurableHarmonicThermochemistry:
    """Persist one exact harmonic THERMOCHEMISTRY Analysis from a parsed frequency result."""

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
        raise HarmonicThermochemistryError("project_root must be an existing directory")
    _verify_parsed_result_artifact(
        root=root,
        calculation=calculation,
        source_analysis=source_analysis,
        source_artifact=source_artifact,
        source_result=source_result,
    )
    _validate_frequency_snapshot(source_result=source_result, snapshot=structure_snapshot)
    result = calculate_harmonic_thermochemistry(
        source_result=source_result,
        identity=identity,
    )
    if source_artifact.sha256 is None:
        raise HarmonicThermochemistryError("parsed-result Artifact requires SHA-256")
    source_receipt = {
        "format": CANONICAL_HARMONIC_THERMOCHEMISTRY_FORMAT,
        "version": CANONICAL_HARMONIC_THERMOCHEMISTRY_VERSION,
        "calculation_id": calculation.id,
        "structure_snapshot_id": structure_snapshot.id,
        "method_fingerprint_id": method_fingerprint.id,
        "source_analysis_id": source_analysis.id,
        "source_artifact_id": source_artifact.id,
        "source_artifact_sha256": source_artifact.sha256,
        "source_result_hash": canonical_sha256(source_result),
        "thermochemistry_identity": identity,
        "thermochemistry_parameters_hash": identity.parameters_hash,
    }
    source_receipt_hash = canonical_sha256(source_receipt)
    analysis = Analysis(
        project_id=calculation.project_id,
        analysis_type=AnalysisType.THERMOCHEMISTRY,
        input_artifact_ids=(source_artifact.id,),
        status=AnalysisStatus.COMPLETED,
        tool=HARMONIC_THERMOCHEMISTRY_TOOL_NAME,
        tool_version=HARMONIC_THERMOCHEMISTRY_TOOL_VERSION,
        parameters_hash=source_receipt_hash,
    )
    payload = {
        "format": CANONICAL_HARMONIC_THERMOCHEMISTRY_FORMAT,
        "version": CANONICAL_HARMONIC_THERMOCHEMISTRY_VERSION,
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
            tool=HARMONIC_THERMOCHEMISTRY_TOOL_NAME,
            tool_version=HARMONIC_THERMOCHEMISTRY_TOOL_VERSION,
            parameters_hash=analysis.parameters_hash,
            method_fingerprint_id=method_fingerprint.id,
        ),
        ProvenanceRecord(
            subject_id=artifact.id,
            tool=HARMONIC_THERMOCHEMISTRY_TOOL_NAME,
            tool_version=HARMONIC_THERMOCHEMISTRY_TOOL_VERSION,
            parameters_hash=artifact.sha256,
            method_fingerprint_id=method_fingerprint.id,
        ),
    )
    dependency_records = (
        DependencyRecord(
            upstream_id=calculation.id,
            downstream_id=analysis.id,
            kind=DependencyKind.SCIENTIFIC,
            role="frequency_calculation",
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
            role="frequency_structure_snapshot",
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
            role="parsed_frequency_result",
            recorded_hash=scientific_hash(source_artifact),
        ),
        DependencyRecord(
            upstream_id=analysis.id,
            downstream_id=artifact.id,
            kind=DependencyKind.SCIENTIFIC,
            role="harmonic_thermochemistry",
            recorded_hash=scientific_hash(analysis),
        ),
    )
    return DurableHarmonicThermochemistry(
        analysis=analysis,
        artifact=artifact,
        result=result,
        provenance_records=provenance_records,
        dependency_records=dependency_records,
    )


def _validate_block2_identity(identity: ThermochemistryIdentity) -> None:
    if identity.subject_kind not in {
        ThermochemistrySubjectKind.SURFACE,
        ThermochemistrySubjectKind.ADSORBATE,
    }:
        raise HarmonicThermochemistryError(
            "Block 2 supports only fixed-cell surface/adsorbate thermochemistry"
        )
    if identity.vibrational_policy is None:
        raise HarmonicThermochemistryError("harmonic thermochemistry requires vibrational policy")
    if identity.corrections:
        raise HarmonicThermochemistryError(
            "Block 2 does not apply correction policies; use the explicit correction layer"
        )


def _electronic_energy(
    source_result: VaspResultDocument,
    kind: ElectronicEnergyKind,
) -> float:
    field_by_kind = {
        ElectronicEnergyKind.SIGMA_ZERO: source_result.energies.energy_sigma0_ev,
        ElectronicEnergyKind.WITHOUT_ENTROPY: source_result.energies.energy_without_entropy_ev,
        ElectronicEnergyKind.TOTEN: source_result.energies.free_energy_toten_ev,
    }
    try:
        value = field_by_kind[kind]
    except KeyError as error:
        raise HarmonicThermochemistryError("unsupported electronic-energy semantic") from error
    if value is None:
        raise HarmonicThermochemistryError(
            f"selected VASP electronic-energy semantic is missing: {kind.value}"
        )
    if not isfinite(value):
        raise HarmonicThermochemistryError("selected electronic energy must be finite")
    return value


def _select_modes(
    modes: tuple[VaspFrequencyMode, ...],
    identity: ThermochemistryIdentity,
) -> ThermochemistryModeSelection:
    policy = identity.vibrational_policy
    if policy is None:
        raise HarmonicThermochemistryError("vibrational policy is required")
    by_index = {mode.mode_index: mode for mode in modes}
    exclusion_by_index = {item.mode_index: item for item in policy.exclusions}
    missing = tuple(index for index in exclusion_by_index if index not in by_index)
    if missing:
        raise HarmonicThermochemistryError(
            f"mode exclusions reference absent raw VASP modes: {missing}"
        )
    for exclusion in policy.exclusions:
        mode = by_index[exclusion.mode_index]
        if exclusion.reason in {
            ModeExclusionReason.TRANSLATIONAL,
            ModeExclusionReason.ROTATIONAL,
        }:
            raise HarmonicThermochemistryError(
                "surface/adsorbate harmonic thermochemistry forbids gas "
                "translation/rotation exclusions"
            )
        if (
            exclusion.reason is ModeExclusionReason.IMAGINARY
            and mode.kind is not VaspFrequencyModeKind.IMAGINARY
        ):
            raise HarmonicThermochemistryError(
                "IMAGINARY exclusion must reference an explicitly imaginary VASP mode"
            )
        if exclusion.reason is ModeExclusionReason.LOW_FREQUENCY and not (
            mode.kind is VaspFrequencyModeKind.REAL
            and mode.wavenumber_cm_inverse < policy.frequency_cutoff_cm_inverse
        ):
            raise HarmonicThermochemistryError(
                "LOW_FREQUENCY exclusion must reference a real mode below the cutoff"
            )

    imaginary = tuple(mode for mode in modes if mode.kind is VaspFrequencyModeKind.IMAGINARY)
    if policy.imaginary_mode_policy is ImaginaryModePolicy.REJECT_ANY:
        if imaginary:
            raise HarmonicThermochemistryError("imaginary VASP modes violate REJECT_ANY policy")
    else:
        unexcluded_imaginary = tuple(
            mode.mode_index for mode in imaginary if mode.mode_index not in exclusion_by_index
        )
        if unexcluded_imaginary:
            raise HarmonicThermochemistryError(
                "imaginary VASP modes require explicit exclusions: "
                f"{unexcluded_imaginary}"
            )

    low_real = tuple(
        mode
        for mode in modes
        if mode.kind is VaspFrequencyModeKind.REAL
        and mode.wavenumber_cm_inverse < policy.frequency_cutoff_cm_inverse
    )
    if policy.low_frequency_policy is LowFrequencyPolicy.REJECT_BELOW_CUTOFF:
        if low_real:
            raise HarmonicThermochemistryError("real VASP modes below cutoff violate policy")
    else:
        unexcluded_low = tuple(
            mode.mode_index for mode in low_real if mode.mode_index not in exclusion_by_index
        )
        if unexcluded_low:
            raise HarmonicThermochemistryError(
                f"real VASP modes below cutoff require explicit exclusions: {unexcluded_low}"
            )

    accepted = tuple(
        mode.mode_index
        for mode in modes
        if mode.kind is VaspFrequencyModeKind.REAL
        and mode.wavenumber_cm_inverse >= policy.frequency_cutoff_cm_inverse
        and mode.mode_index not in exclusion_by_index
    )
    if not accepted:
        raise HarmonicThermochemistryError("no accepted harmonic vibrational modes remain")
    for mode_index in accepted:
        mode = by_index[mode_index]
        if not isfinite(mode.energy_mev) or mode.energy_mev <= 0.0:
            raise HarmonicThermochemistryError("accepted harmonic mode requires positive energy")
    return ThermochemistryModeSelection(
        accepted_mode_indices=accepted,
        excluded_modes=policy.exclusions,
    )


def _harmonic_mode_terms(
    *,
    energy_ev: float,
    temperature_k: float,
) -> tuple[float, float, float]:
    if not isfinite(energy_ev) or energy_ev <= 0.0:
        raise HarmonicThermochemistryError("harmonic mode energy must be finite and positive")
    if not isfinite(temperature_k) or temperature_k <= 0.0:
        raise HarmonicThermochemistryError(
            "thermochemistry temperature must be finite and positive"
        )
    x = energy_ev / (BOLTZMANN_EV_PER_K * temperature_k)
    if not isfinite(x) or x <= 0.0:
        raise HarmonicThermochemistryError(
            "dimensionless harmonic frequency must be positive"
        )
    zpe = 0.5 * energy_ev
    if x > 700.0:
        return zpe, 0.0, 0.0
    denominator = expm1(x)
    thermal = energy_ev / denominator
    exp_minus_x = exp(-x)
    entropy = BOLTZMANN_EV_PER_K * (x / denominator - log1p(-exp_minus_x))
    if not all(isfinite(value) and value >= 0.0 for value in (zpe, thermal, entropy)):
        raise HarmonicThermochemistryError(
            "harmonic thermochemistry produced invalid components"
        )
    return zpe, thermal, entropy


def _validate_source_contract(
    *,
    calculation: Calculation,
    method_fingerprint: MethodFingerprint,
    structure_snapshot: StructureSnapshot,
    source_analysis: Analysis,
    source_artifact: Artifact,
    source_result: VaspResultDocument,
) -> None:
    if calculation.calculation_type is not CalculationType.FREQUENCY:
        raise HarmonicThermochemistryError(
            "Block 2 requires a surface/adsorbate FREQUENCY Calculation"
        )
    if calculation.status is not CalculationScientificStatus.CONVERGED:
        raise HarmonicThermochemistryError(
            "frequency Calculation must be scientifically CONVERGED"
        )
    if method_fingerprint.id != calculation.method_fingerprint_id:
        raise HarmonicThermochemistryError(
            "MethodFingerprint differs from Calculation identity"
        )
    if method_fingerprint.recipe.recipe_id != calculation.recipe_id:
        raise HarmonicThermochemistryError(
            "MethodFingerprint recipe differs from Calculation recipe"
        )
    if structure_snapshot.id != calculation.input_structure_snapshot_id:
        raise HarmonicThermochemistryError(
            "StructureSnapshot differs from Calculation input"
        )
    if source_analysis.project_id != calculation.project_id:
        raise HarmonicThermochemistryError(
            "parsed-result Analysis belongs to another project"
        )
    if source_analysis.analysis_type is not AnalysisType.RESULT_PARSE:
        raise HarmonicThermochemistryError("source Analysis must be RESULT_PARSE")
    if source_analysis.status is not AnalysisStatus.COMPLETED:
        raise HarmonicThermochemistryError("parsed-result Analysis must be completed")
    if source_artifact.artifact_type is not ArtifactType.PARSED_RESULT:
        raise HarmonicThermochemistryError("source Artifact must be PARSED_RESULT")
    if (
        not isinstance(source_artifact.producer, AnalysisProducerRef)
        or source_artifact.producer.id != source_analysis.id
    ):
        raise HarmonicThermochemistryError(
            "parsed-result Artifact producer differs from Analysis"
        )
    if source_artifact.availability not in {
        ArtifactAvailability.LOCAL,
        ArtifactAvailability.BOTH,
    }:
        raise HarmonicThermochemistryError(
            "parsed-result Artifact must be locally available"
        )
    if source_artifact.local_path is None:
        raise HarmonicThermochemistryError("parsed-result Artifact requires local_path")
    if source_artifact.sha256 is None or source_artifact.size_bytes is None:
        raise HarmonicThermochemistryError(
            "parsed-result Artifact requires hash and byte size"
        )
    if source_result.calculation_type is not calculation.calculation_type:
        raise HarmonicThermochemistryError(
            "parsed VASP result CalculationType differs"
        )


def _validate_frequency_snapshot(
    *,
    source_result: VaspResultDocument,
    snapshot: StructureSnapshot,
) -> None:
    frequencies = source_result.frequencies
    if frequencies is None:
        raise HarmonicThermochemistryError(
            "parsed VASP result has no frequency dataset"
        )
    snapshot_uids = tuple(site.atom_uid for site in snapshot.sites)
    if set(frequencies.atom_uids) != set(snapshot_uids):
        raise HarmonicThermochemistryError(
            "frequency atom_uids differ from the exact Calculation StructureSnapshot"
        )


def _verify_parsed_result_artifact(
    *,
    root: Path,
    calculation: Calculation,
    source_analysis: Analysis,
    source_artifact: Artifact,
    source_result: VaspResultDocument,
) -> None:
    if source_artifact.local_path is None:
        raise HarmonicThermochemistryError("parsed-result Artifact requires local_path")
    relative = PurePosixPath(source_artifact.local_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise HarmonicThermochemistryError(
            "parsed-result Artifact path must be project-relative"
        )
    absolute = (root / Path(*relative.parts)).resolve()
    if not absolute.is_relative_to(root) or not absolute.is_file():
        raise HarmonicThermochemistryError(
            "parsed-result Artifact file is unavailable"
        )
    body = absolute.read_bytes()
    if source_artifact.size_bytes != len(body):
        raise HarmonicThermochemistryError(
            "parsed-result Artifact byte size differs"
        )
    if source_artifact.sha256 != hashlib.sha256(body).hexdigest():
        raise HarmonicThermochemistryError(
            "parsed-result Artifact SHA-256 differs"
        )
    try:
        payload_raw = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HarmonicThermochemistryError(
            "parsed-result Artifact is not valid UTF-8 JSON"
        ) from error
    payload = _mapping(payload_raw, "parsed-result payload")
    if payload.get("format") != VASP_RESULT_DOCUMENT_FORMAT:
        raise HarmonicThermochemistryError(
            "parsed-result Artifact format is unsupported"
        )
    if payload.get("version") != VASP_RESULT_DOCUMENT_VERSION:
        raise HarmonicThermochemistryError(
            "parsed-result Artifact version is unsupported"
        )
    if payload.get("calculation_id") != str(calculation.id):
        raise HarmonicThermochemistryError(
            "parsed-result Artifact belongs to another Calculation"
        )
    if payload.get("analysis_id") != str(source_analysis.id):
        raise HarmonicThermochemistryError(
            "parsed-result Artifact belongs to another Analysis"
        )
    if canonical_sha256(payload.get("result")) != canonical_sha256(source_result):
        raise HarmonicThermochemistryError(
            "in-memory VASP result differs from durable parsed-result Artifact"
        )


def _write_result_artifact(
    *,
    root: Path,
    analysis: Analysis,
    payload: object,
) -> Artifact:
    relative = (
        Path("analyses")
        / str(analysis.id)
        / "canonical-harmonic-thermochemistry.json"
    )
    absolute = (root / relative).resolve()
    if not absolute.is_relative_to(root):
        raise HarmonicThermochemistryError(
            "thermochemistry output path resolves outside project_root"
        )
    text = canonical_json(payload) + "\n"
    absolute.parent.mkdir(parents=True, exist_ok=True)
    if absolute.exists():
        if not absolute.is_file():
            raise HarmonicThermochemistryError(
                "thermochemistry output path is not a regular file"
            )
        if absolute.read_text(encoding="utf-8") != text:
            raise HarmonicThermochemistryError(
                "thermochemistry output path already has different content"
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


def _mapping(value: object, field_name: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(
        not isinstance(key, str) for key in value
    ):
        raise HarmonicThermochemistryError(
            f"{field_name} must be a JSON object"
        )
    return value

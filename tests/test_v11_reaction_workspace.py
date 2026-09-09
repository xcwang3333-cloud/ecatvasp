from __future__ import annotations

import hashlib
from pathlib import Path

from ecatvasp.api.reaction_workspace import (
    HERPresetAnalysisBindings,
    ProjectReactionWorkspaceApplicationService,
)
from ecatvasp.api.reaction_workspace_support import decode_thermochemistry_result
from ecatvasp.domain import (
    Analysis,
    AnalysisProducerRef,
    AnalysisStatus,
    AnalysisType,
    Artifact,
    ArtifactAvailability,
    ArtifactType,
    Project,
    RetrievalPolicy,
    canonical_json,
    canonical_sha256,
    new_atom_uid,
)
from ecatvasp.storage import ProjectBundle, ProjectStore
from ecatvasp.thermo import (
    CANONICAL_HARMONIC_THERMOCHEMISTRY_FORMAT,
    CANONICAL_HARMONIC_THERMOCHEMISTRY_VERSION,
    CANONICAL_IDEAL_GAS_THERMOCHEMISTRY_FORMAT,
    CANONICAL_IDEAL_GAS_THERMOCHEMISTRY_VERSION,
    HARMONIC_THERMOCHEMISTRY_TOOL_NAME,
    HARMONIC_THERMOCHEMISTRY_TOOL_VERSION,
    IDEAL_GAS_THERMOCHEMISTRY_TOOL_NAME,
    IDEAL_GAS_THERMOCHEMISTRY_TOOL_VERSION,
    CHEConditions,
    CHEPhSemantics,
    ElectrocatalysisPresetKind,
    ElectrodePotentialReference,
    ElectronicEnergyKind,
    ElectronicEntropyPolicy,
    GasAtomicMass,
    GasGeometryKind,
    GasMoleculeModel,
    GasReferenceDefinition,
    GasReferenceSpecies,
    ImaginaryModePolicy,
    LowFrequencyPolicy,
    ThermochemicalConditions,
    ThermochemicalStandardState,
    ThermochemistryComponents,
    ThermochemistryIdentity,
    ThermochemistryModeSelection,
    ThermochemistryResult,
    ThermochemistrySubjectKind,
    VibrationalModePolicy,
)


def _surface_result(subject: ThermochemistrySubjectKind, energy_ev: float) -> ThermochemistryResult:
    identity = ThermochemistryIdentity(
        subject_kind=subject,
        conditions=ThermochemicalConditions(
            temperature_k=298.15,
            standard_state=ThermochemicalStandardState.SURFACE_FIXED_CELL,
        ),
        electronic_energy_kind=ElectronicEnergyKind.SIGMA_ZERO,
        electronic_entropy_policy=ElectronicEntropyPolicy.NEGLECTED,
        vibrational_policy=VibrationalModePolicy(
            frequency_cutoff_cm_inverse=50.0,
            imaginary_mode_policy=ImaginaryModePolicy.REJECT_ANY,
            low_frequency_policy=LowFrequencyPolicy.REJECT_BELOW_CUTOFF,
        ),
    )
    return ThermochemistryResult(
        identity=identity,
        components=ThermochemistryComponents(electronic_energy_ev=energy_ev),
        mode_selection=ThermochemistryModeSelection(accepted_mode_indices=(1,)),
    )


def _h2_result() -> ThermochemistryResult:
    first_uid = new_atom_uid()
    second_uid = new_atom_uid()
    identity = ThermochemistryIdentity(
        subject_kind=ThermochemistrySubjectKind.GAS,
        conditions=ThermochemicalConditions(
            temperature_k=298.15,
            standard_state=ThermochemicalStandardState.IDEAL_GAS_1_BAR,
            pressure_pa=100_000.0,
        ),
        electronic_energy_kind=ElectronicEnergyKind.SIGMA_ZERO,
        electronic_entropy_policy=ElectronicEntropyPolicy.NEGLECTED,
        vibrational_policy=VibrationalModePolicy(
            frequency_cutoff_cm_inverse=50.0,
            imaginary_mode_policy=ImaginaryModePolicy.REJECT_ANY,
            low_frequency_policy=LowFrequencyPolicy.REJECT_BELOW_CUTOFF,
        ),
        gas_model=GasMoleculeModel(
            geometry_kind=GasGeometryKind.LINEAR,
            symmetry_number=2,
            spin_multiplicity=1,
            atomic_masses=(
                GasAtomicMass(first_uid, 1.00784, "1H"),
                GasAtomicMass(second_uid, 1.00784, "1H"),
            ),
        ),
    )
    return ThermochemistryResult(
        identity=identity,
        components=ThermochemistryComponents(electronic_energy_ev=-6.0),
        mode_selection=ThermochemistryModeSelection(accepted_mode_indices=(1,)),
    )


def _write_thermochemistry(
    root: Path,
    project: Project,
    result: ThermochemistryResult,
    *,
    reference: GasReferenceDefinition | None = None,
) -> tuple[Analysis, Artifact]:
    if reference is None:
        tool = HARMONIC_THERMOCHEMISTRY_TOOL_NAME
        tool_version = HARMONIC_THERMOCHEMISTRY_TOOL_VERSION
        format_name = CANONICAL_HARMONIC_THERMOCHEMISTRY_FORMAT
        version = CANONICAL_HARMONIC_THERMOCHEMISTRY_VERSION
        filename = "canonical-harmonic-thermochemistry.json"
        receipt: dict[str, object] = {
            "thermochemistry_parameters_hash": result.identity.parameters_hash,
        }
    else:
        tool = IDEAL_GAS_THERMOCHEMISTRY_TOOL_NAME
        tool_version = IDEAL_GAS_THERMOCHEMISTRY_TOOL_VERSION
        format_name = CANONICAL_IDEAL_GAS_THERMOCHEMISTRY_FORMAT
        version = CANONICAL_IDEAL_GAS_THERMOCHEMISTRY_VERSION
        filename = "canonical-ideal-gas-thermochemistry.json"
        receipt = {
            "reference": reference,
            "reference_content_hash": reference.content_hash,
            "thermochemistry_parameters_hash": result.identity.parameters_hash,
        }
    receipt_hash = canonical_sha256(receipt)
    analysis = Analysis(
        project_id=project.id,
        analysis_type=AnalysisType.THERMOCHEMISTRY,
        input_artifact_ids=(),
        status=AnalysisStatus.COMPLETED,
        tool=tool,
        tool_version=tool_version,
        parameters_hash=receipt_hash,
    )
    payload = {
        "format": format_name,
        "version": version,
        "analysis_id": analysis.id,
        "source_receipt": receipt,
        "source_receipt_hash": receipt_hash,
        "result_hash": result.result_hash,
        "result": result,
    }
    body = (canonical_json(payload) + "\n").encode()
    relative = Path("analyses") / str(analysis.id) / filename
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    artifact = Artifact(
        artifact_type=ArtifactType.DERIVED_DATASET,
        producer=AnalysisProducerRef(analysis.id),
        availability=ArtifactAvailability.LOCAL,
        retrieval_policy=RetrievalPolicy.ALWAYS,
        local_path=relative.as_posix(),
        size_bytes=len(body),
        sha256=hashlib.sha256(body).hexdigest(),
    )
    return analysis, artifact


def _conditions(potential_v: float) -> CHEConditions:
    return CHEConditions(
        temperature_k=298.15,
        potential_v=potential_v,
        ph=0.0,
        potential_reference=ElectrodePotentialReference.SHE,
        ph_semantics=CHEPhSemantics.EXPLICIT_ACTIVITY,
    )


def test_canonical_thermochemistry_typed_reopen_round_trip() -> None:
    result = _surface_result(ThermochemistrySubjectKind.ADSORBATE, -13.0)
    payload = {
        "result_hash": result.result_hash,
        "result": result,
    }
    normalized = __import__("json").loads(canonical_json(payload))
    reopened = decode_thermochemistry_result(normalized)
    assert reopened == result
    assert reopened.result_hash == result.result_hash


def test_her_preview_resolves_only_durable_analysis_sources(tmp_path: Path) -> None:
    project = Project(name="Block 7 reaction preview", slug="block7-reaction-preview")
    clean_analysis, clean_artifact = _write_thermochemistry(
        tmp_path,
        project,
        _surface_result(ThermochemistrySubjectKind.SURFACE, -10.0),
    )
    h_analysis, h_artifact = _write_thermochemistry(
        tmp_path,
        project,
        _surface_result(ThermochemistrySubjectKind.ADSORBATE, -13.0),
    )
    h2_reference = GasReferenceDefinition(GasReferenceSpecies.H2)
    h2_analysis, h2_artifact = _write_thermochemistry(
        tmp_path,
        project,
        _h2_result(),
        reference=h2_reference,
    )
    store = ProjectStore(tmp_path)
    store.save(
        ProjectBundle(
            project=project,
            analyses=(clean_analysis, h_analysis, h2_analysis),
            artifacts=(clean_artifact, h_artifact, h2_artifact),
        )
    )
    service = ProjectReactionWorkspaceApplicationService(store)
    preview = service.preview_preset(
        preset_kind=ElectrocatalysisPresetKind.HER_VOLMER_HEYROVSKY,
        bindings=HERPresetAnalysisBindings(
            clean_surface_analysis_id=clean_analysis.id,
            h_adsorbed_analysis_id=h_analysis.id,
            h2_reference_analysis_id=h2_analysis.id,
        ),
        baseline_conditions=_conditions(0.0),
        requested_conditions=_conditions(-0.2),
    )

    assert preview["preset_kind"] == "her_volmer_heyrovsky"
    assert isinstance(preview["preset_hash"], str)
    potential_view = preview["potential_view"]
    assert isinstance(potential_view, dict)
    assert potential_view["target_conditions"]["potential_v"] == -0.2
    descriptors = preview["descriptor_definitions"]
    assert isinstance(descriptors, list)
    assert {item["kind"] for item in descriptors} == {
        "limiting_potential",
        "reversible_potential",
        "her_delta_g_h_star",
    }

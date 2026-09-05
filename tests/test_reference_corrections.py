from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

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
    new_artifact_id,
    new_atom_uid,
)
from ecatvasp.thermo import (
    CANONICAL_IDEAL_GAS_THERMOCHEMISTRY_FORMAT,
    CANONICAL_IDEAL_GAS_THERMOCHEMISTRY_VERSION,
    IDEAL_GAS_THERMOCHEMISTRY_TOOL_NAME,
    IDEAL_GAS_THERMOCHEMISTRY_TOOL_VERSION,
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
    ReferenceCorrectionError,
    ReferenceCorrectionPolicy,
    ReferencePhase,
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
    apply_reference_corrections,
    materialize_reference_thermochemistry,
)


def _raw_gas_result(species: GasReferenceSpecies) -> ThermochemistryResult:
    if species is GasReferenceSpecies.H2O:
        atom_uids = (new_atom_uid(), new_atom_uid(), new_atom_uid())
        gas_model = GasMoleculeModel(
            geometry_kind=GasGeometryKind.NONLINEAR,
            symmetry_number=2,
            spin_multiplicity=1,
            atomic_masses=(
                GasAtomicMass(atom_uids[0], 15.999, "16O"),
                GasAtomicMass(atom_uids[1], 1.00784, "1H"),
                GasAtomicMass(atom_uids[2], 1.00784, "1H"),
            ),
        )
    else:
        atom_uids = (new_atom_uid(), new_atom_uid())
        mass = 15.999 if species is GasReferenceSpecies.O2 else 1.00784
        multiplicity = 3 if species is GasReferenceSpecies.O2 else 1
        gas_model = GasMoleculeModel(
            geometry_kind=GasGeometryKind.LINEAR,
            symmetry_number=2,
            spin_multiplicity=multiplicity,
            atomic_masses=(
                GasAtomicMass(atom_uids[0], mass),
                GasAtomicMass(atom_uids[1], mass),
            ),
        )
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
        gas_model=gas_model,
    )
    components = ThermochemistryComponents(
        electronic_energy_ev=-10.0,
        zpe_ev=0.10,
        vibrational_thermal_energy_ev=0.01,
        translational_thermal_energy_ev=0.04,
        rotational_thermal_energy_ev=0.02,
        pv_ev=0.025,
        vibrational_entropy_ev_per_k=1.0e-5,
        translational_entropy_ev_per_k=1.0e-3,
        rotational_entropy_ev_per_k=1.0e-4,
    )
    return ThermochemistryResult(
        identity=identity,
        components=components,
        mode_selection=ThermochemistryModeSelection(
            accepted_mode_indices=(1,),
        ),
    )


def _policy(
    *,
    kind: ThermochemistryCorrectionKind,
    label: str,
    value_ev: float,
    policy_id: str,
    policy_version: str = "1",
    evidence_kind: CorrectionEvidenceKind = CorrectionEvidenceKind.USER_DECLARED,
    source_id: str = "synthetic-test-source",
    source_version: str = "1",
    artifact: Artifact | None = None,
) -> ReferenceCorrectionPolicy:
    return ReferenceCorrectionPolicy(
        correction=ThermochemistryCorrection(
            kind=kind,
            label=label,
            value_ev=value_ev,
            policy_id=policy_id,
            policy_version=policy_version,
        ),
        evidence=CorrectionEvidence(
            kind=evidence_kind,
            source_id=source_id,
            source_version=source_version,
            artifact_id=None if artifact is None else artifact.id,
            artifact_sha256=None if artifact is None else artifact.sha256,
        ),
    )


def test_o2_dft_reference_correction_is_explicit_and_additive() -> None:
    source = _raw_gas_result(GasReferenceSpecies.O2)
    policy = _policy(
        kind=ThermochemistryCorrectionKind.DFT_REFERENCE,
        label="synthetic O2 DFT reference correction",
        value_ev=0.25,
        policy_id="synthetic.o2.dft",
    )
    adjustment = GasReferenceAdjustmentIdentity(
        reference=GasReferenceDefinition(GasReferenceSpecies.O2),
        target_phase=ReferencePhase.IDEAL_GAS,
        policies=(policy,),
    )

    corrected = apply_reference_corrections(
        source_result=source,
        adjustment=adjustment,
    )

    assert corrected.source_gibbs_free_energy_ev == source.gibbs_free_energy_ev
    assert corrected.corrected_gibbs_free_energy_ev == pytest.approx(
        source.gibbs_free_energy_ev + 0.25
    )
    assert source.identity.corrections == ()
    assert source.components.corrections == ()


def test_o2_has_no_implicit_default_correction() -> None:
    with pytest.raises(
        ReferenceCorrectionError,
        match="at least one explicit correction policy",
    ):
        GasReferenceAdjustmentIdentity(
            reference=GasReferenceDefinition(GasReferenceSpecies.O2),
            target_phase=ReferencePhase.IDEAL_GAS,
            policies=(),
        )


def test_correction_evidence_changes_analysis_identity() -> None:
    first = GasReferenceAdjustmentIdentity(
        reference=GasReferenceDefinition(GasReferenceSpecies.O2),
        target_phase=ReferencePhase.IDEAL_GAS,
        policies=(
            _policy(
                kind=ThermochemistryCorrectionKind.DFT_REFERENCE,
                label="synthetic correction",
                value_ev=0.2,
                policy_id="synthetic.o2",
                source_version="1",
            ),
        ),
    )
    second = GasReferenceAdjustmentIdentity(
        reference=GasReferenceDefinition(GasReferenceSpecies.O2),
        target_phase=ReferencePhase.IDEAL_GAS,
        policies=(
            _policy(
                kind=ThermochemistryCorrectionKind.DFT_REFERENCE,
                label="synthetic correction",
                value_ev=0.2,
                policy_id="synthetic.o2",
                source_version="2",
            ),
        ),
    )

    assert first.corrections == second.corrections
    assert first.parameters_hash != second.parameters_hash


def test_liquid_water_requires_one_explicit_phase_change_correction() -> None:
    reference = GasReferenceDefinition(GasReferenceSpecies.H2O)
    with pytest.raises(
        ReferenceCorrectionError,
        match="exactly one explicit PHASE_CHANGE",
    ):
        GasReferenceAdjustmentIdentity(
            reference=reference,
            target_phase=ReferencePhase.LIQUID_WATER,
            policies=(
                _policy(
                    kind=ThermochemistryCorrectionKind.EXPERIMENTAL_REFERENCE,
                    label="synthetic water reference",
                    value_ev=-0.1,
                    policy_id="synthetic.h2o.ref",
                ),
            ),
        )

    phase_policy = _policy(
        kind=ThermochemistryCorrectionKind.PHASE_CHANGE,
        label="synthetic gas-to-liquid water correction",
        value_ev=-0.4,
        policy_id="synthetic.h2o.phase",
    )
    liquid = GasReferenceAdjustmentIdentity(
        reference=reference,
        target_phase=ReferencePhase.LIQUID_WATER,
        policies=(phase_policy,),
    )
    corrected = apply_reference_corrections(
        source_result=_raw_gas_result(GasReferenceSpecies.H2O),
        adjustment=liquid,
    )
    assert corrected.corrected_gibbs_free_energy_ev == pytest.approx(
        corrected.source_gibbs_free_energy_ev - 0.4
    )

    with pytest.raises(
        ReferenceCorrectionError,
        match="only valid for the H2O reference",
    ):
        GasReferenceAdjustmentIdentity(
            reference=GasReferenceDefinition(GasReferenceSpecies.O2),
            target_phase=ReferencePhase.LIQUID_WATER,
            policies=(phase_policy,),
        )


def test_ideal_gas_target_rejects_phase_change_policy() -> None:
    with pytest.raises(
        ReferenceCorrectionError,
        match="must not carry a phase-change correction",
    ):
        GasReferenceAdjustmentIdentity(
            reference=GasReferenceDefinition(GasReferenceSpecies.H2O),
            target_phase=ReferencePhase.IDEAL_GAS,
            policies=(
                _policy(
                    kind=ThermochemistryCorrectionKind.PHASE_CHANGE,
                    label="synthetic phase change",
                    value_ev=-0.3,
                    policy_id="synthetic.phase",
                ),
            ),
        )


def test_reference_layer_rejects_already_corrected_parent() -> None:
    source = _raw_gas_result(GasReferenceSpecies.O2)
    correction = ThermochemistryCorrection(
        kind=ThermochemistryCorrectionKind.USER_DECLARED,
        label="preexisting synthetic correction",
        value_ev=0.1,
        policy_id="synthetic.preexisting",
        policy_version="1",
    )
    already_corrected = ThermochemistryResult(
        identity=replace(source.identity, corrections=(correction,)),
        components=replace(source.components, corrections=(correction,)),
        mode_selection=source.mode_selection,
    )
    adjustment = GasReferenceAdjustmentIdentity(
        reference=GasReferenceDefinition(GasReferenceSpecies.O2),
        target_phase=ReferencePhase.IDEAL_GAS,
        policies=(
            _policy(
                kind=ThermochemistryCorrectionKind.DFT_REFERENCE,
                label="second synthetic correction",
                value_ev=0.2,
                policy_id="synthetic.second",
            ),
        ),
    )

    with pytest.raises(
        ReferenceCorrectionError,
        match="only uncorrected raw thermochemistry",
    ):
        apply_reference_corrections(
            source_result=already_corrected,
            adjustment=adjustment,
        )


def _raw_source_artifact(
    *,
    root: Path,
    result: ThermochemistryResult,
    reference: GasReferenceDefinition,
) -> tuple[Analysis, Artifact]:
    source_receipt = {
        "reference": reference,
        "reference_content_hash": reference.content_hash,
    }
    receipt_hash = canonical_sha256(source_receipt)
    analysis = Analysis(
        project_id=new_artifact_id(),
        analysis_type=AnalysisType.THERMOCHEMISTRY,
        input_artifact_ids=(new_artifact_id(),),
        status=AnalysisStatus.COMPLETED,
        tool=IDEAL_GAS_THERMOCHEMISTRY_TOOL_NAME,
        tool_version=IDEAL_GAS_THERMOCHEMISTRY_TOOL_VERSION,
        parameters_hash=receipt_hash,
    )
    relative = Path("analyses") / str(analysis.id) / "canonical-ideal-gas-thermochemistry.json"
    payload = {
        "format": CANONICAL_IDEAL_GAS_THERMOCHEMISTRY_FORMAT,
        "version": CANONICAL_IDEAL_GAS_THERMOCHEMISTRY_VERSION,
        "analysis_id": analysis.id,
        "source_receipt": source_receipt,
        "source_receipt_hash": receipt_hash,
        "result_hash": result.result_hash,
        "result": result,
    }
    body = (canonical_json(payload) + "\n").encode()
    absolute = root / relative
    absolute.parent.mkdir(parents=True)
    absolute.write_bytes(body)
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


def test_materialization_binds_evidence_artifact_and_scientific_dag(
    tmp_path: Path,
) -> None:
    source_result = _raw_gas_result(GasReferenceSpecies.O2)
    reference = GasReferenceDefinition(GasReferenceSpecies.O2)
    source_analysis, source_artifact = _raw_source_artifact(
        root=tmp_path,
        result=source_result,
        reference=reference,
    )
    evidence_artifact = Artifact(
        artifact_type=ArtifactType.DERIVED_DATASET,
        producer=AnalysisProducerRef(source_analysis.id),
        availability=ArtifactAvailability.MISSING,
        retrieval_policy=RetrievalPolicy.ON_DEMAND,
        sha256="e" * 64,
    )
    policy = _policy(
        kind=ThermochemistryCorrectionKind.DFT_REFERENCE,
        label="synthetic evidence-bound correction",
        value_ev=0.15,
        policy_id="synthetic.evidence-bound",
        evidence_kind=CorrectionEvidenceKind.CALIBRATION,
        source_id="synthetic-calibration",
        artifact=evidence_artifact,
    )
    adjustment = GasReferenceAdjustmentIdentity(
        reference=reference,
        target_phase=ReferencePhase.IDEAL_GAS,
        policies=(policy,),
    )

    durable = materialize_reference_thermochemistry(
        project_root=tmp_path,
        source_analysis=source_analysis,
        source_artifact=source_artifact,
        source_result=source_result,
        adjustment=adjustment,
        evidence_artifacts=(evidence_artifact,),
    )

    assert durable.analysis.analysis_type is AnalysisType.THERMOCHEMISTRY
    assert durable.analysis.input_artifact_ids == (
        source_artifact.id,
        evidence_artifact.id,
    )
    assert durable.artifact.artifact_type is ArtifactType.DERIVED_DATASET
    assert durable.artifact.local_path is not None
    assert (tmp_path / durable.artifact.local_path).is_file()
    assert durable.result.corrected_gibbs_free_energy_ev == pytest.approx(
        source_result.gibbs_free_energy_ev + 0.15
    )
    assert {record.role for record in durable.dependency_records} == {
        "raw_gas_thermochemistry_analysis",
        "raw_gas_thermochemistry",
        f"correction_evidence:{evidence_artifact.id}",
        "corrected_reference_thermochemistry",
    }


def test_materialization_rejects_missing_or_mismatched_evidence_binding(
    tmp_path: Path,
) -> None:
    source_result = _raw_gas_result(GasReferenceSpecies.O2)
    reference = GasReferenceDefinition(GasReferenceSpecies.O2)
    source_analysis, source_artifact = _raw_source_artifact(
        root=tmp_path,
        result=source_result,
        reference=reference,
    )
    evidence_artifact = Artifact(
        artifact_type=ArtifactType.DERIVED_DATASET,
        producer=AnalysisProducerRef(source_analysis.id),
        availability=ArtifactAvailability.MISSING,
        sha256="f" * 64,
    )
    adjustment = GasReferenceAdjustmentIdentity(
        reference=reference,
        target_phase=ReferencePhase.IDEAL_GAS,
        policies=(
            _policy(
                kind=ThermochemistryCorrectionKind.DFT_REFERENCE,
                label="synthetic bound correction",
                value_ev=0.11,
                policy_id="synthetic.bound",
                artifact=evidence_artifact,
            ),
        ),
    )

    with pytest.raises(
        ReferenceCorrectionError,
        match="exactly match correction evidence bindings",
    ):
        materialize_reference_thermochemistry(
            project_root=tmp_path,
            source_analysis=source_analysis,
            source_artifact=source_artifact,
            source_result=source_result,
            adjustment=adjustment,
        )

    mismatched = replace(evidence_artifact, sha256="0" * 64)
    with pytest.raises(
        ReferenceCorrectionError,
        match="SHA-256 differs",
    ):
        materialize_reference_thermochemistry(
            project_root=tmp_path,
            source_analysis=source_analysis,
            source_artifact=source_artifact,
            source_result=source_result,
            adjustment=adjustment,
            evidence_artifacts=(mismatched,),
        )

from __future__ import annotations

from dataclasses import replace

import pytest

from ecatvasp.domain import new_atom_uid
from ecatvasp.thermo import (
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
    apply_bound_reference_corrections,
)


def _raw_o2_result() -> ThermochemistryResult:
    atom_uids = (new_atom_uid(), new_atom_uid())
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
            spin_multiplicity=3,
            atomic_masses=(
                GasAtomicMass(atom_uids[0], 15.999, "16O"),
                GasAtomicMass(atom_uids[1], 15.999, "16O"),
            ),
        ),
    )
    return ThermochemistryResult(
        identity=identity,
        components=ThermochemistryComponents(
            electronic_energy_ev=-10.0,
            zpe_ev=0.10,
            vibrational_thermal_energy_ev=0.01,
            translational_thermal_energy_ev=0.04,
            rotational_thermal_energy_ev=0.02,
            pv_ev=0.025,
            vibrational_entropy_ev_per_k=1.0e-5,
            translational_entropy_ev_per_k=1.0e-3,
            rotational_entropy_ev_per_k=1.0e-4,
        ),
        mode_selection=ThermochemistryModeSelection(accepted_mode_indices=(1,)),
    )


def _adjustment(species: GasReferenceSpecies) -> GasReferenceAdjustmentIdentity:
    policy = ReferenceCorrectionPolicy(
        correction=ThermochemistryCorrection(
            kind=ThermochemistryCorrectionKind.DFT_REFERENCE,
            label="synthetic explicit correction",
            value_ev=0.2,
            policy_id="synthetic.reference.binding",
            policy_version="1",
        ),
        evidence=CorrectionEvidence(
            kind=CorrectionEvidenceKind.USER_DECLARED,
            source_id="synthetic-test-source",
            source_version="1",
        ),
    )
    return GasReferenceAdjustmentIdentity(
        reference=GasReferenceDefinition(species),
        target_phase=ReferencePhase.IDEAL_GAS,
        policies=(policy,),
    )


def test_species_bound_reference_correction_is_public_and_additive() -> None:
    raw = _raw_o2_result()
    source = BoundGasReferenceThermochemistry(
        reference=GasReferenceDefinition(GasReferenceSpecies.O2),
        result=raw,
    )

    corrected = apply_bound_reference_corrections(
        source=source,
        adjustment=_adjustment(GasReferenceSpecies.O2),
    )

    assert corrected.source_gibbs_free_energy_ev == raw.gibbs_free_energy_ev
    assert corrected.corrected_gibbs_free_energy_ev == pytest.approx(
        raw.gibbs_free_energy_ev + 0.2
    )
    assert raw.identity.corrections == ()
    assert raw.components.corrections == ()


def test_species_bound_reference_rejects_adjustment_for_different_species() -> None:
    source = BoundGasReferenceThermochemistry(
        reference=GasReferenceDefinition(GasReferenceSpecies.O2),
        result=_raw_o2_result(),
    )

    with pytest.raises(ReferenceCorrectionError, match="species/state differs"):
        apply_bound_reference_corrections(
            source=source,
            adjustment=_adjustment(GasReferenceSpecies.H2),
        )


def test_species_bound_reference_rejects_already_corrected_result() -> None:
    raw = _raw_o2_result()
    correction = ThermochemistryCorrection(
        kind=ThermochemistryCorrectionKind.USER_DECLARED,
        label="preexisting correction",
        value_ev=0.1,
        policy_id="synthetic.preexisting",
        policy_version="1",
    )
    corrected = ThermochemistryResult(
        identity=replace(raw.identity, corrections=(correction,)),
        components=replace(raw.components, corrections=(correction,)),
        mode_selection=raw.mode_selection,
    )

    with pytest.raises(ReferenceCorrectionError, match="uncorrected raw thermochemistry"):
        BoundGasReferenceThermochemistry(
            reference=GasReferenceDefinition(GasReferenceSpecies.O2),
            result=corrected,
        )

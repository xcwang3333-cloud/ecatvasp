from __future__ import annotations

from dataclasses import replace
from math import log

import pytest

from ecatvasp.domain import new_atom_uid
from ecatvasp.thermo import (
    BOLTZMANN_EV_PER_K,
    BoundGasReferenceThermochemistry,
    CHEConditions,
    CHEError,
    CHEHydrogenReference,
    CHEPhSemantics,
    CHEProtonElectronChemicalPotential,
    CorrectionEvidence,
    CorrectionEvidenceKind,
    ElectrodePotentialReference,
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
    apply_bound_reference_corrections,
    proton_electron_chemical_potential,
    rhe_to_she_potential_v,
    she_to_rhe_potential_v,
)


def _raw_gas_result(
    species: GasReferenceSpecies,
    *,
    temperature_k: float = 298.15,
) -> ThermochemistryResult:
    atom_uids = (new_atom_uid(), new_atom_uid())
    if species is GasReferenceSpecies.H2:
        mass = 1.00784
        spin_multiplicity = 1
    elif species is GasReferenceSpecies.O2:
        mass = 15.999
        spin_multiplicity = 3
    else:
        raise AssertionError("synthetic CHE helper only supports H2/O2")
    identity = ThermochemistryIdentity(
        subject_kind=ThermochemistrySubjectKind.GAS,
        conditions=ThermochemicalConditions(
            temperature_k=temperature_k,
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
            spin_multiplicity=spin_multiplicity,
            atomic_masses=(
                GasAtomicMass(atom_uids[0], mass),
                GasAtomicMass(atom_uids[1], mass),
            ),
        ),
    )
    return ThermochemistryResult(
        identity=identity,
        components=ThermochemistryComponents(
            electronic_energy_ev=-6.0,
            zpe_ev=0.20,
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


def _bound_h2(
    *,
    temperature_k: float = 298.15,
) -> BoundGasReferenceThermochemistry:
    return BoundGasReferenceThermochemistry(
        reference=GasReferenceDefinition(GasReferenceSpecies.H2),
        result=_raw_gas_result(
            GasReferenceSpecies.H2,
            temperature_k=temperature_k,
        ),
    )


def _corrected_h2(
    raw: BoundGasReferenceThermochemistry,
    value_ev: float,
) -> ReferenceThermochemistryResult:
    policy = ReferenceCorrectionPolicy(
        correction=ThermochemistryCorrection(
            kind=ThermochemistryCorrectionKind.DFT_REFERENCE,
            label="synthetic H2 correction",
            value_ev=value_ev,
            policy_id="synthetic.h2.reference",
            policy_version="1",
        ),
        evidence=CorrectionEvidence(
            kind=CorrectionEvidenceKind.USER_DECLARED,
            source_id="synthetic-che-test",
            source_version="1",
        ),
    )
    adjustment = GasReferenceAdjustmentIdentity(
        reference=raw.reference,
        target_phase=ReferencePhase.IDEAL_GAS,
        policies=(policy,),
    )
    return apply_bound_reference_corrections(source=raw, adjustment=adjustment)


def test_she_che_uses_explicit_potential_and_ph_terms() -> None:
    hydrogen = CHEHydrogenReference(raw=_bound_h2())
    conditions = CHEConditions(
        temperature_k=298.15,
        potential_v=-0.50,
        ph=7.0,
        potential_reference=ElectrodePotentialReference.SHE,
        ph_semantics=CHEPhSemantics.EXPLICIT_ACTIVITY,
    )

    result = proton_electron_chemical_potential(
        hydrogen_reference=hydrogen,
        conditions=conditions,
    )

    expected_ph = -BOLTZMANN_EV_PER_K * 298.15 * log(10.0) * 7.0
    assert result.half_h2_term_ev == 0.5 * hydrogen.gibbs_free_energy_ev
    assert result.potential_term_ev == 0.50
    assert result.ph_term_ev == pytest.approx(expected_ph)
    assert result.chemical_potential_ev == pytest.approx(
        0.5 * hydrogen.gibbs_free_energy_ev + 0.50 + expected_ph
    )


def test_rhe_che_has_no_second_ph_term() -> None:
    hydrogen = CHEHydrogenReference(raw=_bound_h2())
    first = proton_electron_chemical_potential(
        hydrogen_reference=hydrogen,
        conditions=CHEConditions(
            temperature_k=298.15,
            potential_v=-0.50,
            ph=0.0,
            potential_reference=ElectrodePotentialReference.RHE,
            ph_semantics=CHEPhSemantics.INCLUDED_IN_RHE,
        ),
    )
    second = proton_electron_chemical_potential(
        hydrogen_reference=hydrogen,
        conditions=CHEConditions(
            temperature_k=298.15,
            potential_v=-0.50,
            ph=13.0,
            potential_reference=ElectrodePotentialReference.RHE,
            ph_semantics=CHEPhSemantics.INCLUDED_IN_RHE,
        ),
    )

    assert first.ph_term_ev == 0.0
    assert second.ph_term_ev == 0.0
    assert first.chemical_potential_ev == second.chemical_potential_ev


def test_she_rhe_transform_produces_identical_che_mu() -> None:
    hydrogen = CHEHydrogenReference(raw=_bound_h2())
    potential_rhe = -0.80
    ph = 13.0
    potential_she = rhe_to_she_potential_v(
        potential_rhe_v=potential_rhe,
        ph=ph,
        temperature_k=298.15,
    )
    assert she_to_rhe_potential_v(
        potential_she_v=potential_she,
        ph=ph,
        temperature_k=298.15,
    ) == pytest.approx(potential_rhe)

    she_result = proton_electron_chemical_potential(
        hydrogen_reference=hydrogen,
        conditions=CHEConditions(
            temperature_k=298.15,
            potential_v=potential_she,
            ph=ph,
            potential_reference=ElectrodePotentialReference.SHE,
            ph_semantics=CHEPhSemantics.EXPLICIT_ACTIVITY,
        ),
    )
    rhe_result = proton_electron_chemical_potential(
        hydrogen_reference=hydrogen,
        conditions=CHEConditions(
            temperature_k=298.15,
            potential_v=potential_rhe,
            ph=ph,
            potential_reference=ElectrodePotentialReference.RHE,
            ph_semantics=CHEPhSemantics.INCLUDED_IN_RHE,
        ),
    )

    assert she_result.chemical_potential_ev == pytest.approx(
        rhe_result.chemical_potential_ev
    )


def test_rhe_with_explicit_ph_correction_semantics_is_rejected() -> None:
    with pytest.raises(CHEError, match="must not receive a second pH correction"):
        CHEConditions(
            temperature_k=298.15,
            potential_v=0.0,
            ph=7.0,
            potential_reference=ElectrodePotentialReference.RHE,
            ph_semantics=CHEPhSemantics.EXPLICIT_ACTIVITY,
        )

    with pytest.raises(CHEError, match="SHE potential requires explicit"):
        CHEConditions(
            temperature_k=298.15,
            potential_v=0.0,
            ph=7.0,
            potential_reference=ElectrodePotentialReference.SHE,
            ph_semantics=CHEPhSemantics.INCLUDED_IN_RHE,
        )


def test_che_requires_exact_h2_species_and_temperature() -> None:
    oxygen = BoundGasReferenceThermochemistry(
        reference=GasReferenceDefinition(GasReferenceSpecies.O2),
        result=_raw_gas_result(GasReferenceSpecies.O2),
    )
    with pytest.raises(CHEError, match="requires explicit H2"):
        CHEHydrogenReference(raw=oxygen)

    hydrogen = CHEHydrogenReference(raw=_bound_h2(temperature_k=300.0))
    with pytest.raises(CHEError, match="temperature must exactly match"):
        proton_electron_chemical_potential(
            hydrogen_reference=hydrogen,
            conditions=CHEConditions(
                temperature_k=298.15,
                potential_v=0.0,
                ph=0.0,
                potential_reference=ElectrodePotentialReference.SHE,
                ph_semantics=CHEPhSemantics.EXPLICIT_ACTIVITY,
            ),
        )


def test_che_uses_explicit_corrected_h2_reference_without_mutating_raw() -> None:
    raw = _bound_h2()
    corrected = _corrected_h2(raw, 0.20)
    hydrogen = CHEHydrogenReference(raw=raw, corrected=corrected)
    result = proton_electron_chemical_potential(
        hydrogen_reference=hydrogen,
        conditions=CHEConditions(
            temperature_k=298.15,
            potential_v=0.0,
            ph=0.0,
            potential_reference=ElectrodePotentialReference.SHE,
            ph_semantics=CHEPhSemantics.EXPLICIT_ACTIVITY,
        ),
    )

    assert hydrogen.gibbs_free_energy_ev == pytest.approx(
        raw.result.gibbs_free_energy_ev + 0.20
    )
    assert result.chemical_potential_ev == pytest.approx(
        0.5 * (raw.result.gibbs_free_energy_ev + 0.20)
    )
    assert raw.result.identity.corrections == ()
    assert raw.result.components.corrections == ()


def test_che_rejects_corrected_h2_from_different_raw_source() -> None:
    raw = _bound_h2()
    corrected = _corrected_h2(raw, 0.10)
    forged = replace(corrected, source_result_hash="0" * 64)

    with pytest.raises(CHEError, match="does not derive from the exact raw H2"):
        CHEHydrogenReference(raw=raw, corrected=forged)


def test_che_result_contract_rejects_forged_hash_and_components() -> None:
    hydrogen = CHEHydrogenReference(raw=_bound_h2())
    conditions = CHEConditions(
        temperature_k=298.15,
        potential_v=-0.20,
        ph=3.0,
        potential_reference=ElectrodePotentialReference.SHE,
        ph_semantics=CHEPhSemantics.EXPLICIT_ACTIVITY,
    )
    valid = proton_electron_chemical_potential(
        hydrogen_reference=hydrogen,
        conditions=conditions,
    )

    with pytest.raises(CHEError, match="64-character hexadecimal SHA-256"):
        CHEProtonElectronChemicalPotential(
            conditions=conditions,
            hydrogen_reference_hash="not-a-hash",
            hydrogen_gibbs_free_energy_ev=valid.hydrogen_gibbs_free_energy_ev,
            half_h2_term_ev=valid.half_h2_term_ev,
            potential_term_ev=valid.potential_term_ev,
            ph_term_ev=valid.ph_term_ev,
            chemical_potential_ev=valid.chemical_potential_ev,
        )

    with pytest.raises(CHEError, match="potential term differs"):
        CHEProtonElectronChemicalPotential(
            conditions=conditions,
            hydrogen_reference_hash=valid.hydrogen_reference_hash,
            hydrogen_gibbs_free_energy_ev=valid.hydrogen_gibbs_free_energy_ev,
            half_h2_term_ev=valid.half_h2_term_ev,
            potential_term_ev=valid.potential_term_ev + 0.01,
            ph_term_ev=valid.ph_term_ev,
            chemical_potential_ev=valid.chemical_potential_ev,
        )


def test_che_conditions_reject_nonfinite_values() -> None:
    with pytest.raises(CHEError, match="temperature_k"):
        CHEConditions(
            temperature_k=float("nan"),
            potential_v=0.0,
            ph=0.0,
            potential_reference=ElectrodePotentialReference.SHE,
            ph_semantics=CHEPhSemantics.EXPLICIT_ACTIVITY,
        )
    with pytest.raises(CHEError, match="potential_v"):
        CHEConditions(
            temperature_k=298.15,
            potential_v=float("inf"),
            ph=0.0,
            potential_reference=ElectrodePotentialReference.SHE,
            ph_semantics=CHEPhSemantics.EXPLICIT_ACTIVITY,
        )
    with pytest.raises(CHEError, match="pH"):
        CHEConditions(
            temperature_k=298.15,
            potential_v=0.0,
            ph=float("nan"),
            potential_reference=ElectrodePotentialReference.SHE,
            ph_semantics=CHEPhSemantics.EXPLICIT_ACTIVITY,
        )

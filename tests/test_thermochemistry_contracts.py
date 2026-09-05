from dataclasses import replace

import pytest

from ecatvasp.domain import new_atom_uid
from ecatvasp.thermo import (
    ONE_ATM_PA,
    ONE_BAR_PA,
    ElectronicEnergyKind,
    ElectronicEntropyPolicy,
    GasAtomicMass,
    GasGeometryKind,
    GasMoleculeModel,
    ImaginaryModePolicy,
    LowFrequencyPolicy,
    ModeExclusion,
    ModeExclusionReason,
    ThermochemicalConditions,
    ThermochemicalStandardState,
    ThermochemistryComponents,
    ThermochemistryContractError,
    ThermochemistryCorrection,
    ThermochemistryCorrectionKind,
    ThermochemistryIdentity,
    ThermochemistryModeSelection,
    ThermochemistryResult,
    ThermochemistrySubjectKind,
    VibrationalModePolicy,
)


def _policy() -> VibrationalModePolicy:
    return VibrationalModePolicy(
        frequency_cutoff_cm_inverse=50.0,
        imaginary_mode_policy=ImaginaryModePolicy.EXCLUDE_EXPLICIT,
        low_frequency_policy=LowFrequencyPolicy.EXCLUDE_EXPLICIT,
        exclusions=(
            ModeExclusion(1, ModeExclusionReason.TRANSLATIONAL),
            ModeExclusion(2, ModeExclusionReason.ROTATIONAL),
        ),
    )


def _correction(value_ev: float = -0.10) -> ThermochemistryCorrection:
    return ThermochemistryCorrection(
        kind=ThermochemistryCorrectionKind.DFT_REFERENCE,
        label="explicit reference correction",
        value_ev=value_ev,
        policy_id="test.reference",
        policy_version="1",
    )


def _gas_model() -> GasMoleculeModel:
    return GasMoleculeModel(
        geometry_kind=GasGeometryKind.LINEAR,
        symmetry_number=2,
        spin_multiplicity=1,
        atomic_masses=(
            GasAtomicMass(atom_uid=new_atom_uid(), mass_amu=1.00784),
            GasAtomicMass(atom_uid=new_atom_uid(), mass_amu=1.00784),
        ),
    )


def _gas_identity() -> ThermochemistryIdentity:
    return ThermochemistryIdentity(
        subject_kind=ThermochemistrySubjectKind.GAS,
        conditions=ThermochemicalConditions(
            temperature_k=298.15,
            standard_state=ThermochemicalStandardState.IDEAL_GAS_1_BAR,
            pressure_pa=ONE_BAR_PA,
        ),
        electronic_energy_kind=ElectronicEnergyKind.SIGMA_ZERO,
        electronic_entropy_policy=ElectronicEntropyPolicy.SPIN_DEGENERACY,
        vibrational_policy=_policy(),
        gas_model=_gas_model(),
    )


def test_standard_state_preserves_one_bar_vs_one_atmosphere() -> None:
    one_bar = ThermochemicalConditions(
        temperature_k=298.15,
        standard_state=ThermochemicalStandardState.IDEAL_GAS_1_BAR,
        pressure_pa=ONE_BAR_PA,
    )
    one_atm = ThermochemicalConditions(
        temperature_k=298.15,
        standard_state=ThermochemicalStandardState.IDEAL_GAS_1_ATM,
        pressure_pa=ONE_ATM_PA,
    )

    assert one_bar.standard_pressure_pa == 100_000.0
    assert one_atm.standard_pressure_pa == 101_325.0
    assert one_bar != one_atm


def test_gas_identity_requires_explicit_molecular_metadata() -> None:
    with pytest.raises(ThermochemistryContractError, match="gas_model"):
        ThermochemistryIdentity(
            subject_kind=ThermochemistrySubjectKind.GAS,
            conditions=ThermochemicalConditions(
                temperature_k=298.15,
                standard_state=ThermochemicalStandardState.IDEAL_GAS_1_BAR,
                pressure_pa=ONE_BAR_PA,
            ),
            electronic_energy_kind=ElectronicEnergyKind.SIGMA_ZERO,
            electronic_entropy_policy=ElectronicEntropyPolicy.NEGLECTED,
            vibrational_policy=_policy(),
        )


def test_gas_model_requires_explicit_unique_atomic_masses() -> None:
    atom_uid = new_atom_uid()
    with pytest.raises(ThermochemistryContractError, match="unique atom_uids"):
        GasMoleculeModel(
            geometry_kind=GasGeometryKind.LINEAR,
            symmetry_number=2,
            spin_multiplicity=1,
            atomic_masses=(
                GasAtomicMass(atom_uid=atom_uid, mass_amu=1.00784),
                GasAtomicMass(atom_uid=atom_uid, mass_amu=2.01410, isotopologue_label="2H"),
            ),
        )


def test_adsorbate_rejects_gas_standard_state() -> None:
    with pytest.raises(ThermochemistryContractError, match="SURFACE_FIXED_CELL"):
        ThermochemistryIdentity(
            subject_kind=ThermochemistrySubjectKind.ADSORBATE,
            conditions=ThermochemicalConditions(
                temperature_k=298.15,
                standard_state=ThermochemicalStandardState.IDEAL_GAS_1_BAR,
                pressure_pa=ONE_BAR_PA,
            ),
            electronic_energy_kind=ElectronicEnergyKind.SIGMA_ZERO,
            electronic_entropy_policy=ElectronicEntropyPolicy.NEGLECTED,
            vibrational_policy=_policy(),
        )


def test_reject_any_policy_cannot_hide_imaginary_exclusion() -> None:
    with pytest.raises(ThermochemistryContractError, match="REJECT_ANY"):
        VibrationalModePolicy(
            frequency_cutoff_cm_inverse=25.0,
            imaginary_mode_policy=ImaginaryModePolicy.REJECT_ANY,
            low_frequency_policy=LowFrequencyPolicy.EXCLUDE_EXPLICIT,
            exclusions=(ModeExclusion(3, ModeExclusionReason.IMAGINARY),),
        )


def test_parameters_hash_changes_for_scientifically_relevant_policy() -> None:
    baseline = _gas_identity()
    assert baseline.gas_model is not None
    changed_temperature = replace(
        baseline,
        conditions=replace(baseline.conditions, temperature_k=350.0),
    )
    changed_cutoff = replace(
        baseline,
        vibrational_policy=replace(
            _policy(),
            frequency_cutoff_cm_inverse=75.0,
        ),
    )
    changed_correction = replace(baseline, corrections=(_correction(-0.20),))
    first_mass, second_mass = baseline.gas_model.atomic_masses
    changed_mass = replace(
        baseline,
        gas_model=replace(
            baseline.gas_model,
            atomic_masses=(
                replace(first_mass, mass_amu=2.01410, isotopologue_label="2H"),
                second_mass,
            ),
        ),
    )

    assert baseline.parameters_hash != changed_temperature.parameters_hash
    assert baseline.parameters_hash != changed_cutoff.parameters_hash
    assert baseline.parameters_hash != changed_correction.parameters_hash
    assert baseline.parameters_hash != changed_mass.parameters_hash


def test_mode_selection_cannot_accept_and_exclude_same_mode() -> None:
    with pytest.raises(ThermochemistryContractError, match="both accepted and excluded"):
        ThermochemistryModeSelection(
            accepted_mode_indices=(1, 2, 3),
            excluded_modes=(ModeExclusion(2, ModeExclusionReason.CONSTRAINED),),
        )


def test_result_mode_exclusions_must_match_identity() -> None:
    identity = _gas_identity()
    with pytest.raises(ThermochemistryContractError, match="exactly match"):
        ThermochemistryResult(
            identity=identity,
            components=ThermochemistryComponents(electronic_energy_ev=-10.0),
            mode_selection=ThermochemistryModeSelection(
                accepted_mode_indices=(1, 3, 4),
                excluded_modes=(ModeExclusion(2, ModeExclusionReason.ROTATIONAL),),
            ),
        )


def test_result_preserves_components_and_assembles_gibbs_energy() -> None:
    correction = _correction()
    identity = replace(_gas_identity(), corrections=(correction,))
    components = ThermochemistryComponents(
        electronic_energy_ev=-10.0,
        zpe_ev=0.2,
        vibrational_thermal_energy_ev=0.03,
        translational_thermal_energy_ev=0.04,
        rotational_thermal_energy_ev=0.02,
        pv_ev=0.025,
        vibrational_entropy_ev_per_k=0.00010,
        translational_entropy_ev_per_k=0.00020,
        rotational_entropy_ev_per_k=0.00005,
        electronic_entropy_ev_per_k=0.0,
        corrections=(correction,),
    )
    result = ThermochemistryResult(
        identity=identity,
        components=components,
        mode_selection=ThermochemistryModeSelection(
            accepted_mode_indices=(3, 4, 5, 6),
            excluded_modes=_policy().exclusions,
        ),
    )
    expected = -10.0 + 0.2 + 0.03 + 0.04 + 0.02 + 0.025
    expected -= 298.15 * (0.00010 + 0.00020 + 0.00005)
    expected -= 0.10

    assert result.gibbs_free_energy_ev == pytest.approx(expected)
    assert result.components.total_correction_ev == pytest.approx(-0.10)
    assert len(result.result_hash) == 64


def test_result_corrections_must_match_identity() -> None:
    identity = replace(_gas_identity(), corrections=(_correction(),))
    with pytest.raises(ThermochemistryContractError, match="correction terms"):
        ThermochemistryResult(
            identity=identity,
            components=ThermochemistryComponents(electronic_energy_ev=-10.0),
            mode_selection=ThermochemistryModeSelection(
                accepted_mode_indices=(3, 4, 5),
                excluded_modes=_policy().exclusions,
            ),
        )


def test_surface_without_frequency_policy_cannot_carry_mode_selection() -> None:
    identity = ThermochemistryIdentity(
        subject_kind=ThermochemistrySubjectKind.SURFACE,
        conditions=ThermochemicalConditions(
            temperature_k=298.15,
            standard_state=ThermochemicalStandardState.SURFACE_FIXED_CELL,
        ),
        electronic_energy_kind=ElectronicEnergyKind.SIGMA_ZERO,
        electronic_entropy_policy=ElectronicEntropyPolicy.NEGLECTED,
        vibrational_policy=None,
    )
    with pytest.raises(ThermochemistryContractError, match="requires a vibrational policy"):
        ThermochemistryResult(
            identity=identity,
            components=ThermochemistryComponents(electronic_energy_ev=-20.0),
            mode_selection=ThermochemistryModeSelection(accepted_mode_indices=(1,)),
        )

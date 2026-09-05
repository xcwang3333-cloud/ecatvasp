from __future__ import annotations

from dataclasses import replace

import pytest

from ecatvasp.domain import new_atom_uid
from ecatvasp.thermo import (
    AdsorptionReferenceTerm,
    BoundGasReferenceThermochemistry,
    CHEConditions,
    CHEHydrogenReference,
    CHEPhSemantics,
    CHEReactionSource,
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
    MolecularReferenceReactionSource,
    ReactionFreeEnergyError,
    ReactionPathwayDefinition,
    ReactionStepDefinition,
    StoichiometricTerm,
    ThermochemicalConditions,
    ThermochemicalStandardState,
    ThermochemistryComponents,
    ThermochemistryIdentity,
    ThermochemistryModeSelection,
    ThermochemistryReactionSource,
    ThermochemistryResult,
    ThermochemistrySubjectKind,
    VibrationalModePolicy,
    evaluate_adsorption_free_energy,
    evaluate_reaction_pathway,
    evaluate_reaction_step,
    proton_electron_chemical_potential,
)


def _surface_result(
    energy_ev: float,
    *,
    subject_kind: ThermochemistrySubjectKind = ThermochemistrySubjectKind.SURFACE,
    temperature_k: float = 298.15,
) -> ThermochemistryResult:
    vibrational_policy = None
    mode_selection = None
    if subject_kind is ThermochemistrySubjectKind.ADSORBATE:
        vibrational_policy = VibrationalModePolicy(
            frequency_cutoff_cm_inverse=50.0,
            imaginary_mode_policy=ImaginaryModePolicy.REJECT_ANY,
            low_frequency_policy=LowFrequencyPolicy.REJECT_BELOW_CUTOFF,
        )
        mode_selection = ThermochemistryModeSelection(accepted_mode_indices=(1,))
    identity = ThermochemistryIdentity(
        subject_kind=subject_kind,
        conditions=ThermochemicalConditions(
            temperature_k=temperature_k,
            standard_state=ThermochemicalStandardState.SURFACE_FIXED_CELL,
        ),
        electronic_energy_kind=ElectronicEnergyKind.SIGMA_ZERO,
        electronic_entropy_policy=ElectronicEntropyPolicy.NEGLECTED,
        vibrational_policy=vibrational_policy,
    )
    return ThermochemistryResult(
        identity=identity,
        components=ThermochemistryComponents(electronic_energy_ev=energy_ev),
        mode_selection=mode_selection,
    )


def _raw_gas_result(
    species: GasReferenceSpecies,
    *,
    energy_ev: float,
    temperature_k: float = 298.15,
) -> ThermochemistryResult:
    atom_uids = (new_atom_uid(), new_atom_uid())
    if species is GasReferenceSpecies.H2:
        mass = 1.00784
        multiplicity = 1
    elif species is GasReferenceSpecies.O2:
        mass = 15.999
        multiplicity = 3
    else:
        raise AssertionError("synthetic reaction helper only supports H2/O2")
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
            spin_multiplicity=multiplicity,
            atomic_masses=(
                GasAtomicMass(atom_uids[0], mass),
                GasAtomicMass(atom_uids[1], mass),
            ),
        ),
    )
    return ThermochemistryResult(
        identity=identity,
        components=ThermochemistryComponents(electronic_energy_ev=energy_ev),
        mode_selection=ThermochemistryModeSelection(accepted_mode_indices=(1,)),
    )


def _bound_gas(
    species: GasReferenceSpecies,
    *,
    energy_ev: float,
    temperature_k: float = 298.15,
) -> BoundGasReferenceThermochemistry:
    return BoundGasReferenceThermochemistry(
        reference=GasReferenceDefinition(species),
        result=_raw_gas_result(
            species,
            energy_ev=energy_ev,
            temperature_k=temperature_k,
        ),
    )


def _che_source(
    species_key: str,
    *,
    potential_v: float,
    ph: float = 0.0,
) -> CHEReactionSource:
    raw_h2 = _bound_gas(GasReferenceSpecies.H2, energy_ev=-6.0)
    hydrogen = CHEHydrogenReference(raw=raw_h2)
    result = proton_electron_chemical_potential(
        hydrogen_reference=hydrogen,
        conditions=CHEConditions(
            temperature_k=298.15,
            potential_v=potential_v,
            ph=ph,
            potential_reference=ElectrodePotentialReference.RHE,
            ph_semantics=CHEPhSemantics.INCLUDED_IN_RHE,
        ),
    )
    return CHEReactionSource(species_key=species_key, result=result)


def test_generic_signed_stoichiometry_uses_products_positive_reactants_negative() -> None:
    clean = ThermochemistryReactionSource(
        species_key="surface",
        result=_surface_result(-10.0),
    )
    adsorbed = ThermochemistryReactionSource(
        species_key="surface_O",
        result=_surface_result(
            -11.25,
            subject_kind=ThermochemistrySubjectKind.ADSORBATE,
        ),
    )
    definition = ReactionStepDefinition(
        step_key="adsorb",
        label="surface to adsorbed state",
        initial_state_key="clean",
        final_state_key="adsorbed",
        terms=(
            StoichiometricTerm("surface", -1.0),
            StoichiometricTerm("surface_O", 1.0),
        ),
    )

    result = evaluate_reaction_step(
        definition=definition,
        sources=(clean, adsorbed),
    )

    assert result.delta_g_ev == pytest.approx(-1.25)
    contribution_by_key = {
        item.species_key: item.contribution_ev for item in result.contributions
    }
    assert contribution_by_key == {
        "surface": pytest.approx(10.0),
        "surface_O": pytest.approx(-11.25),
    }


def test_fractional_molecular_reference_stoichiometry_is_supported() -> None:
    clean = ThermochemistryReactionSource("surface", _surface_result(-10.0))
    adsorbed = ThermochemistryReactionSource(
        "surface_O",
        _surface_result(-12.0, subject_kind=ThermochemistrySubjectKind.ADSORBATE),
    )
    oxygen = MolecularReferenceReactionSource(
        species_key="O2",
        raw=_bound_gas(GasReferenceSpecies.O2, energy_ev=-4.0),
    )
    definition = ReactionStepDefinition(
        step_key="oxygen_adsorption",
        label="half oxygen adsorption",
        initial_state_key="clean",
        final_state_key="oxygenated",
        terms=(
            StoichiometricTerm("surface", -1.0),
            StoichiometricTerm("O2", -0.5),
            StoichiometricTerm("surface_O", 1.0),
        ),
    )

    result = evaluate_reaction_step(
        definition=definition,
        sources=(clean, adsorbed, oxygen),
    )

    assert result.delta_g_ev == pytest.approx(0.0)


def test_che_potential_sign_is_derived_only_from_stoichiometric_coefficient() -> None:
    clean = ThermochemistryReactionSource("surface", _surface_result(-10.0))
    adsorbed = ThermochemistryReactionSource(
        "surface_H",
        _surface_result(-12.0, subject_kind=ThermochemistrySubjectKind.ADSORBATE),
    )
    definition = ReactionStepDefinition(
        step_key="hydrogenation",
        label="explicit proton electron consumption",
        initial_state_key="clean",
        final_state_key="hydrogenated",
        terms=(
            StoichiometricTerm("surface", -1.0),
            StoichiometricTerm("H+e-", -1.0),
            StoichiometricTerm("surface_H", 1.0),
        ),
    )
    at_zero = evaluate_reaction_step(
        definition=definition,
        sources=(clean, adsorbed, _che_source("H+e-", potential_v=0.0)),
    )
    at_half_volt = evaluate_reaction_step(
        definition=definition,
        sources=(clean, adsorbed, _che_source("H+e-", potential_v=0.5)),
    )

    assert at_half_volt.delta_g_ev - at_zero.delta_g_ev == pytest.approx(0.5)


def test_exact_source_registry_rejects_missing_extra_and_duplicate_sources() -> None:
    clean = ThermochemistryReactionSource("surface", _surface_result(-10.0))
    adsorbed = ThermochemistryReactionSource(
        "surface_X",
        _surface_result(-11.0, subject_kind=ThermochemistrySubjectKind.ADSORBATE),
    )
    extra = ThermochemistryReactionSource("unused", _surface_result(-9.0))
    definition = ReactionStepDefinition(
        step_key="step",
        label="exact registry",
        initial_state_key="a",
        final_state_key="b",
        terms=(
            StoichiometricTerm("surface", -1.0),
            StoichiometricTerm("surface_X", 1.0),
        ),
    )

    with pytest.raises(ReactionFreeEnergyError, match="exactly match stoichiometry"):
        evaluate_reaction_step(definition=definition, sources=(clean,))
    with pytest.raises(ReactionFreeEnergyError, match="exactly match stoichiometry"):
        evaluate_reaction_step(
            definition=definition,
            sources=(clean, adsorbed, extra),
        )
    with pytest.raises(ReactionFreeEnergyError, match="species_keys must be unique"):
        evaluate_reaction_step(
            definition=definition,
            sources=(clean, adsorbed, replace(clean)),
        )


def test_reaction_sources_require_exact_temperature_and_che_conditions() -> None:
    cold = ThermochemistryReactionSource("cold", _surface_result(-10.0))
    hot = ThermochemistryReactionSource(
        "hot",
        _surface_result(-11.0, temperature_k=300.0),
    )
    temperature_step = ReactionStepDefinition(
        step_key="temperature_mismatch",
        label="temperature mismatch",
        initial_state_key="cold",
        final_state_key="hot",
        terms=(StoichiometricTerm("cold", -1.0), StoichiometricTerm("hot", 1.0)),
    )
    with pytest.raises(ReactionFreeEnergyError, match="exact same temperature"):
        evaluate_reaction_step(
            definition=temperature_step,
            sources=(cold, hot),
        )

    che_a = _che_source("reservoir_a", potential_v=0.0)
    che_b = _che_source("reservoir_b", potential_v=0.2)
    che_step = ReactionStepDefinition(
        step_key="che_condition_mismatch",
        label="CHE mismatch",
        initial_state_key="a",
        final_state_key="b",
        terms=(
            StoichiometricTerm("reservoir_a", -1.0),
            StoichiometricTerm("reservoir_b", 1.0),
        ),
    )
    with pytest.raises(ReactionFreeEnergyError, match="share exact conditions"):
        evaluate_reaction_step(
            definition=che_step,
            sources=(che_a, che_b),
        )


def test_raw_gas_cannot_bypass_molecular_reference_binding() -> None:
    gas_result = _raw_gas_result(GasReferenceSpecies.O2, energy_ev=-4.0)
    with pytest.raises(ReactionFreeEnergyError, match="MolecularReferenceReactionSource"):
        ThermochemistryReactionSource(species_key="O2", result=gas_result)


def test_directed_pathway_requires_explicit_adjacent_state_order_and_accumulates() -> None:
    state_a = ThermochemistryReactionSource("A", _surface_result(-10.0))
    state_b = ThermochemistryReactionSource(
        "B",
        _surface_result(-9.5, subject_kind=ThermochemistrySubjectKind.ADSORBATE),
    )
    state_c = ThermochemistryReactionSource(
        "C",
        _surface_result(-10.25, subject_kind=ThermochemistrySubjectKind.ADSORBATE),
    )
    first = ReactionStepDefinition(
        step_key="a_to_b",
        label="A to B",
        initial_state_key="A_state",
        final_state_key="B_state",
        terms=(StoichiometricTerm("A", -1.0), StoichiometricTerm("B", 1.0)),
    )
    second = ReactionStepDefinition(
        step_key="b_to_c",
        label="B to C",
        initial_state_key="B_state",
        final_state_key="C_state",
        terms=(StoichiometricTerm("B", -1.0), StoichiometricTerm("C", 1.0)),
    )
    pathway = ReactionPathwayDefinition(
        pathway_key="generic_path",
        label="generic directed path",
        state_keys=("A_state", "B_state", "C_state"),
        steps=(first, second),
    )

    result = evaluate_reaction_pathway(
        definition=pathway,
        sources=(state_a, state_b, state_c),
    )

    assert tuple(item.delta_g_ev for item in result.step_results) == pytest.approx(
        (0.5, -0.75)
    )
    assert result.cumulative_state_free_energies_ev == pytest.approx((0.0, 0.5, -0.25))

    with pytest.raises(ReactionFreeEnergyError, match="initial_state_key"):
        ReactionPathwayDefinition(
            pathway_key="not_a_string_reverse",
            label="invalid reversed ordering",
            state_keys=("C_state", "B_state", "A_state"),
            steps=(first, second),
        )


def test_adsorption_helper_compiles_to_the_same_generic_stoichiometric_step() -> None:
    clean = ThermochemistryReactionSource("surface", _surface_result(-10.0))
    adsorbed = ThermochemistryReactionSource(
        "surface_H",
        _surface_result(-11.5, subject_kind=ThermochemistrySubjectKind.ADSORBATE),
    )
    hydrogen = MolecularReferenceReactionSource(
        species_key="H2",
        raw=_bound_gas(GasReferenceSpecies.H2, energy_ev=-6.0),
    )

    helper_result = evaluate_adsorption_free_energy(
        step_key="h_adsorption",
        label="H adsorption",
        initial_state_key="clean",
        final_state_key="H_star",
        adsorbed_source=adsorbed,
        clean_surface_source=clean,
        reference_terms=(AdsorptionReferenceTerm(hydrogen, -0.5),),
    )
    manual_definition = ReactionStepDefinition(
        step_key="h_adsorption",
        label="H adsorption",
        initial_state_key="clean",
        final_state_key="H_star",
        terms=(
            StoichiometricTerm("surface", -1.0),
            StoichiometricTerm("H2", -0.5),
            StoichiometricTerm("surface_H", 1.0),
        ),
    )
    manual_result = evaluate_reaction_step(
        definition=manual_definition,
        sources=(clean, hydrogen, adsorbed),
    )

    assert helper_result.delta_g_ev == pytest.approx(1.5)
    assert helper_result.definition_hash == manual_result.definition_hash
    assert helper_result.result_hash == manual_result.result_hash


def test_invalid_stoichiometry_fails_closed() -> None:
    with pytest.raises(ReactionFreeEnergyError, match="finite and non-zero"):
        StoichiometricTerm("A", 0.0)
    with pytest.raises(ReactionFreeEnergyError, match="aggregated coefficient"):
        ReactionStepDefinition(
            step_key="duplicate",
            label="duplicate species",
            initial_state_key="a",
            final_state_key="b",
            terms=(StoichiometricTerm("A", -1.0), StoichiometricTerm("A", 1.0)),
        )
    with pytest.raises(ReactionFreeEnergyError, match="at least one product"):
        ReactionStepDefinition(
            step_key="no_product",
            label="invalid",
            initial_state_key="a",
            final_state_key="b",
            terms=(StoichiometricTerm("A", -1.0),),
        )

from __future__ import annotations

import pytest

from ecatvasp.thermo.presets import (
    ElectrocatalysisPresetError,
    ElectrocatalysisPresetKind,
    ElectrocatalysisReactionFamily,
    compile_co2rr_to_co_2e_preset,
    compile_her_volmer_heyrovsky_preset,
    compile_oer_associative_4e_preset,
    compile_orr_associative_4e_preset,
)


def _net_stoichiometry(preset: object) -> dict[str, float]:
    definition = preset.definition  # type: ignore[attr-defined]
    totals: dict[str, float] = {}
    for step in definition.steps:
        for term in step.terms:
            totals[term.species_key] = totals.get(term.species_key, 0.0) + term.coefficient
    return {key: value for key, value in totals.items() if value != 0.0}


def test_her_preset_compiles_volmer_heyrovsky_into_generic_stoichiometry() -> None:
    preset = compile_her_volmer_heyrovsky_preset(
        pathway_key="her",
        clean_surface_key="star",
        h_adsorbed_key="H_star",
        h2_reference_key="H2",
        che_key="H_plus_e",
    )

    assert preset.family is ElectrocatalysisReactionFamily.HER
    assert preset.kind is ElectrocatalysisPresetKind.HER_VOLMER_HEYROVSKY
    assert preset.definition.state_keys == ("star", "H_star", "star")
    assert _net_stoichiometry(preset) == {"H2": 1.0, "H_plus_e": -2.0}
    assert preset.required_species_keys == ("H2", "H_plus_e", "H_star", "star")


def test_orr_preset_has_exact_four_electron_water_stoichiometry() -> None:
    preset = compile_orr_associative_4e_preset(
        pathway_key="orr",
        clean_surface_key="star",
        ooh_adsorbed_key="OOH_star",
        o_adsorbed_key="O_star",
        oh_adsorbed_key="OH_star",
        o2_reference_key="O2",
        h2o_reference_key="H2O",
        che_key="H_plus_e",
    )

    assert preset.family is ElectrocatalysisReactionFamily.ORR
    assert preset.kind is ElectrocatalysisPresetKind.ORR_ASSOCIATIVE_4E
    assert _net_stoichiometry(preset) == {
        "H2O": 2.0,
        "H_plus_e": -4.0,
        "O2": -1.0,
    }
    assert tuple(step.step_key for step in preset.definition.steps) == (
        "orr_ooh_formation",
        "orr_o_formation",
        "orr_oh_formation",
        "orr_water_release",
    )


def test_oer_is_an_explicit_oxidation_pathway_not_an_orr_string_reversal() -> None:
    preset = compile_oer_associative_4e_preset(
        pathway_key="oer",
        clean_surface_key="star",
        oh_adsorbed_key="OH_star",
        o_adsorbed_key="O_star",
        ooh_adsorbed_key="OOH_star",
        h2o_reference_key="H2O",
        o2_reference_key="O2",
        che_key="H_plus_e",
    )

    assert preset.family is ElectrocatalysisReactionFamily.OER
    assert preset.kind is ElectrocatalysisPresetKind.OER_ASSOCIATIVE_4E
    assert _net_stoichiometry(preset) == {
        "H2O": -2.0,
        "H_plus_e": 4.0,
        "O2": 1.0,
    }
    first_step = preset.definition.steps[0]
    first_terms = {term.species_key: term.coefficient for term in first_step.terms}
    assert first_step.step_key == "oer_oh_formation"
    assert first_terms == {
        "H2O": -1.0,
        "H_plus_e": 1.0,
        "OH_star": 1.0,
        "star": -1.0,
    }


def test_co2_to_co_preset_retains_co_desorption_as_potential_independent_step() -> None:
    preset = compile_co2rr_to_co_2e_preset(
        pathway_key="co2_to_co",
        clean_surface_key="star",
        cooh_adsorbed_key="COOH_star",
        co_adsorbed_key="CO_star",
        co2_reference_key="CO2",
        h2o_reference_key="H2O",
        co_reference_key="CO_gas",
        che_key="H_plus_e",
    )

    assert preset.family is ElectrocatalysisReactionFamily.CO2RR_TO_CO
    assert preset.kind is ElectrocatalysisPresetKind.CO2RR_TO_CO_2E
    assert _net_stoichiometry(preset) == {
        "CO2": -1.0,
        "CO_gas": 1.0,
        "H2O": 1.0,
        "H_plus_e": -2.0,
    }
    release = preset.definition.steps[-1]
    assert release.step_key == "co2rr_co_release"
    assert all(term.species_key != "H_plus_e" for term in release.terms)


def test_preset_rejects_scientific_role_key_collision() -> None:
    with pytest.raises(ElectrocatalysisPresetError, match="must be distinct"):
        compile_her_volmer_heyrovsky_preset(
            pathway_key="her",
            clean_surface_key="same",
            h_adsorbed_key="same",
            h2_reference_key="H2",
            che_key="H_plus_e",
        )

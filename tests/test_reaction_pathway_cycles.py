from __future__ import annotations

import pytest

from ecatvasp.thermo import (
    ReactionEnergySourceKind,
    ReactionFreeEnergyError,
    ReactionPathwayDefinition,
    ReactionPathwayResult,
    ReactionStepDefinition,
    ReactionStepResult,
    ReactionTermContribution,
    StoichiometricTerm,
)


def test_closed_catalytic_pathway_may_return_to_the_initial_state() -> None:
    adsorption = ReactionStepDefinition(
        step_key="adsorb",
        label="adsorb",
        initial_state_key="clean",
        final_state_key="intermediate",
        terms=(
            StoichiometricTerm("clean_species", -1.0),
            StoichiometricTerm("intermediate_species", 1.0),
        ),
    )
    release = ReactionStepDefinition(
        step_key="release",
        label="release product and regenerate catalyst",
        initial_state_key="intermediate",
        final_state_key="clean",
        terms=(
            StoichiometricTerm("intermediate_species", -1.0),
            StoichiometricTerm("clean_species", 1.0),
        ),
    )

    pathway = ReactionPathwayDefinition(
        pathway_key="closed_cycle",
        label="closed catalytic cycle",
        state_keys=("clean", "intermediate", "clean"),
        steps=(adsorption, release),
    )

    assert pathway.state_keys[0] == pathway.state_keys[-1] == "clean"
    assert len(pathway.content_hash) == 64


def _step_result(
    *,
    step_key: str,
    initial_state_key: str,
    final_state_key: str,
    temperature_k: float,
    condition_hash: str | None,
) -> ReactionStepResult:
    contribution = ReactionTermContribution(
        species_key=step_key,
        coefficient=1.0,
        source_kind=ReactionEnergySourceKind.THERMOCHEMISTRY,
        source_hash="0" * 64,
        gibbs_free_energy_ev=0.0,
        contribution_ev=0.0,
    )
    return ReactionStepResult(
        definition_hash="1" * 64,
        step_key=step_key,
        initial_state_key=initial_state_key,
        final_state_key=final_state_key,
        temperature_k=temperature_k,
        electrochemical_condition_hash=condition_hash,
        contributions=(contribution,),
        delta_g_ev=0.0,
    )


def test_pathway_result_rejects_mixed_step_temperatures() -> None:
    first = _step_result(
        step_key="first",
        initial_state_key="a",
        final_state_key="b",
        temperature_k=298.15,
        condition_hash=None,
    )
    second = _step_result(
        step_key="second",
        initial_state_key="b",
        final_state_key="a",
        temperature_k=300.0,
        condition_hash=None,
    )

    with pytest.raises(ReactionFreeEnergyError, match="exact same temperature"):
        ReactionPathwayResult(
            pathway_hash="2" * 64,
            state_keys=("a", "b", "a"),
            step_results=(first, second),
            cumulative_state_free_energies_ev=(0.0, 0.0, 0.0),
        )


def test_pathway_result_rejects_mixed_che_conditions() -> None:
    first = _step_result(
        step_key="first",
        initial_state_key="a",
        final_state_key="b",
        temperature_k=298.15,
        condition_hash="a" * 64,
    )
    second = _step_result(
        step_key="second",
        initial_state_key="b",
        final_state_key="a",
        temperature_k=298.15,
        condition_hash="b" * 64,
    )

    with pytest.raises(ReactionFreeEnergyError, match="exact electrochemical conditions"):
        ReactionPathwayResult(
            pathway_hash="2" * 64,
            state_keys=("a", "b", "a"),
            step_results=(first, second),
            cumulative_state_free_energies_ev=(0.0, 0.0, 0.0),
        )

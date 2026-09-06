from __future__ import annotations

import pytest

from ecatvasp.thermo import (
    CHEConditions,
    CHEPhSemantics,
    ElectrocatalysisDescriptorError,
    ElectrodePotentialReference,
    OEROverpotentialResult,
    ReactionEnergySourceKind,
    ReactionPathwayResult,
    ReactionStepResult,
    ReactionTermContribution,
    derive_reversible_potential,
    solve_limiting_potential,
)


def test_direct_oer_result_rejects_reduction_like_pathway_even_at_zero_gap() -> None:
    conditions = CHEConditions(
        temperature_k=298.15,
        potential_v=0.0,
        ph=0.0,
        potential_reference=ElectrodePotentialReference.RHE,
        ph_semantics=CHEPhSemantics.INCLUDED_IN_RHE,
    )
    contributions = (
        ReactionTermContribution(
            species_key="reaction_energy",
            coefficient=1.0,
            source_kind=ReactionEnergySourceKind.THERMOCHEMISTRY,
            source_hash="1" * 64,
            gibbs_free_energy_ev=0.40,
            contribution_ev=0.40,
        ),
        ReactionTermContribution(
            species_key="H_plus_e",
            coefficient=-1.0,
            source_kind=ReactionEnergySourceKind.CHE_RESERVOIR,
            source_hash="2" * 64,
            gibbs_free_energy_ev=0.0,
            contribution_ev=0.0,
        ),
    )
    step = ReactionStepResult(
        definition_hash="3" * 64,
        step_key="reduction",
        initial_state_key="a",
        final_state_key="b",
        temperature_k=298.15,
        electrochemical_condition_hash=conditions.parameters_hash,
        contributions=contributions,
        delta_g_ev=0.40,
    )
    pathway = ReactionPathwayResult(
        pathway_hash="4" * 64,
        state_keys=("a", "b"),
        step_results=(step,),
        cumulative_state_free_energies_ev=(0.0, 0.40),
    )
    limiting = solve_limiting_potential(
        baseline_pathway=pathway,
        baseline_conditions=conditions,
    )
    reversible = derive_reversible_potential(
        baseline_pathway=pathway,
        baseline_conditions=conditions,
    )

    assert limiting.limiting_potential_v == pytest.approx(-0.40)
    assert reversible.reversible_potential_v == pytest.approx(-0.40)
    with pytest.raises(ElectrocatalysisDescriptorError, match="net production"):
        OEROverpotentialResult(
            limiting=limiting,
            reversible=reversible,
            overpotential_v=0.0,
        )

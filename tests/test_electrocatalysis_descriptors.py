from __future__ import annotations

from math import log

import pytest

from ecatvasp.domain import new_atom_uid
from ecatvasp.thermo import (
    BOLTZMANN_EV_PER_K,
    BoundGasReferenceThermochemistry,
    CHEConditions,
    CHEPhSemantics,
    ElectrocatalysisDescriptorError,
    ElectrodePotentialReference,
    ElectronicEnergyKind,
    ElectronicEntropyPolicy,
    GasAtomicMass,
    GasGeometryKind,
    GasMoleculeModel,
    GasReferenceDefinition,
    GasReferenceSpecies,
    HERDeltaGHStarResult,
    ImaginaryModePolicy,
    LimitingPotentialSelection,
    LowFrequencyPolicy,
    MolecularReferenceReactionSource,
    ReactionEnergySourceKind,
    ReactionPathwayResult,
    ReactionStepResult,
    ReactionTermContribution,
    ThermochemicalConditions,
    ThermochemicalStandardState,
    ThermochemistryComponents,
    ThermochemistryIdentity,
    ThermochemistryModeSelection,
    ThermochemistryReactionSource,
    ThermochemistryResult,
    ThermochemistrySubjectKind,
    VibrationalModePolicy,
    derive_reversible_potential,
    evaluate_her_delta_g_h_star,
    evaluate_oer_theoretical_overpotential,
    evaluate_potential_dependent_pathway_view,
    rhe_to_she_potential_v,
    solve_limiting_potential,
)


def _rhe_conditions(*, potential_v: float, ph: float = 0.0) -> CHEConditions:
    return CHEConditions(
        temperature_k=298.15,
        potential_v=potential_v,
        ph=ph,
        potential_reference=ElectrodePotentialReference.RHE,
        ph_semantics=CHEPhSemantics.INCLUDED_IN_RHE,
    )


def _synthetic_step(
    *,
    step_key: str,
    initial_state_key: str,
    final_state_key: str,
    delta_g_ev: float,
    che_coefficient: float,
    conditions: CHEConditions,
) -> ReactionStepResult:
    contributions = [
        ReactionTermContribution(
            species_key=f"energy_{step_key}",
            coefficient=1.0,
            source_kind=ReactionEnergySourceKind.THERMOCHEMISTRY,
            source_hash="1" * 64,
            gibbs_free_energy_ev=delta_g_ev,
            contribution_ev=delta_g_ev,
        )
    ]
    condition_hash = None
    if che_coefficient != 0.0:
        contributions.append(
            ReactionTermContribution(
                species_key=f"che_{step_key}",
                coefficient=che_coefficient,
                source_kind=ReactionEnergySourceKind.CHE_RESERVOIR,
                source_hash="2" * 64,
                gibbs_free_energy_ev=0.0,
                contribution_ev=0.0,
            )
        )
        condition_hash = conditions.parameters_hash
    return ReactionStepResult(
        definition_hash="3" * 64,
        step_key=step_key,
        initial_state_key=initial_state_key,
        final_state_key=final_state_key,
        temperature_k=conditions.temperature_k,
        electrochemical_condition_hash=condition_hash,
        contributions=tuple(contributions),
        delta_g_ev=delta_g_ev,
    )


def _synthetic_pathway(
    *,
    state_keys: tuple[str, ...],
    step_data: tuple[tuple[str, float, float], ...],
    conditions: CHEConditions,
) -> ReactionPathwayResult:
    steps = tuple(
        _synthetic_step(
            step_key=step_key,
            initial_state_key=state_keys[index],
            final_state_key=state_keys[index + 1],
            delta_g_ev=delta_g_ev,
            che_coefficient=che_coefficient,
            conditions=conditions,
        )
        for index, (step_key, delta_g_ev, che_coefficient) in enumerate(step_data)
    )
    cumulative_values = [0.0]
    cumulative = 0.0
    for step in steps:
        cumulative += step.delta_g_ev
        cumulative_values.append(cumulative)
    return ReactionPathwayResult(
        pathway_hash="4" * 64,
        state_keys=state_keys,
        step_results=steps,
        cumulative_state_free_energies_ev=tuple(cumulative_values),
    )


def test_potential_view_uses_only_explicit_che_stoichiometry() -> None:
    baseline_conditions = _rhe_conditions(potential_v=0.0)
    pathway = _synthetic_pathway(
        state_keys=("clean", "H_star", "clean"),
        step_data=(
            ("reduction_step", 0.40, -1.0),
            ("chemical_release", -0.10, 0.0),
        ),
        conditions=baseline_conditions,
    )
    target_conditions = _rhe_conditions(potential_v=-0.50)

    view = evaluate_potential_dependent_pathway_view(
        baseline_pathway=pathway,
        baseline_conditions=baseline_conditions,
        target_conditions=target_conditions,
    )

    assert view.delta_che_condition_ev == pytest.approx(0.50)
    assert view.step_views[0].potential_slope_ev_per_v == pytest.approx(1.0)
    assert view.step_views[0].target_delta_g_ev == pytest.approx(-0.10)
    assert view.step_views[1].potential_slope_ev_per_v == 0.0
    assert view.step_views[1].target_delta_g_ev == pytest.approx(-0.10)
    assert view.cumulative_state_free_energies_ev == pytest.approx((0.0, -0.10, -0.20))


def test_equivalent_rhe_and_she_conditions_produce_identical_pathway_view() -> None:
    ph = 13.0
    baseline_conditions = _rhe_conditions(potential_v=-0.80, ph=ph)
    pathway = _synthetic_pathway(
        state_keys=("a", "b"),
        step_data=(("pcet", 0.25, -1.0),),
        conditions=baseline_conditions,
    )
    equivalent_she = CHEConditions(
        temperature_k=298.15,
        potential_v=rhe_to_she_potential_v(
            potential_rhe_v=-0.80,
            ph=ph,
            temperature_k=298.15,
        ),
        ph=ph,
        potential_reference=ElectrodePotentialReference.SHE,
        ph_semantics=CHEPhSemantics.EXPLICIT_ACTIVITY,
    )

    view = evaluate_potential_dependent_pathway_view(
        baseline_pathway=pathway,
        baseline_conditions=baseline_conditions,
        target_conditions=equivalent_she,
    )

    assert view.delta_che_condition_ev == pytest.approx(0.0, abs=1.0e-12)
    assert view.step_views[0].target_delta_g_ev == pytest.approx(0.25)


def test_she_ph_change_uses_the_che_proton_activity_term_once() -> None:
    baseline_conditions = CHEConditions(
        temperature_k=298.15,
        potential_v=0.0,
        ph=0.0,
        potential_reference=ElectrodePotentialReference.SHE,
        ph_semantics=CHEPhSemantics.EXPLICIT_ACTIVITY,
    )
    pathway = _synthetic_pathway(
        state_keys=("a", "b"),
        step_data=(("pcet", 0.0, -1.0),),
        conditions=baseline_conditions,
    )
    target_conditions = CHEConditions(
        temperature_k=298.15,
        potential_v=0.0,
        ph=7.0,
        potential_reference=ElectrodePotentialReference.SHE,
        ph_semantics=CHEPhSemantics.EXPLICIT_ACTIVITY,
    )

    view = evaluate_potential_dependent_pathway_view(
        baseline_pathway=pathway,
        baseline_conditions=baseline_conditions,
        target_conditions=target_conditions,
    )

    expected_delta_mu = -BOLTZMANN_EV_PER_K * 298.15 * log(10.0) * 7.0
    assert view.delta_che_condition_ev == pytest.approx(expected_delta_mu)
    assert view.step_views[0].target_delta_g_ev == pytest.approx(-expected_delta_mu)


def test_reduction_limiting_potential_is_maximum_feasible_edge() -> None:
    conditions = _rhe_conditions(potential_v=0.0)
    pathway = _synthetic_pathway(
        state_keys=("a", "b", "c"),
        step_data=(
            ("hard_reduction", 0.40, -1.0),
            ("easy_reduction", -0.10, -1.0),
        ),
        conditions=conditions,
    )

    result = solve_limiting_potential(
        baseline_pathway=pathway,
        baseline_conditions=conditions,
    )

    assert result.net_che_coefficient == -2.0
    assert result.selection is LimitingPotentialSelection.MAXIMUM_FEASIBLE
    assert result.feasible_lower_bound_v is None
    assert result.feasible_upper_bound_v == pytest.approx(-0.40)
    assert result.limiting_potential_v == pytest.approx(-0.40)
    assert result.determining_step_keys == ("hard_reduction",)


def test_oer_overpotential_and_reversible_potential_share_the_same_pathway() -> None:
    conditions = _rhe_conditions(potential_v=0.0)
    pathway = _synthetic_pathway(
        state_keys=("star", "OH", "O", "OOH", "star"),
        step_data=(
            ("oer_1", 1.40, 1.0),
            ("oer_2", 1.60, 1.0),
            ("oer_3", 0.90, 1.0),
            ("oer_4", 1.02, 1.0),
        ),
        conditions=conditions,
    )

    reversible = derive_reversible_potential(
        baseline_pathway=pathway,
        baseline_conditions=conditions,
    )
    oer = evaluate_oer_theoretical_overpotential(
        baseline_pathway=pathway,
        baseline_conditions=conditions,
    )

    assert reversible.reversible_potential_v == pytest.approx(1.23)
    assert oer.limiting.limiting_potential_v == pytest.approx(1.60)
    assert oer.limiting.determining_step_keys == ("oer_2",)
    assert oer.overpotential_v == pytest.approx(0.37)
    assert oer.reversible.result_hash == reversible.result_hash


def test_potential_independent_uphill_step_has_no_finite_electrode_solution() -> None:
    conditions = _rhe_conditions(potential_v=0.0)
    pathway = _synthetic_pathway(
        state_keys=("a", "b", "c"),
        step_data=(
            ("chemical_uphill", 0.20, 0.0),
            ("pcet", -0.10, -1.0),
        ),
        conditions=conditions,
    )

    with pytest.raises(ElectrocatalysisDescriptorError, match="potential-independent uphill"):
        solve_limiting_potential(
            baseline_pathway=pathway,
            baseline_conditions=conditions,
        )


def test_descriptor_rejects_baseline_condition_hash_mismatch() -> None:
    conditions = _rhe_conditions(potential_v=0.0)
    pathway = _synthetic_pathway(
        state_keys=("a", "b"),
        step_data=(("pcet", 0.10, -1.0),),
        conditions=conditions,
    )

    with pytest.raises(ElectrocatalysisDescriptorError, match="do not match"):
        solve_limiting_potential(
            baseline_pathway=pathway,
            baseline_conditions=_rhe_conditions(potential_v=0.10),
        )


def _surface_result(
    energy_ev: float,
    *,
    subject_kind: ThermochemistrySubjectKind,
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
            temperature_k=298.15,
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


def _bound_h2(energy_ev: float) -> BoundGasReferenceThermochemistry:
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
            spin_multiplicity=1,
            atomic_masses=(
                GasAtomicMass(atom_uids[0], 1.00784),
                GasAtomicMass(atom_uids[1], 1.00784),
            ),
        ),
    )
    result = ThermochemistryResult(
        identity=identity,
        components=ThermochemistryComponents(electronic_energy_ev=energy_ev),
        mode_selection=ThermochemistryModeSelection(accepted_mode_indices=(1,)),
    )
    return BoundGasReferenceThermochemistry(
        reference=GasReferenceDefinition(GasReferenceSpecies.H2),
        result=result,
    )


def test_her_delta_g_h_star_remains_an_explicit_adsorption_result() -> None:
    clean = ThermochemistryReactionSource(
        species_key="surface",
        result=_surface_result(
            -10.0,
            subject_kind=ThermochemistrySubjectKind.SURFACE,
        ),
    )
    hydrogen_adsorbed = ThermochemistryReactionSource(
        species_key="surface_H",
        result=_surface_result(
            -13.10,
            subject_kind=ThermochemistrySubjectKind.ADSORBATE,
        ),
    )
    h2 = MolecularReferenceReactionSource(
        species_key="H2",
        raw=_bound_h2(-6.0),
    )

    descriptor = evaluate_her_delta_g_h_star(
        step_key="her_h_adsorption",
        label="H* adsorption",
        initial_state_key="clean",
        final_state_key="H_star",
        hydrogen_adsorbed_source=hydrogen_adsorbed,
        clean_surface_source=clean,
        h2_reference=h2,
    )

    assert isinstance(descriptor, HERDeltaGHStarResult)
    assert descriptor.delta_g_h_star_ev == pytest.approx(-0.10)
    assert descriptor.delta_g_h_star_ev == descriptor.adsorption_result.delta_g_ev
    assert descriptor.h2_reference_hash == h2.source_hash

from __future__ import annotations

from typing import cast

import pytest

from ecatvasp.domain import AnalysisType, Project, new_artifact_id, new_atom_uid
from ecatvasp.domain.ids import new_analysis_id
from ecatvasp.schema.version import SCHEMA_VERSION
from ecatvasp.thermo import (
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
    ReactionDiagramDescriptorDefinition,
    ReactionDiagramDescriptorKind,
    ReactionDiagramDescriptorUnit,
    ReactionDiagramError,
    ReactionDiagramSourceReceipt,
    ReactionEnergySource,
    ReactionEnergySourceKind,
    ThermochemicalConditions,
    ThermochemicalStandardState,
    ThermochemistryComponents,
    ThermochemistryIdentity,
    ThermochemistryModeSelection,
    ThermochemistryReactionSource,
    ThermochemistryResult,
    ThermochemistrySubjectKind,
    VibrationalModePolicy,
    compile_co2rr_to_co_2e_preset,
    compile_her_volmer_heyrovsky_preset,
    compile_oer_associative_4e_preset,
    compile_orr_associative_4e_preset,
    evaluate_reaction_pathway,
    proton_electron_chemical_potential,
)
from ecatvasp.workflow import (
    THERMOCHEMISTRY_ANALYSIS_TYPES,
    ThermochemistryAnalysisRequirement,
    ThermochemistryReconciliationError,
)


def _surface_source(
    species_key: str,
    energy_ev: float,
    *,
    adsorbate: bool,
) -> ThermochemistryReactionSource:
    subject_kind = ThermochemistrySubjectKind.ADSORBATE if adsorbate else (
        ThermochemistrySubjectKind.SURFACE
    )
    policy = None
    selection = None
    if adsorbate:
        policy = VibrationalModePolicy(
            frequency_cutoff_cm_inverse=50.0,
            imaginary_mode_policy=ImaginaryModePolicy.REJECT_ANY,
            low_frequency_policy=LowFrequencyPolicy.REJECT_BELOW_CUTOFF,
        )
        selection = ThermochemistryModeSelection(accepted_mode_indices=(1,))
    result = ThermochemistryResult(
        identity=ThermochemistryIdentity(
            subject_kind=subject_kind,
            conditions=ThermochemicalConditions(
                temperature_k=298.15,
                standard_state=ThermochemicalStandardState.SURFACE_FIXED_CELL,
            ),
            electronic_energy_kind=ElectronicEnergyKind.SIGMA_ZERO,
            electronic_entropy_policy=ElectronicEntropyPolicy.NEGLECTED,
            vibrational_policy=policy,
        ),
        components=ThermochemistryComponents(electronic_energy_ev=energy_ev),
        mode_selection=selection,
    )
    return ThermochemistryReactionSource(species_key, result)


def _molecular_source(
    species_key: str,
    species: GasReferenceSpecies,
    energy_ev: float,
) -> MolecularReferenceReactionSource:
    elements_by_species = {
        GasReferenceSpecies.H2: ("H", "H"),
        GasReferenceSpecies.H2O: ("H", "H", "O"),
        GasReferenceSpecies.O2: ("O", "O"),
        GasReferenceSpecies.CO: ("C", "O"),
        GasReferenceSpecies.CO2: ("C", "O", "O"),
    }
    masses = {"H": 1.00784, "C": 12.011, "O": 15.999}
    elements = elements_by_species[species]
    atom_uids = tuple(new_atom_uid() for _ in elements)
    geometry = (
        GasGeometryKind.NONLINEAR
        if species is GasReferenceSpecies.H2O
        else GasGeometryKind.LINEAR
    )
    symmetric_species = {
        GasReferenceSpecies.H2,
        GasReferenceSpecies.H2O,
        GasReferenceSpecies.O2,
        GasReferenceSpecies.CO2,
    }
    result = ThermochemistryResult(
        identity=ThermochemistryIdentity(
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
                geometry_kind=geometry,
                symmetry_number=2 if species in symmetric_species else 1,
                spin_multiplicity=3 if species is GasReferenceSpecies.O2 else 1,
                atomic_masses=tuple(
                    GasAtomicMass(atom_uid, masses[element])
                    for atom_uid, element in zip(atom_uids, elements, strict=True)
                ),
            ),
        ),
        components=ThermochemistryComponents(electronic_energy_ev=energy_ev),
        mode_selection=ThermochemistryModeSelection(accepted_mode_indices=(1,)),
    )
    return MolecularReferenceReactionSource(
        species_key,
        raw=BoundGasReferenceThermochemistry(
            reference=GasReferenceDefinition(species),
            result=result,
        ),
    )


def test_representative_presets_share_the_generic_reaction_evaluator() -> None:
    conditions = CHEConditions(
        temperature_k=298.15,
        potential_v=0.0,
        ph=0.0,
        potential_reference=ElectrodePotentialReference.SHE,
        ph_semantics=CHEPhSemantics.EXPLICIT_ACTIVITY,
    )
    h2 = _molecular_source("H2", GasReferenceSpecies.H2, -6.0)
    che = CHEReactionSource(
        "H_plus_e",
        proton_electron_chemical_potential(
            hydrogen_reference=CHEHydrogenReference(raw=h2.raw),
            conditions=conditions,
        ),
    )
    surface = _surface_source("star", -10.0, adsorbate=False)
    h_star = _surface_source("H_star", -13.0, adsorbate=True)
    ooh = _surface_source("OOH_star", -15.0, adsorbate=True)
    o = _surface_source("O_star", -14.0, adsorbate=True)
    oh = _surface_source("OH_star", -13.5, adsorbate=True)
    cooh = _surface_source("COOH_star", -15.2, adsorbate=True)
    co_star = _surface_source("CO_star", -12.8, adsorbate=True)
    h2o = _molecular_source("H2O", GasReferenceSpecies.H2O, -14.0)
    o2 = _molecular_source("O2", GasReferenceSpecies.O2, -9.0)
    co2 = _molecular_source("CO2", GasReferenceSpecies.CO2, -18.0)
    co = _molecular_source("CO_gas", GasReferenceSpecies.CO, -12.0)

    cases = (
        (
            compile_her_volmer_heyrovsky_preset(
                pathway_key="her",
                clean_surface_key="star",
                h_adsorbed_key="H_star",
                h2_reference_key="H2",
                che_key="H_plus_e",
            ),
            (surface, h_star, h2, che),
        ),
        (
            compile_orr_associative_4e_preset(
                pathway_key="orr",
                clean_surface_key="star",
                ooh_adsorbed_key="OOH_star",
                o_adsorbed_key="O_star",
                oh_adsorbed_key="OH_star",
                o2_reference_key="O2",
                h2o_reference_key="H2O",
                che_key="H_plus_e",
            ),
            (surface, ooh, o, oh, o2, h2o, che),
        ),
        (
            compile_oer_associative_4e_preset(
                pathway_key="oer",
                clean_surface_key="star",
                oh_adsorbed_key="OH_star",
                o_adsorbed_key="O_star",
                ooh_adsorbed_key="OOH_star",
                h2o_reference_key="H2O",
                o2_reference_key="O2",
                che_key="H_plus_e",
            ),
            (surface, oh, o, ooh, h2o, o2, che),
        ),
        (
            compile_co2rr_to_co_2e_preset(
                pathway_key="co2-to-co",
                clean_surface_key="star",
                cooh_adsorbed_key="COOH_star",
                co_adsorbed_key="CO_star",
                co2_reference_key="CO2",
                h2o_reference_key="H2O",
                co_reference_key="CO_gas",
                che_key="H_plus_e",
            ),
            (surface, cooh, co_star, co2, h2o, co, che),
        ),
    )
    for preset, sources in cases:
        result = evaluate_reaction_pathway(
            definition=preset.definition,
            sources=cast(tuple[ReactionEnergySource, ...], sources),
        )
        assert result.pathway_hash == preset.definition.content_hash
        assert result.state_keys == preset.definition.state_keys
        assert len(result.step_results) == len(preset.definition.steps)


def test_public_v08_runtime_enums_fail_closed() -> None:
    with pytest.raises(ReactionDiagramError, match="descriptor kind"):
        ReactionDiagramDescriptorDefinition(
            key="u_lim",
            kind=cast(ReactionDiagramDescriptorKind, "limiting_potential"),
            value=-0.4,
            unit=ReactionDiagramDescriptorUnit.VOLT,
            source_result_hash="1" * 64,
            pathway_hash="2" * 64,
            baseline_result_hash="3" * 64,
        )
    with pytest.raises(ReactionDiagramError, match="descriptor unit"):
        ReactionDiagramDescriptorDefinition(
            key="delta_g_h",
            kind=ReactionDiagramDescriptorKind.HER_DELTA_G_H_STAR,
            value=0.0,
            unit=cast(ReactionDiagramDescriptorUnit, "eV"),
            source_result_hash="1" * 64,
        )
    with pytest.raises(ReactionDiagramError, match="source_kind"):
        ReactionDiagramSourceReceipt(
            species_key="star",
            source_kind=cast(ReactionEnergySourceKind, "thermochemistry"),
            source_hash="1" * 64,
            analysis_id=new_analysis_id(),
            artifact_id=new_artifact_id(),
            artifact_sha256="2" * 64,
            artifact_result_hash="3" * 64,
        )
    with pytest.raises(ThermochemistryReconciliationError, match="analysis_type"):
        ThermochemistryAnalysisRequirement(
            key="diagram",
            project_id=Project(name="runtime", slug="runtime").id,
            analysis_type=cast(AnalysisType, "reaction_diagram"),
            input_artifact_ids=(new_artifact_id(),),
            parameters_hash="4" * 64,
        )


def test_v08_final_scope_lock_preserves_schema_and_analysis_surface() -> None:
    assert SCHEMA_VERSION == 3
    assert frozenset(
        {AnalysisType.THERMOCHEMISTRY, AnalysisType.REACTION_DIAGRAM}
    ) == THERMOCHEMISTRY_ANALYSIS_TYPES

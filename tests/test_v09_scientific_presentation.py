from __future__ import annotations

from dataclasses import replace

import pytest

from ecatvasp.analysis.electronic import (
    CanonicalDosResult,
    DosSeries,
    ElectronicEnergyAxis,
    OrbitalChannel,
    ProjectionScope,
    SpinChannel,
)
from ecatvasp.analysis.lobster import (
    CanonicalCohpResult,
    CohpInteraction,
    CohpSpinSeries,
)
from ecatvasp.domain import Lattice, StructureSite, StructureSnapshot, new_atom_uid
from ecatvasp.domain.ids import new_analysis_id, new_artifact_id, new_project_id
from ecatvasp.provenance import scientific_hash
from ecatvasp.schema.version import SCHEMA_VERSION
from ecatvasp.thermo.che import (
    CHEConditions,
    CHEPhSemantics,
    ElectrodePotentialReference,
)
from ecatvasp.thermo.electrocatalysis import (
    PotentialDependentPathwayView,
    PotentialStepView,
)
from ecatvasp.thermo.reaction import ReactionEnergySourceKind
from ecatvasp.thermo.reaction_diagram import (
    ReactionDiagramDataset,
    ReactionDiagramDescriptorDefinition,
    ReactionDiagramDescriptorKind,
    ReactionDiagramDescriptorUnit,
    ReactionDiagramSourceReceipt,
)
from ecatvasp.visualization import (
    PRESENTATION_CONTRACT_VERSION,
    ScientificPresentationError,
    build_cohp_presentation,
    build_dos_presentation,
    build_reaction_diagram_presentation,
    build_structure_presentation,
)


def _snapshot() -> StructureSnapshot:
    return StructureSnapshot(
        label="presentation structure",
        lattice=Lattice(
            vectors=((3.0, 0.0, 0.0), (0.0, 3.0, 0.0), (0.0, 0.0, 15.0))
        ),
        sites=(
            StructureSite(
                atom_uid=new_atom_uid(),
                element="C",
                fractional_coords=(0.1, 0.2, 0.3),
            ),
            StructureSite(
                atom_uid=new_atom_uid(),
                element="Pt",
                fractional_coords=(0.5, 0.5, 0.4),
            ),
        ),
    )


def _dos(snapshot: StructureSnapshot) -> CanonicalDosResult:
    axis = ElectronicEnergyAxis(
        energies_ev=(-2.0, -1.0, 1.0),
        fermi_energy_ev=-0.5,
    )
    return CanonicalDosResult(
        structure_snapshot_id=snapshot.id,
        energy_axis=axis,
        series=(
            DosSeries(
                scope=ProjectionScope.SYSTEM,
                spin=SpinChannel.TOTAL,
                values=(1.0, 2.0, 3.0),
            ),
            DosSeries(
                scope=ProjectionScope.ATOM,
                spin=SpinChannel.TOTAL,
                values=(0.2, 0.4, 0.6),
                atom_uid=snapshot.sites[1].atom_uid,
                element="Pt",
                orbital=OrbitalChannel(label="d", angular_momentum=2),
            ),
        ),
        atom_index_map_sha256="a" * 64,
    )


def _cohp(snapshot: StructureSnapshot) -> CanonicalCohpResult:
    average = CohpSpinSeries(
        spin=SpinChannel.TOTAL,
        cohp_values=(-0.3, -0.1, 0.2),
        icohp_values=(-0.7, -0.9, -0.5),
        icohp_at_fermi_ev=-0.9,
    )
    interaction = CohpInteraction(
        source_index=1,
        source_label="No.1:C1->Pt2(2.00)",
        atom_uid_a=snapshot.sites[0].atom_uid,
        atom_uid_b=snapshot.sites[1].atom_uid,
        element_a="C",
        element_b="Pt",
        bond_length_angstrom=2.0,
        series=(
            CohpSpinSeries(
                spin=SpinChannel.TOTAL,
                cohp_values=(-0.4, -0.2, 0.3),
                icohp_values=(-0.8, -1.1, -0.6),
                icohp_at_fermi_ev=-1.1,
            ),
        ),
        orbital_a="2p",
        orbital_b="5d",
    )
    return CanonicalCohpResult(
        structure_snapshot_id=snapshot.id,
        energies_ev_relative_to_fermi=(-1.0, 0.0, 1.0),
        source_fermi_energy_ev=4.5,
        average_series=(average,),
        interactions=(interaction,),
        atom_index_map_sha256="b" * 64,
    )


def _conditions() -> CHEConditions:
    return CHEConditions(
        temperature_k=298.15,
        potential_v=0.0,
        ph=0.0,
        potential_reference=ElectrodePotentialReference.RHE,
        ph_semantics=CHEPhSemantics.INCLUDED_IN_RHE,
    )


def _reaction_diagram() -> ReactionDiagramDataset:
    conditions = _conditions()
    view = PotentialDependentPathwayView(
        pathway_hash="c" * 64,
        baseline_result_hash="d" * 64,
        baseline_conditions=conditions,
        target_conditions=conditions,
        delta_che_condition_ev=0.0,
        state_keys=("*", "*OH"),
        step_views=(
            PotentialStepView(
                step_key="adsorb-oh",
                che_coefficient=1.0,
                potential_slope_ev_per_v=-1.0,
                baseline_delta_g_ev=0.4,
                target_delta_g_ev=0.4,
            ),
        ),
        cumulative_state_free_energies_ev=(0.0, 0.4),
    )
    receipt = ReactionDiagramSourceReceipt(
        species_key="surface",
        source_kind=ReactionEnergySourceKind.THERMOCHEMISTRY,
        source_hash="1" * 64,
        analysis_id=new_analysis_id(),
        artifact_id=new_artifact_id(),
        artifact_sha256="2" * 64,
        artifact_result_hash="3" * 64,
    )
    descriptor = ReactionDiagramDescriptorDefinition(
        key="delta-g-h-star",
        kind=ReactionDiagramDescriptorKind.HER_DELTA_G_H_STAR,
        value=0.1,
        unit=ReactionDiagramDescriptorUnit.ELECTRON_VOLT,
        source_result_hash="4" * 64,
    )
    return ReactionDiagramDataset(
        project_id=new_project_id(),
        pathway_definition_hash="c" * 64,
        baseline_pathway_result_hash="d" * 64,
        baseline_conditions=conditions,
        requested_conditions=conditions,
        potential_view=view,
        source_receipts=(receipt,),
        descriptor_definitions=(descriptor,),
    )


def test_structure_presentation_reuses_identity_preserving_matterviz_contract() -> None:
    snapshot = _snapshot()

    dataset = build_structure_presentation(snapshot, matterviz_available=False)

    assert dataset.structure_snapshot_id == snapshot.id
    assert dataset.source_scientific_hash == scientific_hash(snapshot)
    assert dataset.contract_version == PRESENTATION_CONTRACT_VERSION
    assert dataset.matterviz.runtime.interactive_available is False
    assert dataset.matterviz.atom_index_map.atom_uid_by_viewer_index == tuple(
        item.atom_uid for item in snapshot.sites
    )
    assert dataset.to_dict()["matterviz"] == dataset.matterviz.to_dict()


def test_dos_presentation_preserves_native_values_and_exposes_explicit_fermi_view() -> None:
    snapshot = _snapshot()
    source = _dos(snapshot)

    dataset = build_dos_presentation(source)

    assert dataset.source_content_hash == source.content_hash
    assert dataset.structure_snapshot_id == snapshot.id
    assert dataset.native_energies_ev == (-2.0, -1.0, 1.0)
    assert dataset.energies_ev_relative_to_fermi == (-1.5, -0.5, 1.5)
    assert tuple(item.values for item in dataset.series) == tuple(
        item.values for item in source.series
    )
    projected = dataset.series[1]
    assert projected.atom_uid == snapshot.sites[1].atom_uid
    assert projected.element == "Pt"
    assert projected.orbital_label == "d"
    assert projected.orbital_angular_momentum == 2
    assert dataset.density_unit == "states/eV"


def test_cohp_presentation_preserves_lobster_native_sign_and_pair_identity() -> None:
    snapshot = _snapshot()
    source = _cohp(snapshot)

    dataset = build_cohp_presentation(source)

    assert dataset.source_content_hash == source.content_hash
    assert dataset.sign_convention == "lobster_native"
    assert dataset.energies_ev_relative_to_fermi == (-1.0, 0.0, 1.0)
    assert dataset.average_series[0].cohp_values == (-0.3, -0.1, 0.2)
    interaction = dataset.interactions[0]
    assert interaction.atom_uid_a == snapshot.sites[0].atom_uid
    assert interaction.atom_uid_b == snapshot.sites[1].atom_uid
    assert interaction.series[0].cohp_values == (-0.4, -0.2, 0.3)
    assert interaction.series[0].icohp_at_fermi_ev == -1.1

    with pytest.raises(ScientificPresentationError, match="native sign"):
        replace(dataset, sign_convention="minus_cohp")


def test_reaction_diagram_presentation_copies_conditions_steps_descriptors_and_receipts() -> None:
    source = _reaction_diagram()

    dataset = build_reaction_diagram_presentation(source)

    assert dataset.source_result_hash == source.result_hash
    assert dataset.potential_view_result_hash == source.potential_view.result_hash
    assert dataset.pathway_definition_hash == source.pathway_definition_hash
    assert dataset.requested_conditions.parameters_hash == source.requested_conditions.parameters_hash
    assert tuple((item.state_key, item.cumulative_free_energy_ev) for item in dataset.states) == (
        ("*", 0.0),
        ("*OH", 0.4),
    )
    assert dataset.steps[0].step_key == "adsorb-oh"
    assert dataset.steps[0].from_state_key == "*"
    assert dataset.steps[0].to_state_key == "*OH"
    assert dataset.steps[0].target_delta_g_ev == 0.4
    assert dataset.descriptors[0].kind == "her_delta_g_h_star"
    assert dataset.descriptors[0].unit == "eV"
    assert dataset.sources[0].species_key == "surface"
    assert dataset.sources[0].source_kind == "thermochemistry"


def test_presentation_contract_contains_no_style_authority_and_does_not_advance_schema() -> None:
    snapshot = _snapshot()
    dos_dict = build_dos_presentation(_dos(snapshot)).to_dict()
    cohp_dict = build_cohp_presentation(_cohp(snapshot)).to_dict()
    reaction_dict = build_reaction_diagram_presentation(_reaction_diagram()).to_dict()

    forbidden = {
        "color",
        "colors",
        "axis_range",
        "camera",
        "panel_layout",
        "export_format",
    }
    assert forbidden.isdisjoint(dos_dict)
    assert forbidden.isdisjoint(cohp_dict)
    assert forbidden.isdisjoint(reaction_dict)
    assert SCHEMA_VERSION == 3
    assert PRESENTATION_CONTRACT_VERSION == "ecatvasp-scientific-presentation-v1"

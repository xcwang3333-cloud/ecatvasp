"""Stable scientific presentation DTOs over canonical ECatVASP datasets.

These views are transient presentation contracts. They preserve source identities and
scientific semantics while deliberately excluding style, axis-range, color, camera,
panel-layout, and export-format state from scientific authority.
"""

from __future__ import annotations

from dataclasses import dataclass

from ecatvasp.analysis.electronic import (
    CanonicalDosResult,
    DosSeries,
    ElectronicEnergyReference,
    ProjectionScope,
    SpinChannel,
)
from ecatvasp.analysis.lobster import (
    CanonicalCohpResult,
    CohpEnergyReference,
    CohpInteraction,
    CohpSpinSeries,
)
from ecatvasp.domain import StructureSnapshot
from ecatvasp.domain.ids import AtomUid, ProjectId, StructureSnapshotId
from ecatvasp.provenance import scientific_hash
from ecatvasp.structures.state_conformer import ConformerVisualizationContext
from ecatvasp.thermo.che import CHEConditions
from ecatvasp.thermo.reaction_diagram import ReactionDiagramDataset
from ecatvasp.visualization.matterviz import MatterVizViewBundle, build_matterviz_view

PRESENTATION_CONTRACT_VERSION = "ecatvasp-scientific-presentation-v1"


class ScientificPresentationError(ValueError):
    """Raised when a canonical scientific result cannot be projected exactly."""


def _sha256(value: str, field_name: str) -> str:
    normalized = value.strip().lower()
    if len(normalized) != 64:
        raise ScientificPresentationError(
            f"{field_name} must be a 64-character hexadecimal SHA-256 digest"
        )
    try:
        int(normalized, 16)
    except ValueError as error:
        raise ScientificPresentationError(
            f"{field_name} must contain only hexadecimal characters"
        ) from error
    return normalized


def _contract(value: str) -> None:
    if value != PRESENTATION_CONTRACT_VERSION:
        raise ScientificPresentationError("unsupported scientific presentation contract version")


@dataclass(frozen=True, slots=True)
class StructurePresentationDataset:
    """Structure presentation boundary composed from the existing MatterViz adapter."""

    structure_snapshot_id: StructureSnapshotId
    source_scientific_hash: str
    matterviz: MatterVizViewBundle
    contract_version: str = PRESENTATION_CONTRACT_VERSION

    def __post_init__(self) -> None:
        _contract(self.contract_version)
        object.__setattr__(
            self,
            "source_scientific_hash",
            _sha256(self.source_scientific_hash, "source_scientific_hash"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "structure_snapshot_id": str(self.structure_snapshot_id),
            "source_scientific_hash": self.source_scientific_hash,
            "matterviz": self.matterviz.to_dict(),
        }


def build_structure_presentation(
    snapshot: StructureSnapshot,
    *,
    context: ConformerVisualizationContext | None = None,
    matterviz_available: bool = True,
    unavailable_message: str = "interactive MatterViz runtime unavailable",
) -> StructurePresentationDataset:
    """Compose the existing identity-preserving MatterViz view with exact snapshot identity."""

    return StructurePresentationDataset(
        structure_snapshot_id=snapshot.id,
        source_scientific_hash=scientific_hash(snapshot),
        matterviz=build_matterviz_view(
            snapshot,
            context=context,
            matterviz_available=matterviz_available,
            unavailable_message=unavailable_message,
        ),
    )


@dataclass(frozen=True, slots=True)
class DosPresentationSeries:
    """One unchanged canonical DOS/PDOS series with structured selectors."""

    scope: ProjectionScope
    spin: SpinChannel
    values: tuple[float, ...]
    atom_uid: AtomUid | None = None
    element: str | None = None
    orbital_label: str | None = None
    orbital_angular_momentum: int | None = None

    @classmethod
    def from_source(cls, source: DosSeries) -> DosPresentationSeries:
        orbital = source.orbital
        return cls(
            scope=source.scope,
            spin=source.spin,
            values=source.values,
            atom_uid=source.atom_uid,
            element=source.element,
            orbital_label=None if orbital is None else orbital.label,
            orbital_angular_momentum=(
                None if orbital is None else orbital.angular_momentum
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "scope": self.scope.value,
            "spin": self.spin.value,
            "values": list(self.values),
            "atom_uid": None if self.atom_uid is None else str(self.atom_uid),
            "element": self.element,
            "orbital_label": self.orbital_label,
            "orbital_angular_momentum": self.orbital_angular_momentum,
        }


@dataclass(frozen=True, slots=True)
class DosPresentationDataset:
    """Plot-ready DOS/PDOS data retaining canonical source and energy-reference semantics."""

    structure_snapshot_id: StructureSnapshotId
    source_content_hash: str
    atom_index_map_sha256: str
    source_energy_reference: ElectronicEnergyReference
    native_energies_ev: tuple[float, ...]
    energies_ev_relative_to_fermi: tuple[float, ...]
    fermi_energy_ev: float
    series: tuple[DosPresentationSeries, ...]
    energy_unit: str = "eV"
    density_unit: str = "states/eV"
    contract_version: str = PRESENTATION_CONTRACT_VERSION

    def __post_init__(self) -> None:
        _contract(self.contract_version)
        object.__setattr__(
            self,
            "source_content_hash",
            _sha256(self.source_content_hash, "source_content_hash"),
        )
        object.__setattr__(
            self,
            "atom_index_map_sha256",
            _sha256(self.atom_index_map_sha256, "atom_index_map_sha256"),
        )
        if len(self.native_energies_ev) != len(self.energies_ev_relative_to_fermi):
            raise ScientificPresentationError("DOS native and Fermi-relative axes must align")
        if not self.series:
            raise ScientificPresentationError("DOS presentation requires at least one series")
        if any(len(item.values) != len(self.native_energies_ev) for item in self.series):
            raise ScientificPresentationError("DOS presentation series must use the common grid")
        if self.energy_unit != "eV" or self.density_unit != "states/eV":
            raise ScientificPresentationError("unsupported DOS presentation units")

    def to_dict(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "structure_snapshot_id": str(self.structure_snapshot_id),
            "source_content_hash": self.source_content_hash,
            "atom_index_map_sha256": self.atom_index_map_sha256,
            "source_energy_reference": self.source_energy_reference.value,
            "native_energies_ev": list(self.native_energies_ev),
            "energies_ev_relative_to_fermi": list(self.energies_ev_relative_to_fermi),
            "fermi_energy_ev": self.fermi_energy_ev,
            "energy_unit": self.energy_unit,
            "density_unit": self.density_unit,
            "series": [item.to_dict() for item in self.series],
        }


def build_dos_presentation(source: CanonicalDosResult) -> DosPresentationDataset:
    """Project canonical DOS without aggregation, smoothing, spin inversion, or clipping."""

    return DosPresentationDataset(
        structure_snapshot_id=source.structure_snapshot_id,
        source_content_hash=source.content_hash,
        atom_index_map_sha256=source.atom_index_map_sha256,
        source_energy_reference=source.energy_axis.reference,
        native_energies_ev=source.energy_axis.energies_ev,
        energies_ev_relative_to_fermi=source.energy_axis.relative_to_fermi(),
        fermi_energy_ev=source.energy_axis.fermi_energy_ev,
        series=tuple(DosPresentationSeries.from_source(item) for item in source.series),
    )


@dataclass(frozen=True, slots=True)
class CohpPresentationSeries:
    """Native-sign COHP and ICOHP curves for one canonical spin channel."""

    spin: SpinChannel
    cohp_values: tuple[float, ...]
    icohp_values: tuple[float, ...]
    icohp_at_fermi_ev: float | None

    @classmethod
    def from_source(cls, source: CohpSpinSeries) -> CohpPresentationSeries:
        return cls(
            spin=source.spin,
            cohp_values=source.cohp_values,
            icohp_values=source.icohp_values,
            icohp_at_fermi_ev=source.icohp_at_fermi_ev,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "spin": self.spin.value,
            "cohp_values": list(self.cohp_values),
            "icohp_values": list(self.icohp_values),
            "icohp_at_fermi_ev": self.icohp_at_fermi_ev,
        }


@dataclass(frozen=True, slots=True)
class CohpInteractionPresentation:
    """Permanent-UID pair/orbital metadata plus unchanged native COHP/ICOHP curves."""

    source_index: int
    source_label: str
    atom_uid_a: AtomUid
    atom_uid_b: AtomUid
    element_a: str
    element_b: str
    bond_length_angstrom: float
    series: tuple[CohpPresentationSeries, ...]
    cell_a: tuple[int, int, int] | None = None
    cell_b: tuple[int, int, int] | None = None
    orbital_a: str | None = None
    orbital_b: str | None = None

    @classmethod
    def from_source(cls, source: CohpInteraction) -> CohpInteractionPresentation:
        return cls(
            source_index=source.source_index,
            source_label=source.source_label,
            atom_uid_a=source.atom_uid_a,
            atom_uid_b=source.atom_uid_b,
            element_a=source.element_a,
            element_b=source.element_b,
            bond_length_angstrom=source.bond_length_angstrom,
            series=tuple(CohpPresentationSeries.from_source(item) for item in source.series),
            cell_a=source.cell_a,
            cell_b=source.cell_b,
            orbital_a=source.orbital_a,
            orbital_b=source.orbital_b,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "source_index": self.source_index,
            "source_label": self.source_label,
            "atom_uid_a": str(self.atom_uid_a),
            "atom_uid_b": str(self.atom_uid_b),
            "element_a": self.element_a,
            "element_b": self.element_b,
            "bond_length_angstrom": self.bond_length_angstrom,
            "cell_a": None if self.cell_a is None else list(self.cell_a),
            "cell_b": None if self.cell_b is None else list(self.cell_b),
            "orbital_a": self.orbital_a,
            "orbital_b": self.orbital_b,
            "series": [item.to_dict() for item in self.series],
        }


@dataclass(frozen=True, slots=True)
class CohpPresentationDataset:
    """Plot-ready LOBSTER curves preserving native sign and exact pair identity."""

    structure_snapshot_id: StructureSnapshotId
    source_content_hash: str
    atom_index_map_sha256: str
    energy_reference: CohpEnergyReference
    energies_ev_relative_to_fermi: tuple[float, ...]
    source_fermi_energy_ev: float
    average_series: tuple[CohpPresentationSeries, ...]
    interactions: tuple[CohpInteractionPresentation, ...]
    sign_convention: str = "lobster_native"
    energy_unit: str = "eV"
    bond_length_unit: str = "angstrom"
    contract_version: str = PRESENTATION_CONTRACT_VERSION

    def __post_init__(self) -> None:
        _contract(self.contract_version)
        object.__setattr__(
            self,
            "source_content_hash",
            _sha256(self.source_content_hash, "source_content_hash"),
        )
        object.__setattr__(
            self,
            "atom_index_map_sha256",
            _sha256(self.atom_index_map_sha256, "atom_index_map_sha256"),
        )
        if self.sign_convention != "lobster_native":
            raise ScientificPresentationError("COHP presentation must preserve LOBSTER native sign")
        if self.energy_unit != "eV" or self.bond_length_unit != "angstrom":
            raise ScientificPresentationError("unsupported COHP presentation units")
        if not self.average_series or not self.interactions:
            raise ScientificPresentationError("COHP presentation requires average and interactions")
        grid_len = len(self.energies_ev_relative_to_fermi)
        all_series = self.average_series + tuple(
            series
            for interaction in self.interactions
            for series in interaction.series
        )
        if any(
            len(item.cohp_values) != grid_len or len(item.icohp_values) != grid_len
            for item in all_series
        ):
            raise ScientificPresentationError("COHP presentation series must use the common grid")

    def to_dict(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "structure_snapshot_id": str(self.structure_snapshot_id),
            "source_content_hash": self.source_content_hash,
            "atom_index_map_sha256": self.atom_index_map_sha256,
            "energy_reference": self.energy_reference.value,
            "energies_ev_relative_to_fermi": list(self.energies_ev_relative_to_fermi),
            "source_fermi_energy_ev": self.source_fermi_energy_ev,
            "sign_convention": self.sign_convention,
            "energy_unit": self.energy_unit,
            "bond_length_unit": self.bond_length_unit,
            "average_series": [item.to_dict() for item in self.average_series],
            "interactions": [item.to_dict() for item in self.interactions],
        }


def build_cohp_presentation(source: CanonicalCohpResult) -> CohpPresentationDataset:
    """Project canonical COHP/ICOHP without applying a -COHP display transform."""

    return CohpPresentationDataset(
        structure_snapshot_id=source.structure_snapshot_id,
        source_content_hash=source.content_hash,
        atom_index_map_sha256=source.atom_index_map_sha256,
        energy_reference=source.energy_reference,
        energies_ev_relative_to_fermi=source.energies_ev_relative_to_fermi,
        source_fermi_energy_ev=source.source_fermi_energy_ev,
        average_series=tuple(
            CohpPresentationSeries.from_source(item) for item in source.average_series
        ),
        interactions=tuple(
            CohpInteractionPresentation.from_source(item) for item in source.interactions
        ),
    )


@dataclass(frozen=True, slots=True)
class CHEConditionsPresentation:
    """Explicit CHE condition metadata copied from a canonical reaction diagram."""

    temperature_k: float
    potential_v: float
    ph: float
    potential_reference: str
    ph_semantics: str
    parameters_hash: str

    @classmethod
    def from_source(cls, source: CHEConditions) -> CHEConditionsPresentation:
        return cls(
            temperature_k=source.temperature_k,
            potential_v=source.potential_v,
            ph=source.ph,
            potential_reference=source.potential_reference.value,
            ph_semantics=source.ph_semantics.value,
            parameters_hash=source.parameters_hash,
        )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "parameters_hash",
            _sha256(self.parameters_hash, "parameters_hash"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "temperature_k": self.temperature_k,
            "potential_v": self.potential_v,
            "ph": self.ph,
            "potential_reference": self.potential_reference,
            "ph_semantics": self.ph_semantics,
            "parameters_hash": self.parameters_hash,
        }


@dataclass(frozen=True, slots=True)
class ReactionDiagramStatePresentation:
    """One ordered canonical pathway state at the requested CHE condition."""

    index: int
    state_key: str
    cumulative_free_energy_ev: float

    def to_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "state_key": self.state_key,
            "cumulative_free_energy_ev": self.cumulative_free_energy_ev,
        }


@dataclass(frozen=True, slots=True)
class ReactionDiagramStepPresentation:
    """One canonical adjacent pathway step at the requested CHE condition."""

    index: int
    step_key: str
    from_state_key: str
    to_state_key: str
    che_coefficient: float
    potential_slope_ev_per_v: float
    baseline_delta_g_ev: float
    target_delta_g_ev: float

    def to_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "step_key": self.step_key,
            "from_state_key": self.from_state_key,
            "to_state_key": self.to_state_key,
            "che_coefficient": self.che_coefficient,
            "potential_slope_ev_per_v": self.potential_slope_ev_per_v,
            "baseline_delta_g_ev": self.baseline_delta_g_ev,
            "target_delta_g_ev": self.target_delta_g_ev,
        }


@dataclass(frozen=True, slots=True)
class ReactionDiagramDescriptorPresentation:
    """Exact canonical descriptor definition copied into a presentation-safe shape."""

    key: str
    kind: str
    value: float
    unit: str
    source_result_hash: str
    definition_hash: str
    pathway_hash: str | None = None
    baseline_result_hash: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_result_hash",
            _sha256(self.source_result_hash, "source_result_hash"),
        )
        object.__setattr__(
            self,
            "definition_hash",
            _sha256(self.definition_hash, "definition_hash"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "kind": self.kind,
            "value": self.value,
            "unit": self.unit,
            "source_result_hash": self.source_result_hash,
            "definition_hash": self.definition_hash,
            "pathway_hash": self.pathway_hash,
            "baseline_result_hash": self.baseline_result_hash,
        }


@dataclass(frozen=True, slots=True)
class ReactionDiagramSourcePresentation:
    """Exact durable non-CHE source receipt retained for downstream inspection."""

    species_key: str
    source_kind: str
    source_hash: str
    analysis_id: str
    artifact_id: str
    artifact_sha256: str
    artifact_result_hash: str

    def __post_init__(self) -> None:
        for field_name in (
            "source_hash",
            "artifact_sha256",
            "artifact_result_hash",
        ):
            object.__setattr__(
                self,
                field_name,
                _sha256(getattr(self, field_name), field_name),
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "species_key": self.species_key,
            "source_kind": self.source_kind,
            "source_hash": self.source_hash,
            "analysis_id": self.analysis_id,
            "artifact_id": self.artifact_id,
            "artifact_sha256": self.artifact_sha256,
            "artifact_result_hash": self.artifact_result_hash,
        }


@dataclass(frozen=True, slots=True)
class ReactionDiagramPresentationDataset:
    """Plot-ready canonical pathway values with explicit conditions and source receipts."""

    project_id: ProjectId
    source_result_hash: str
    potential_view_result_hash: str
    pathway_definition_hash: str
    baseline_pathway_result_hash: str
    baseline_conditions: CHEConditionsPresentation
    requested_conditions: CHEConditionsPresentation
    states: tuple[ReactionDiagramStatePresentation, ...]
    steps: tuple[ReactionDiagramStepPresentation, ...]
    descriptors: tuple[ReactionDiagramDescriptorPresentation, ...]
    sources: tuple[ReactionDiagramSourcePresentation, ...]
    free_energy_unit: str = "eV"
    potential_unit: str = "V"
    contract_version: str = PRESENTATION_CONTRACT_VERSION

    def __post_init__(self) -> None:
        _contract(self.contract_version)
        for field_name in (
            "source_result_hash",
            "potential_view_result_hash",
            "pathway_definition_hash",
            "baseline_pathway_result_hash",
        ):
            object.__setattr__(
                self,
                field_name,
                _sha256(getattr(self, field_name), field_name),
            )
        if len(self.states) < 2 or len(self.steps) != len(self.states) - 1:
            raise ScientificPresentationError(
                "reaction-diagram presentation requires adjacent ordered states and steps"
            )
        if self.free_energy_unit != "eV" or self.potential_unit != "V":
            raise ScientificPresentationError("unsupported reaction-diagram presentation units")

    def to_dict(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "project_id": str(self.project_id),
            "source_result_hash": self.source_result_hash,
            "potential_view_result_hash": self.potential_view_result_hash,
            "pathway_definition_hash": self.pathway_definition_hash,
            "baseline_pathway_result_hash": self.baseline_pathway_result_hash,
            "baseline_conditions": self.baseline_conditions.to_dict(),
            "requested_conditions": self.requested_conditions.to_dict(),
            "free_energy_unit": self.free_energy_unit,
            "potential_unit": self.potential_unit,
            "states": [item.to_dict() for item in self.states],
            "steps": [item.to_dict() for item in self.steps],
            "descriptors": [item.to_dict() for item in self.descriptors],
            "sources": [item.to_dict() for item in self.sources],
        }


def build_reaction_diagram_presentation(
    source: ReactionDiagramDataset,
) -> ReactionDiagramPresentationDataset:
    """Project canonical reaction-diagram values without re-evaluating CHE or stoichiometry."""

    view = source.potential_view
    states = tuple(
        ReactionDiagramStatePresentation(
            index=index,
            state_key=state_key,
            cumulative_free_energy_ev=view.cumulative_state_free_energies_ev[index],
        )
        for index, state_key in enumerate(view.state_keys)
    )
    steps = tuple(
        ReactionDiagramStepPresentation(
            index=index,
            step_key=step.step_key,
            from_state_key=view.state_keys[index],
            to_state_key=view.state_keys[index + 1],
            che_coefficient=step.che_coefficient,
            potential_slope_ev_per_v=step.potential_slope_ev_per_v,
            baseline_delta_g_ev=step.baseline_delta_g_ev,
            target_delta_g_ev=step.target_delta_g_ev,
        )
        for index, step in enumerate(view.step_views)
    )
    descriptors = tuple(
        ReactionDiagramDescriptorPresentation(
            key=item.key,
            kind=item.kind.value,
            value=item.value,
            unit=item.unit.value,
            source_result_hash=item.source_result_hash,
            definition_hash=item.definition_hash,
            pathway_hash=item.pathway_hash,
            baseline_result_hash=item.baseline_result_hash,
        )
        for item in source.descriptor_definitions
    )
    sources = tuple(
        ReactionDiagramSourcePresentation(
            species_key=item.species_key,
            source_kind=item.source_kind.value,
            source_hash=item.source_hash,
            analysis_id=str(item.analysis_id),
            artifact_id=str(item.artifact_id),
            artifact_sha256=item.artifact_sha256,
            artifact_result_hash=item.artifact_result_hash,
        )
        for item in source.source_receipts
    )
    return ReactionDiagramPresentationDataset(
        project_id=source.project_id,
        source_result_hash=source.result_hash,
        potential_view_result_hash=view.result_hash,
        pathway_definition_hash=source.pathway_definition_hash,
        baseline_pathway_result_hash=source.baseline_pathway_result_hash,
        baseline_conditions=CHEConditionsPresentation.from_source(source.baseline_conditions),
        requested_conditions=CHEConditionsPresentation.from_source(source.requested_conditions),
        states=states,
        steps=steps,
        descriptors=descriptors,
        sources=sources,
    )

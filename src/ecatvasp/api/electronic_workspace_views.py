"""Canonical presentation payloads for the v1.1 Electronic Analysis Workspace."""

from __future__ import annotations

from ecatvasp.analysis import (
    BandCenterResult,
    CanonicalBaderResult,
    CanonicalCohpResult,
    CanonicalDosResult,
    CohpSpinSeries,
    LoadedChargeDifference,
    load_band_center_artifact,
    load_canonical_bader_artifact,
    load_canonical_cohp_artifact,
    load_canonical_dos_artifact,
    load_charge_difference_artifacts,
)
from ecatvasp.api.application import ApplicationServiceError
from ecatvasp.api.electronic_workspace_support import (
    BADER_ACF_OUTPUT,
    BADER_OUTPUT,
    BAND_CENTER_OUTPUT,
    CHARGE_DENSITY_OUTPUT,
    CHARGE_METADATA_OUTPUT,
    COHP_OUTPUT,
    COHP_RAW_OUTPUT,
    DOS_OUTPUT,
    ICOHP_RAW_OUTPUT,
    analysis_output,
    require_analysis,
    require_artifact,
)
from ecatvasp.domain import (
    Analysis,
    AnalysisProducerRef,
    AnalysisType,
    Artifact,
    ArtifactType,
)
from ecatvasp.storage import ProjectBundle, ProjectStore


def build_analysis_view(
    *,
    store: ProjectStore,
    bundle: ProjectBundle,
    analysis: Analysis,
) -> dict[str, object]:
    if analysis.analysis_type is AnalysisType.DOS:
        return _dos_view(store, bundle, analysis)
    if analysis.analysis_type is AnalysisType.BADER:
        return _bader_view(store, bundle, analysis)
    if analysis.analysis_type is AnalysisType.CHARGE_DIFFERENCE:
        return _charge_difference_view(store, bundle, analysis)
    if analysis.analysis_type is AnalysisType.COHP:
        return _cohp_view(store, bundle, analysis)
    if analysis.analysis_type is AnalysisType.BAND_CENTER:
        return _band_center_view(store, bundle, analysis)
    if analysis.analysis_type is AnalysisType.PDOS:
        raise ApplicationServiceError(
            "standalone PDOS Analysis has no v0.7 canonical loader; PDOS is exposed "
            "through atom/orbital series in canonical DOS"
        )
    raise ApplicationServiceError("unsupported electronic Analysis type")


def _dos_view(
    store: ProjectStore,
    bundle: ProjectBundle,
    analysis: Analysis,
) -> dict[str, object]:
    artifact = analysis_output(
        bundle,
        analysis,
        filename=DOS_OUTPUT,
        artifact_type=ArtifactType.DERIVED_DATASET,
    )
    result = load_canonical_dos_artifact(
        project_root=store.root,
        analysis=analysis,
        artifact=artifact,
    )
    return _dos_payload(result, artifact)


def _bader_view(
    store: ProjectStore,
    bundle: ProjectBundle,
    analysis: Analysis,
) -> dict[str, object]:
    acf = analysis_output(
        bundle,
        analysis,
        filename=BADER_ACF_OUTPUT,
        artifact_type=ArtifactType.ACF_DAT,
    )
    result_artifact = analysis_output(
        bundle,
        analysis,
        filename=BADER_OUTPUT,
        artifact_type=ArtifactType.DERIVED_DATASET,
    )
    result = load_canonical_bader_artifact(
        project_root=store.root,
        analysis=analysis,
        acf_artifact=acf,
        result_artifact=result_artifact,
    )
    return _bader_payload(result, acf, result_artifact)


def _charge_difference_view(
    store: ProjectStore,
    bundle: ProjectBundle,
    analysis: Analysis,
) -> dict[str, object]:
    density = analysis_output(
        bundle,
        analysis,
        filename=CHARGE_DENSITY_OUTPUT,
        artifact_type=ArtifactType.DERIVED_DATASET,
    )
    metadata = analysis_output(
        bundle,
        analysis,
        filename=CHARGE_METADATA_OUTPUT,
        artifact_type=ArtifactType.DERIVED_DATASET,
    )
    loaded = load_charge_difference_artifacts(
        project_root=store.root,
        analysis=analysis,
        density_artifact=density,
        metadata_artifact=metadata,
    )
    return _charge_difference_payload(loaded, density, metadata)


def _cohp_view(
    store: ProjectStore,
    bundle: ProjectBundle,
    analysis: Analysis,
) -> dict[str, object]:
    cohpcar = analysis_output(
        bundle,
        analysis,
        filename=COHP_RAW_OUTPUT,
        artifact_type=ArtifactType.COHPCAR_LOBSTER,
    )
    icohplist = analysis_output(
        bundle,
        analysis,
        filename=ICOHP_RAW_OUTPUT,
        artifact_type=ArtifactType.ICOHPLIST_LOBSTER,
    )
    result_artifact = analysis_output(
        bundle,
        analysis,
        filename=COHP_OUTPUT,
        artifact_type=ArtifactType.DERIVED_DATASET,
    )
    result = load_canonical_cohp_artifact(
        project_root=store.root,
        analysis=analysis,
        cohpcar_artifact=cohpcar,
        icohplist_artifact=icohplist,
        result_artifact=result_artifact,
    )
    return _cohp_payload(result, cohpcar, icohplist, result_artifact)


def _band_center_view(
    store: ProjectStore,
    bundle: ProjectBundle,
    analysis: Analysis,
) -> dict[str, object]:
    if len(analysis.input_artifact_ids) != 1:
        raise ApplicationServiceError("band-center Analysis requires one canonical DOS input")
    source_artifact = require_artifact(bundle, analysis.input_artifact_ids[0])
    producer = source_artifact.producer
    if not isinstance(producer, AnalysisProducerRef):
        raise ApplicationServiceError(
            "band-center source Artifact is not Analysis-produced canonical DOS"
        )
    source_analysis = require_analysis(bundle, producer.id)
    if source_analysis.analysis_type is not AnalysisType.DOS:
        raise ApplicationServiceError("band-center source Analysis is not canonical DOS")
    artifact = analysis_output(
        bundle,
        analysis,
        filename=BAND_CENTER_OUTPUT,
        artifact_type=ArtifactType.DERIVED_DATASET,
    )
    result = load_band_center_artifact(
        project_root=store.root,
        source_analysis=source_analysis,
        source_artifact=source_artifact,
        analysis=analysis,
        artifact=artifact,
    )
    return _band_center_payload(result, source_analysis, source_artifact, artifact)


def _dos_payload(result: CanonicalDosResult, artifact: Artifact) -> dict[str, object]:
    return {
        "kind": "dos",
        "structure_snapshot_id": str(result.structure_snapshot_id),
        "source_artifact_id": str(artifact.id),
        "energy_reference": result.energy_axis.reference.value,
        "fermi_energy_ev": result.energy_axis.fermi_energy_ev,
        "energies_ev_native": list(result.energy_axis.energies_ev),
        "energies_ev_relative_to_fermi": list(result.energy_axis.relative_to_fermi()),
        "series": [
            {
                "scope": item.scope.value,
                "spin": item.spin.value,
                "atom_uid": str(item.atom_uid) if item.atom_uid is not None else None,
                "element": item.element,
                "orbital": (
                    {
                        "label": item.orbital.label,
                        "angular_momentum": item.orbital.angular_momentum,
                    }
                    if item.orbital is not None
                    else None
                ),
                "values": list(item.values),
            }
            for item in result.series
        ],
        "display_contract": {
            "native_axis_is_canonical": True,
            "fermi_relative_axis_is_explicit_transform": True,
            "spin_down_may_be_mirrored_for_display": True,
        },
    }


def _bader_payload(
    result: CanonicalBaderResult,
    acf: Artifact,
    artifact: Artifact,
) -> dict[str, object]:
    return {
        "kind": "bader",
        "structure_snapshot_id": str(result.structure_snapshot_id),
        "acf_artifact_id": str(acf.id),
        "source_artifact_id": str(artifact.id),
        "reference_mode": result.reference_mode.value,
        "number_of_electrons": result.number_of_electrons,
        "vacuum_charge_e": result.vacuum_charge_e,
        "vacuum_volume_angstrom3": result.vacuum_volume_angstrom3,
        "sites": [
            {
                "atom_uid": str(item.atom_uid),
                "electron_count": item.electron_count,
                "min_distance_angstrom": item.min_distance_angstrom,
                "basin_volume_angstrom3": item.basin_volume_angstrom3,
            }
            for item in result.sites
        ],
        "interpretation_contract": "raw_basin_facts_no_oxidation_state_inference",
    }


def _charge_difference_payload(
    loaded: LoadedChargeDifference,
    density: Artifact,
    metadata: Artifact,
) -> dict[str, object]:
    item = loaded.metadata
    return {
        "kind": "charge_difference",
        "density_artifact_id": str(density.id),
        "metadata_artifact_id": str(metadata.id),
        "grid_shape_xyz": list(item.grid_shape_xyz),
        "cell_volume_angstrom3": item.cell_volume_angstrom3,
        "voxel_volume_angstrom3": item.voxel_volume_angstrom3,
        "density_unit": item.density_unit,
        "axis_order": item.axis_order,
        "dtype": item.dtype,
        "delta_convention": item.delta_convention,
        "combined_electron_integral": item.combined_electron_integral,
        "slab_electron_integral": item.slab_electron_integral,
        "adsorbate_electron_integral": item.adsorbate_electron_integral,
        "delta_electron_integral": item.delta_electron_integral,
        "density_min": item.density_min,
        "density_max": item.density_max,
        "volume_payload_included": False,
    }


def _cohp_payload(
    result: CanonicalCohpResult,
    cohpcar: Artifact,
    icohplist: Artifact,
    artifact: Artifact,
) -> dict[str, object]:
    return {
        "kind": "cohp",
        "structure_snapshot_id": str(result.structure_snapshot_id),
        "cohpcar_artifact_id": str(cohpcar.id),
        "icohplist_artifact_id": str(icohplist.id),
        "source_artifact_id": str(artifact.id),
        "energy_reference": result.energy_reference.value,
        "source_fermi_energy_ev": result.source_fermi_energy_ev,
        "energies_ev_relative_to_fermi": list(result.energies_ev_relative_to_fermi),
        "average_series": [_cohp_series_payload(item) for item in result.average_series],
        "interactions": [
            {
                "source_index": item.source_index,
                "source_label": item.source_label,
                "atom_uid_a": str(item.atom_uid_a),
                "atom_uid_b": str(item.atom_uid_b),
                "element_a": item.element_a,
                "element_b": item.element_b,
                "bond_length_angstrom": item.bond_length_angstrom,
                "cell_a": list(item.cell_a) if item.cell_a is not None else None,
                "cell_b": list(item.cell_b) if item.cell_b is not None else None,
                "orbital_a": item.orbital_a,
                "orbital_b": item.orbital_b,
                "series": [_cohp_series_payload(series) for series in item.series],
            }
            for item in result.interactions
        ],
        "display_contract": {
            "native_cohp_is_canonical": True,
            "negative_cohp_is_explicit_transform": True,
        },
    }


def _cohp_series_payload(series: CohpSpinSeries) -> dict[str, object]:
    return {
        "spin": series.spin.value,
        "cohp_values_native": list(series.cohp_values),
        "negative_cohp_values": [-value for value in series.cohp_values],
        "icohp_values_native": list(series.icohp_values),
        "icohp_at_fermi_ev_native": series.icohp_at_fermi_ev,
    }


def _band_center_payload(
    result: BandCenterResult,
    source_analysis: Analysis,
    source_artifact: Artifact,
    artifact: Artifact,
) -> dict[str, object]:
    parameters = result.parameters
    selector = parameters.selector
    return {
        "kind": "band_center",
        "source_analysis_id": str(source_analysis.id),
        "source_artifact_id": str(source_artifact.id),
        "result_artifact_id": str(artifact.id),
        "structure_snapshot_id": str(result.structure_snapshot_id),
        "descriptor_kind": parameters.kind.value,
        "center_ev": result.center_ev,
        "zeroth_moment_states": result.zeroth_moment_states,
        "first_moment_ev_states": result.first_moment_ev_states,
        "quadrature_point_count": result.quadrature_point_count,
        "contributing_series_count": result.contributing_series_count,
        "selector": {
            "scope": selector.scope.value,
            "spin": selector.spin.value,
            "atom_uid": str(selector.atom_uid) if selector.atom_uid is not None else None,
            "element": selector.element,
        },
        "energy_reference": parameters.energy_reference.value,
        "window_lower_ev": parameters.window_lower_ev,
        "window_upper_ev": parameters.window_upper_ev,
        "integration_rule": parameters.integration_rule.value,
        "normalization": parameters.normalization.value,
    }


__all__ = ["build_analysis_view"]

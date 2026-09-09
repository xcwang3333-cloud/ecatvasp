"""Task-oriented thermochemistry/reaction application authority for v1.1 Block 7.

The service composes the frozen v0.8 thermochemistry/electrocatalysis authorities. Scientific
energies, mode policies, gas-reference semantics, CHE/reaction equations, descriptor identity, and
freshness remain in the pre-existing Python core. This module owns project-scoped evidence
resolution, exact-result reuse, persistence, and presentation only.
"""

from __future__ import annotations

from uuid import UUID

from ecatvasp.api.application import ApplicationServiceError, ProjectApplicationService
from ecatvasp.api.thermochemistry_workspace_support import (
    GAS_OUTPUT,
    HARMONIC_OUTPUT,
    ResolvedFrequencySource,
    canonical_analysis_payload,
    find_existing_thermochemistry,
    observed_artifact_state,
    persist_analysis_graph,
    require_analysis,
    resolve_frequency_source,
    source_is_unchanged,
    thermochemistry_projection_payload,
)
from ecatvasp.domain import (
    Analysis,
    AnalysisId,
    AnalysisType,
    Artifact,
    Calculation,
    CalculationId,
    CalculationType,
)
from ecatvasp.storage import ProjectBundle
from ecatvasp.thermo import (
    CANONICAL_HARMONIC_THERMOCHEMISTRY_FORMAT,
    CANONICAL_HARMONIC_THERMOCHEMISTRY_VERSION,
    CANONICAL_IDEAL_GAS_THERMOCHEMISTRY_FORMAT,
    CANONICAL_IDEAL_GAS_THERMOCHEMISTRY_VERSION,
    HARMONIC_THERMOCHEMISTRY_TOOL_NAME,
    HARMONIC_THERMOCHEMISTRY_TOOL_VERSION,
    IDEAL_GAS_THERMOCHEMISTRY_TOOL_NAME,
    IDEAL_GAS_THERMOCHEMISTRY_TOOL_VERSION,
    INITIAL_GAS_REFERENCE_REGISTRY,
    DurableGasThermochemistry,
    DurableHarmonicThermochemistry,
    ElectronicEnergyKind,
    ElectronicEntropyPolicy,
    GasAtomicMass,
    GasGeometryKind,
    GasMoleculeModel,
    GasReferenceDefinition,
    GasReferenceSpecies,
    ImaginaryModePolicy,
    LowFrequencyPolicy,
    ModeExclusion,
    ThermochemicalConditions,
    ThermochemicalStandardState,
    ThermochemistryIdentity,
    ThermochemistrySubjectKind,
    VibrationalModePolicy,
    materialize_harmonic_thermochemistry,
    materialize_ideal_gas_thermochemistry,
)


class ProjectThermochemistryApplicationService(ProjectApplicationService):
    """Project-scoped application seam over canonical v0.8 thermochemistry/reaction science."""

    def catalog(self) -> dict[str, object]:
        bundle = self.store.open()
        observations, invalid_ids = observed_artifact_state(self.store.root, bundle)
        frequency_sources = [
            self._frequency_source_row(calculation)
            for calculation in bundle.calculations
            if calculation.calculation_type
            in {CalculationType.FREQUENCY, CalculationType.GAS_FREQUENCY}
        ]
        analyses = [
            self._analysis_catalog_row(
                bundle=bundle,
                analysis=analysis,
                observations=observations,
                invalid_ids=invalid_ids,
            )
            for analysis in bundle.analyses
            if analysis.analysis_type
            in {AnalysisType.THERMOCHEMISTRY, AnalysisType.REACTION_DIAGRAM}
        ]
        return {
            "project_id": str(bundle.project.id),
            "project_name": bundle.project.name,
            "frequency_sources": frequency_sources,
            "gas_reference_registry": [
                {
                    "species": item.species.value,
                    "state_label": item.state_label,
                    "reference_hash": item.content_hash,
                }
                for item in INITIAL_GAS_REFERENCE_REGISTRY
            ],
            "analyses": analyses,
        }

    def materialize_harmonic(
        self,
        *,
        calculation_id: CalculationId,
        subject_kind: ThermochemistrySubjectKind,
        temperature_k: float,
        electronic_energy_kind: ElectronicEnergyKind,
        electronic_entropy_policy: ElectronicEntropyPolicy,
        frequency_cutoff_cm_inverse: float,
        imaginary_mode_policy: ImaginaryModePolicy,
        low_frequency_policy: LowFrequencyPolicy,
        exclusions: tuple[ModeExclusion, ...] = (),
    ) -> dict[str, object]:
        if subject_kind not in {
            ThermochemistrySubjectKind.SURFACE,
            ThermochemistrySubjectKind.ADSORBATE,
        }:
            raise ApplicationServiceError(
                "harmonic workspace materialization supports surface or adsorbate subjects only"
            )
        source = resolve_frequency_source(
            store=self.store,
            calculation_id=calculation_id,
            expected_type=CalculationType.FREQUENCY,
        )
        identity = ThermochemistryIdentity(
            subject_kind=subject_kind,
            conditions=ThermochemicalConditions(
                temperature_k=temperature_k,
                standard_state=ThermochemicalStandardState.SURFACE_FIXED_CELL,
            ),
            electronic_energy_kind=electronic_energy_kind,
            electronic_entropy_policy=electronic_entropy_policy,
            vibrational_policy=VibrationalModePolicy(
                frequency_cutoff_cm_inverse=frequency_cutoff_cm_inverse,
                imaginary_mode_policy=imaginary_mode_policy,
                low_frequency_policy=low_frequency_policy,
                exclusions=exclusions,
            ),
        )
        existing = find_existing_thermochemistry(
            root=self.store.root,
            bundle=source.bundle,
            source=source,
            identity=identity,
            tool=HARMONIC_THERMOCHEMISTRY_TOOL_NAME,
            tool_version=HARMONIC_THERMOCHEMISTRY_TOOL_VERSION,
            filename=HARMONIC_OUTPUT,
            expected_format=CANONICAL_HARMONIC_THERMOCHEMISTRY_FORMAT,
            expected_version=CANONICAL_HARMONIC_THERMOCHEMISTRY_VERSION,
        )
        if existing is not None:
            return _materialization_receipt(
                analysis=existing[0],
                artifact=existing[1],
                calculation_id=source.calculation.id,
                reused=True,
            )
        materialization = materialize_harmonic_thermochemistry(
            project_root=self.store.root,
            calculation=source.calculation,
            method_fingerprint=source.fingerprint,
            structure_snapshot=source.snapshot,
            source_analysis=source.source_analysis,
            source_artifact=source.source_artifact,
            source_result=source.result,
            identity=identity,
        )
        self._persist_harmonic(source=source, materialization=materialization)
        return _materialization_receipt(
            analysis=materialization.analysis,
            artifact=materialization.artifact,
            calculation_id=source.calculation.id,
            reused=False,
        )

    def materialize_gas_reference(
        self,
        *,
        calculation_id: CalculationId,
        species: GasReferenceSpecies,
        temperature_k: float,
        pressure_pa: float,
        standard_state: ThermochemicalStandardState,
        electronic_energy_kind: ElectronicEnergyKind,
        electronic_entropy_policy: ElectronicEntropyPolicy,
        geometry_kind: GasGeometryKind,
        symmetry_number: int,
        spin_multiplicity: int,
        atomic_masses: tuple[GasAtomicMass, ...],
        frequency_cutoff_cm_inverse: float,
        imaginary_mode_policy: ImaginaryModePolicy,
        low_frequency_policy: LowFrequencyPolicy,
        exclusions: tuple[ModeExclusion, ...] = (),
    ) -> dict[str, object]:
        if standard_state not in {
            ThermochemicalStandardState.IDEAL_GAS_1_BAR,
            ThermochemicalStandardState.IDEAL_GAS_1_ATM,
        }:
            raise ApplicationServiceError(
                "gas reference requires IDEAL_GAS_1_BAR or IDEAL_GAS_1_ATM standard state"
            )
        source = resolve_frequency_source(
            store=self.store,
            calculation_id=calculation_id,
            expected_type=CalculationType.GAS_FREQUENCY,
        )
        reference = GasReferenceDefinition(species=species)
        identity = ThermochemistryIdentity(
            subject_kind=ThermochemistrySubjectKind.GAS,
            conditions=ThermochemicalConditions(
                temperature_k=temperature_k,
                standard_state=standard_state,
                pressure_pa=pressure_pa,
            ),
            electronic_energy_kind=electronic_energy_kind,
            electronic_entropy_policy=electronic_entropy_policy,
            vibrational_policy=VibrationalModePolicy(
                frequency_cutoff_cm_inverse=frequency_cutoff_cm_inverse,
                imaginary_mode_policy=imaginary_mode_policy,
                low_frequency_policy=low_frequency_policy,
                exclusions=exclusions,
            ),
            gas_model=GasMoleculeModel(
                geometry_kind=geometry_kind,
                symmetry_number=symmetry_number,
                spin_multiplicity=spin_multiplicity,
                atomic_masses=atomic_masses,
            ),
        )
        existing = find_existing_thermochemistry(
            root=self.store.root,
            bundle=source.bundle,
            source=source,
            identity=identity,
            tool=IDEAL_GAS_THERMOCHEMISTRY_TOOL_NAME,
            tool_version=IDEAL_GAS_THERMOCHEMISTRY_TOOL_VERSION,
            filename=GAS_OUTPUT,
            expected_format=CANONICAL_IDEAL_GAS_THERMOCHEMISTRY_FORMAT,
            expected_version=CANONICAL_IDEAL_GAS_THERMOCHEMISTRY_VERSION,
            reference=reference,
        )
        if existing is not None:
            return _materialization_receipt(
                analysis=existing[0],
                artifact=existing[1],
                calculation_id=source.calculation.id,
                reused=True,
            )
        materialization = materialize_ideal_gas_thermochemistry(
            project_root=self.store.root,
            reference=reference,
            calculation=source.calculation,
            method_fingerprint=source.fingerprint,
            structure_snapshot=source.snapshot,
            source_analysis=source.source_analysis,
            source_artifact=source.source_artifact,
            source_result=source.result,
            identity=identity,
        )
        self._persist_gas(source=source, materialization=materialization)
        return _materialization_receipt(
            analysis=materialization.analysis,
            artifact=materialization.artifact,
            calculation_id=source.calculation.id,
            reused=False,
        )

    def analysis_view(self, *, analysis_id: AnalysisId) -> dict[str, object]:
        bundle = self.store.open()
        analysis = require_analysis(bundle, analysis_id)
        if analysis.analysis_type not in {
            AnalysisType.THERMOCHEMISTRY,
            AnalysisType.REACTION_DIAGRAM,
        }:
            raise ApplicationServiceError(
                "Analysis is not thermochemistry/reaction scientific data"
            )
        observations, invalid_ids = observed_artifact_state(self.store.root, bundle)
        freshness = thermochemistry_projection_payload(
            store=self.store,
            bundle=bundle,
            analysis=analysis,
            current_hash_overrides=observations,
            invalid_ids=invalid_ids,
        )
        canonical = canonical_analysis_payload(
            root=self.store.root,
            bundle=bundle,
            analysis=analysis,
        )
        payload = canonical.payload
        if analysis.analysis_type is AnalysisType.REACTION_DIAGRAM:
            view: dict[str, object] = {
                "kind": "reaction_diagram",
                "dataset_hash": payload.get("dataset_hash"),
                "dataset": payload.get("dataset"),
            }
        else:
            view = {
                "kind": _thermochemistry_kind(analysis),
                "source_receipt": payload.get("source_receipt"),
                "result_hash": payload.get("result_hash"),
                "result": payload.get("result"),
            }
        return {
            "project_id": str(bundle.project.id),
            "analysis_id": str(analysis.id),
            "analysis_type": analysis.analysis_type.value,
            "analysis_status": analysis.status.value,
            "tool": analysis.tool,
            "tool_version": analysis.tool_version,
            "artifact_id": str(canonical.artifact.id),
            "freshness": freshness,
            "view": view,
        }

    def _frequency_source_row(self, calculation: Calculation) -> dict[str, object]:
        row: dict[str, object] = {
            "calculation_id": str(calculation.id),
            "calculation_type": calculation.calculation_type.value,
            "scientific_status": calculation.status.value,
            "source_ready": False,
            "attempt_id": None,
            "source_analysis_id": None,
            "source_artifact_id": None,
            "reason": "frequency source is not resolved",
        }
        try:
            source = resolve_frequency_source(
                store=self.store,
                calculation_id=calculation.id,
                expected_type=calculation.calculation_type,
            )
        except ValueError as error:
            row["reason"] = str(error)
            return row
        row.update(
            {
                "source_ready": True,
                "attempt_id": str(source.attempt.id),
                "source_analysis_id": str(source.source_analysis.id),
                "source_artifact_id": str(source.source_artifact.id),
                "reason": "exact persisted frequency result is canonical-replay verified",
            }
        )
        return row

    def _analysis_catalog_row(
        self,
        *,
        bundle: ProjectBundle,
        analysis: Analysis,
        observations: dict[UUID, str],
        invalid_ids: set[UUID],
    ) -> dict[str, object]:
        freshness = thermochemistry_projection_payload(
            store=self.store,
            bundle=bundle,
            analysis=analysis,
            current_hash_overrides=observations,
            invalid_ids=invalid_ids,
        )
        return {
            "analysis_id": str(analysis.id),
            "analysis_type": analysis.analysis_type.value,
            "status": analysis.status.value,
            "tool": analysis.tool,
            "tool_version": analysis.tool_version,
            "input_artifact_count": len(analysis.input_artifact_ids),
            "freshness": freshness,
            "view_supported": True,
        }

    def _persist_harmonic(
        self,
        *,
        source: ResolvedFrequencySource,
        materialization: DurableHarmonicThermochemistry,
    ) -> None:
        current = self.store.open()
        if not source_is_unchanged(current, source):
            raise ApplicationServiceError(
                "frequency source changed while harmonic thermochemistry was materializing"
            )
        persist_analysis_graph(
            store=self.store,
            bundle=current,
            analysis=materialization.analysis,
            artifacts=(materialization.artifact,),
            provenance_records=materialization.provenance_records,
            dependency_records=materialization.dependency_records,
        )

    def _persist_gas(
        self,
        *,
        source: ResolvedFrequencySource,
        materialization: DurableGasThermochemistry,
    ) -> None:
        current = self.store.open()
        if not source_is_unchanged(current, source):
            raise ApplicationServiceError(
                "frequency source changed while gas reference was materializing"
            )
        persist_analysis_graph(
            store=self.store,
            bundle=current,
            analysis=materialization.analysis,
            artifacts=(materialization.artifact,),
            provenance_records=materialization.provenance_records,
            dependency_records=materialization.dependency_records,
        )


def _thermochemistry_kind(analysis: Analysis) -> str:
    if analysis.tool == HARMONIC_THERMOCHEMISTRY_TOOL_NAME:
        return "harmonic_surface_adsorbate"
    if analysis.tool == IDEAL_GAS_THERMOCHEMISTRY_TOOL_NAME:
        return "ideal_gas_reference"
    return "reference_correction"


def _materialization_receipt(
    *,
    analysis: Analysis,
    artifact: Artifact,
    calculation_id: CalculationId,
    reused: bool,
) -> dict[str, object]:
    return {
        "analysis_id": str(analysis.id),
        "artifact_id": str(artifact.id),
        "calculation_id": str(calculation_id),
        "reused": reused,
    }


__all__ = ["ProjectThermochemistryApplicationService"]

"""Task-oriented electronic-analysis application authority for v1.1 Block 6.

The service resolves managed ProjectStore evidence and composes the frozen v0.7
canonical electronic-analysis authorities.  It owns application orchestration only;
scientific parsing, identity, freshness, and descriptor semantics remain in Python
core modules that predate this desktop workspace.
"""

from __future__ import annotations

from ecatvasp.analysis import (
    BandCenterEnergyReference,
    BandCenterKind,
    BandCenterParameters,
    BandCenterSelector,
    BandCenterSpinMode,
    CanonicalDosIntake,
    DurableBandCenter,
    DurableDosMaterialization,
    ProjectionScope,
    materialize_band_center_analysis,
    materialize_canonical_dos_analysis,
    parse_vasp_doscar,
)
from ecatvasp.api.application import ApplicationServiceError, ProjectApplicationService
from ecatvasp.api.electronic_workspace_support import (
    ATOM_MAP_OUTPUT,
    DOS_OUTPUT,
    DosSource,
    analysis_catalog_row,
    analysis_output,
    analysis_projection,
    analysis_projection_payload,
    find_existing_band_center,
    find_existing_dos,
    latest_attempt,
    matching_analysis_ids,
    observed_artifact_state,
    persist_analysis_graph,
    require_analysis,
    require_artifact,
    require_calculation,
    require_fingerprint,
    require_snapshot,
    require_source_unchanged,
    single_artifact,
    verified_local_bytes,
)
from ecatvasp.api.electronic_workspace_views import build_analysis_view
from ecatvasp.domain import (
    Analysis,
    AnalysisId,
    AnalysisType,
    Artifact,
    ArtifactType,
    AtomUid,
    Calculation,
    CalculationId,
    CalculationProducerRef,
    CalculationScientificStatus,
    CalculationType,
    ExecutionAttemptProducerRef,
)
from ecatvasp.storage import ProjectBundle
from ecatvasp.workflow import (
    ELECTRONIC_ANALYSIS_TYPES,
    ElectronicAnalysisScientificState,
    WorkflowStepReadiness,
)


class ProjectElectronicAnalysisApplicationService(ProjectApplicationService):
    """Project-scoped application seam over canonical v0.7 electronic analysis."""

    def catalog(self) -> dict[str, object]:
        bundle = self.store.open()
        observations, invalid_ids = observed_artifact_state(self.store.root, bundle)
        return {
            "project_id": str(bundle.project.id),
            "project_name": bundle.project.name,
            "dos_sources": [
                self._dos_source_row(bundle, calculation)
                for calculation in bundle.calculations
                if calculation.calculation_type is CalculationType.DOS_STATIC
            ],
            "analyses": [
                analysis_catalog_row(
                    store=self.store,
                    bundle=bundle,
                    analysis=analysis,
                    current_hash_overrides=observations,
                    invalid_ids=invalid_ids,
                )
                for analysis in bundle.analyses
                if analysis.analysis_type in ELECTRONIC_ANALYSIS_TYPES
            ],
        }

    def materialize_dos(self, *, calculation_id: CalculationId) -> dict[str, object]:
        source = self._resolve_dos_source(calculation_id)
        intake = self._parse_dos_source(source)
        existing = find_existing_dos(
            root=self.store.root,
            bundle=source.bundle,
            doscar=source.doscar,
            atom_map=source.atom_map,
            expected=intake.result,
        )
        if existing is not None:
            analysis, artifact = existing
            return _materialization_receipt(
                analysis=analysis,
                artifact=artifact,
                source_id=str(source.calculation.id),
                source_field="calculation_id",
                reused=True,
            )

        materialization = materialize_canonical_dos_analysis(
            project_root=self.store.root,
            calculation=source.calculation,
            execution_attempt=source.attempt,
            doscar_artifact=source.doscar,
            atom_index_map_artifact=source.atom_map,
            intake=intake,
        )
        self._persist_dos(source=source, materialization=materialization)
        return _materialization_receipt(
            analysis=materialization.analysis,
            artifact=materialization.artifact,
            source_id=str(source.calculation.id),
            source_field="calculation_id",
            reused=False,
        )

    def analysis_view(self, *, analysis_id: AnalysisId) -> dict[str, object]:
        bundle = self.store.open()
        analysis = require_analysis(bundle, analysis_id)
        if analysis.analysis_type not in ELECTRONIC_ANALYSIS_TYPES:
            raise ApplicationServiceError("Analysis is not an electronic-analysis type")
        observations, invalid_ids = observed_artifact_state(self.store.root, bundle)
        freshness = analysis_projection_payload(
            store=self.store,
            bundle=bundle,
            analysis=analysis,
            current_hash_overrides=observations,
            invalid_ids=invalid_ids,
        )
        view = build_analysis_view(store=self.store, bundle=bundle, analysis=analysis)
        return {
            "project_id": str(bundle.project.id),
            "analysis_id": str(analysis.id),
            "analysis_type": analysis.analysis_type.value,
            "analysis_status": analysis.status.value,
            "tool": analysis.tool,
            "tool_version": analysis.tool_version,
            "freshness": freshness,
            "view": view,
        }

    def materialize_band_center(
        self,
        *,
        source_analysis_id: AnalysisId,
        kind: BandCenterKind,
        scope: ProjectionScope,
        spin: BandCenterSpinMode,
        atom_uid: AtomUid | None,
        element: str | None,
        energy_reference: BandCenterEnergyReference,
        window_lower_ev: float,
        window_upper_ev: float,
    ) -> dict[str, object]:
        bundle = self.store.open()
        source_analysis = require_analysis(bundle, source_analysis_id)
        if source_analysis.analysis_type is not AnalysisType.DOS:
            raise ApplicationServiceError("band-center source must be canonical DOS Analysis")
        source_artifact = analysis_output(
            bundle,
            source_analysis,
            filename=DOS_OUTPUT,
            artifact_type=ArtifactType.DERIVED_DATASET,
        )
        observations, invalid_ids = observed_artifact_state(self.store.root, bundle)
        projection = analysis_projection(
            store=self.store,
            bundle=bundle,
            analysis=source_analysis,
            current_hash_overrides=observations,
            invalid_ids=invalid_ids,
        )
        if (
            projection.scientific_state is not ElectronicAnalysisScientificState.COMPLETED
            or projection.readiness is not WorkflowStepReadiness.SATISFIED
        ):
            reasons = ", ".join(projection.reason_codes) or "source is not current"
            raise ApplicationServiceError(
                f"band-center source canonical DOS is not fresh/satisfied: {reasons}"
            )

        parameters = BandCenterParameters(
            kind=kind,
            selector=BandCenterSelector(
                scope=scope,
                spin=spin,
                atom_uid=atom_uid,
                element=element,
            ),
            energy_reference=energy_reference,
            window_lower_ev=window_lower_ev,
            window_upper_ev=window_upper_ev,
        )
        existing = find_existing_band_center(
            root=self.store.root,
            bundle=bundle,
            source_analysis=source_analysis,
            source_artifact=source_artifact,
            parameters=parameters,
        )
        if existing is not None:
            analysis, artifact = existing
            return _materialization_receipt(
                analysis=analysis,
                artifact=artifact,
                source_id=str(source_analysis.id),
                source_field="source_analysis_id",
                reused=True,
            )

        materialization = materialize_band_center_analysis(
            project_root=self.store.root,
            source_analysis=source_analysis,
            source_artifact=source_artifact,
            parameters=parameters,
        )
        self._persist_band_center(
            original_bundle=bundle,
            source_analysis=source_analysis,
            source_artifact=source_artifact,
            materialization=materialization,
        )
        return _materialization_receipt(
            analysis=materialization.analysis,
            artifact=materialization.artifact,
            source_id=str(source_analysis.id),
            source_field="source_analysis_id",
            reused=False,
        )

    def _dos_source_row(
        self,
        bundle: ProjectBundle,
        calculation: Calculation,
    ) -> dict[str, object]:
        latest = latest_attempt(bundle, calculation.id)
        row: dict[str, object] = {
            "calculation_id": str(calculation.id),
            "scientific_status": calculation.status.value,
            "latest_attempt_id": str(latest.id) if latest is not None else None,
            "latest_attempt_status": latest.status.value if latest is not None else None,
            "materialization_ready": False,
            "materialized_analysis_id": None,
            "reason": "DOS source is not resolved",
        }
        try:
            source = self._resolve_dos_source(calculation.id, bundle=bundle)
            intake = self._parse_dos_source(source)
            existing = find_existing_dos(
                root=self.store.root,
                bundle=bundle,
                doscar=source.doscar,
                atom_map=source.atom_map,
                expected=intake.result,
            )
        except ValueError as error:
            row["reason"] = str(error)
            return row
        if existing is not None:
            row["materialized_analysis_id"] = str(existing[0].id)
            row["reason"] = "exact canonical DOS already materialized"
        else:
            row["materialization_ready"] = True
            row["reason"] = "exact managed DOSCAR and atom map are canonical-parse-ready"
        return row

    def _resolve_dos_source(
        self,
        calculation_id: CalculationId,
        *,
        bundle: ProjectBundle | None = None,
    ) -> DosSource:
        current = self.store.open() if bundle is None else bundle
        calculation = require_calculation(current, calculation_id)
        if calculation.calculation_type is not CalculationType.DOS_STATIC:
            raise ApplicationServiceError("DOS materialization requires DOS_STATIC Calculation")
        if calculation.status is not CalculationScientificStatus.CONVERGED:
            raise ApplicationServiceError(
                "DOS materialization requires scientifically converged Calculation"
            )
        attempt = latest_attempt(current, calculation.id)
        if attempt is None:
            raise ApplicationServiceError("DOS Calculation has no managed ExecutionAttempt")
        fingerprint = require_fingerprint(current, calculation)
        snapshot = require_snapshot(current, calculation)
        doscar = single_artifact(
            current,
            producer=ExecutionAttemptProducerRef(attempt.id),
            artifact_type=ArtifactType.DOSCAR,
            filename="DOSCAR",
            label="latest managed DOSCAR",
        )
        atom_map = single_artifact(
            current,
            producer=CalculationProducerRef(calculation.id),
            artifact_type=ArtifactType.DERIVED_DATASET,
            filename=ATOM_MAP_OUTPUT,
            label="frozen atom-index-map.json",
        )
        return DosSource(
            bundle=current,
            calculation=calculation,
            attempt=attempt,
            fingerprint=fingerprint,
            snapshot=snapshot,
            doscar=doscar,
            atom_map=atom_map,
            doscar_bytes=verified_local_bytes(
                root=self.store.root,
                artifact=doscar,
                expected_filename="DOSCAR",
                label="DOSCAR",
            ),
            atom_map_bytes=verified_local_bytes(
                root=self.store.root,
                artifact=atom_map,
                expected_filename=ATOM_MAP_OUTPUT,
                label="atom-index-map.json",
            ),
        )

    @staticmethod
    def _parse_dos_source(source: DosSource) -> CanonicalDosIntake:
        return parse_vasp_doscar(
            doscar_bytes=source.doscar_bytes,
            atom_index_map_bytes=source.atom_map_bytes,
            structure_snapshot_id=source.snapshot.id,
            spin_treatment=source.fingerprint.method.spin_treatment,
            soc=source.fingerprint.method.soc,
        )

    def _persist_dos(
        self,
        *,
        source: DosSource,
        materialization: DurableDosMaterialization,
    ) -> None:
        current = self.store.open()
        require_source_unchanged(current, source)
        if matching_analysis_ids(
            current,
            AnalysisType.DOS,
            (source.doscar.id, source.atom_map.id),
        ):
            raise ApplicationServiceError(
                "canonical DOS Analysis appeared while materialization was in progress"
            )
        persist_analysis_graph(
            store=self.store,
            bundle=current,
            analysis=materialization.analysis,
            artifacts=(materialization.artifact,),
            provenance_records=materialization.provenance_records,
            dependency_records=materialization.dependency_records,
        )

    def _persist_band_center(
        self,
        *,
        original_bundle: ProjectBundle,
        source_analysis: Analysis,
        source_artifact: Artifact,
        materialization: DurableBandCenter,
    ) -> None:
        current = self.store.open()
        if require_analysis(current, source_analysis.id) != source_analysis:
            raise ApplicationServiceError(
                "source DOS Analysis changed while descriptor was being materialized"
            )
        if require_artifact(current, source_artifact.id) != source_artifact:
            raise ApplicationServiceError(
                "source DOS Artifact changed while descriptor was being materialized"
            )
        if current.project != original_bundle.project:
            raise ApplicationServiceError("project changed while descriptor was being materialized")
        persist_analysis_graph(
            store=self.store,
            bundle=current,
            analysis=materialization.analysis,
            artifacts=(materialization.artifact,),
            provenance_records=materialization.provenance_records,
            dependency_records=materialization.dependency_records,
        )


def _materialization_receipt(
    *,
    analysis: Analysis,
    artifact: Artifact,
    source_id: str,
    source_field: str,
    reused: bool,
) -> dict[str, object]:
    return {
        "analysis_id": str(analysis.id),
        "artifact_id": str(artifact.id),
        source_field: source_id,
        "reused": reused,
    }


__all__ = ["ProjectElectronicAnalysisApplicationService"]

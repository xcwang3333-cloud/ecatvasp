"""Task-oriented electronic-analysis application authority for v1.1 Block 6.

This service productizes the frozen v0.7 electronic-analysis contracts.  It resolves
managed scientific evidence from ProjectStore, invokes canonical parsers/loaders and
materializers, and returns presentation-ready facts without introducing a second
scientific engine, workflow state machine, or durable workspace entity.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from uuid import UUID

from ecatvasp.analysis import (
    BandCenterEnergyReference,
    BandCenterKind,
    BandCenterParameters,
    BandCenterSelector,
    BandCenterSpinMode,
    CanonicalBaderResult,
    CanonicalCohpResult,
    CanonicalDosResult,
    DurableBandCenter,
    DurableDosMaterialization,
    LoadedChargeDifference,
    ProjectionScope,
    load_band_center_artifact,
    load_canonical_bader_artifact,
    load_canonical_cohp_artifact,
    load_canonical_dos_artifact,
    load_charge_difference_artifacts,
    materialize_band_center_analysis,
    materialize_canonical_dos_analysis,
    parse_vasp_doscar,
)
from ecatvasp.api.application import ApplicationServiceError, ProjectApplicationService
from ecatvasp.domain import (
    Analysis,
    AnalysisId,
    AnalysisProducerRef,
    AnalysisStatus,
    AnalysisType,
    Artifact,
    ArtifactAvailability,
    ArtifactType,
    AtomUid,
    Calculation,
    CalculationId,
    CalculationProducerRef,
    CalculationScientificStatus,
    CalculationType,
    ExecutionAttempt,
    ExecutionAttemptProducerRef,
    MethodFingerprint,
    StructureSnapshot,
)
from ecatvasp.provenance import DependencyRecord, ProvenanceRecord, scientific_hash
from ecatvasp.storage import ProjectBundle, ProjectStore
from ecatvasp.workflow import (
    ELECTRONIC_ANALYSIS_TYPES,
    ElectronicAnalysisRequirement,
    ElectronicAnalysisScientificState,
    WorkflowStepReadiness,
    reconcile_electronic_analyses_from_store,
)

_DOS_OUTPUT = "canonical-dos.json"
_BADER_ACF_OUTPUT = "ACF.dat"
_BADER_OUTPUT = "canonical-bader.json"
_CHARGE_DENSITY_OUTPUT = "charge-difference.f64"
_CHARGE_METADATA_OUTPUT = "canonical-charge-difference.json"
_COHP_RAW_OUTPUT = "COHPCAR.lobster"
_ICOHP_RAW_OUTPUT = "ICOHPLIST.lobster"
_COHP_OUTPUT = "canonical-cohp.json"
_BAND_CENTER_OUTPUT = "canonical-band-center.json"
_ATOM_MAP_OUTPUT = "atom-index-map.json"


@dataclass(frozen=True, slots=True)
class _DosSource:
    bundle: ProjectBundle
    calculation: Calculation
    attempt: ExecutionAttempt
    fingerprint: MethodFingerprint
    snapshot: StructureSnapshot
    doscar: Artifact
    atom_map: Artifact
    doscar_bytes: bytes
    atom_map_bytes: bytes


class ProjectElectronicAnalysisApplicationService(ProjectApplicationService):
    """Project-scoped application seam over the canonical v0.7 analysis layer."""

    def catalog(self) -> dict[str, object]:
        bundle = self.store.open()
        observations, invalid_ids = _observed_artifact_state(self.store.root, bundle)
        source_rows = [
            self._dos_source_row(bundle, calculation)
            for calculation in bundle.calculations
            if calculation.calculation_type is CalculationType.DOS_STATIC
        ]
        analysis_rows = [
            _analysis_catalog_row(
                store=self.store,
                bundle=bundle,
                analysis=analysis,
                current_hash_overrides=observations,
                invalid_ids=invalid_ids,
            )
            for analysis in bundle.analyses
            if analysis.analysis_type in ELECTRONIC_ANALYSIS_TYPES
        ]
        return {
            "project_id": str(bundle.project.id),
            "project_name": bundle.project.name,
            "dos_sources": source_rows,
            "analyses": analysis_rows,
        }

    def materialize_dos(self, *, calculation_id: CalculationId) -> dict[str, object]:
        source = self._resolve_dos_source(calculation_id)
        intake = parse_vasp_doscar(
            doscar_bytes=source.doscar_bytes,
            atom_index_map_bytes=source.atom_map_bytes,
            structure_snapshot_id=source.snapshot.id,
            spin_treatment=source.fingerprint.method.spin_treatment,
            soc=source.fingerprint.method.soc,
        )
        existing = _find_existing_dos(
            root=self.store.root,
            bundle=source.bundle,
            doscar=source.doscar,
            atom_map=source.atom_map,
            expected=intake.result,
        )
        if existing is not None:
            analysis, artifact = existing
            return {
                "analysis_id": str(analysis.id),
                "artifact_id": str(artifact.id),
                "calculation_id": str(source.calculation.id),
                "reused": True,
            }

        materialization = materialize_canonical_dos_analysis(
            project_root=self.store.root,
            calculation=source.calculation,
            execution_attempt=source.attempt,
            doscar_artifact=source.doscar,
            atom_index_map_artifact=source.atom_map,
            intake=intake,
        )
        self._persist_dos(source=source, materialization=materialization)
        return {
            "analysis_id": str(materialization.analysis.id),
            "artifact_id": str(materialization.artifact.id),
            "calculation_id": str(source.calculation.id),
            "reused": False,
        }

    def analysis_view(self, *, analysis_id: AnalysisId) -> dict[str, object]:
        bundle = self.store.open()
        analysis = _require_analysis(bundle, analysis_id)
        if analysis.analysis_type not in ELECTRONIC_ANALYSIS_TYPES:
            raise ApplicationServiceError("Analysis is not an electronic-analysis type")

        observations, invalid_ids = _observed_artifact_state(self.store.root, bundle)
        status = _analysis_projection_payload(
            store=self.store,
            bundle=bundle,
            analysis=analysis,
            current_hash_overrides=observations,
            invalid_ids=invalid_ids,
        )
        if analysis.analysis_type is AnalysisType.DOS:
            payload = self._dos_view(bundle, analysis)
        elif analysis.analysis_type is AnalysisType.BADER:
            payload = self._bader_view(bundle, analysis)
        elif analysis.analysis_type is AnalysisType.CHARGE_DIFFERENCE:
            payload = self._charge_difference_view(bundle, analysis)
        elif analysis.analysis_type is AnalysisType.COHP:
            payload = self._cohp_view(bundle, analysis)
        elif analysis.analysis_type is AnalysisType.BAND_CENTER:
            payload = self._band_center_view(bundle, analysis)
        elif analysis.analysis_type is AnalysisType.PDOS:
            raise ApplicationServiceError(
                "standalone PDOS Analysis has no v0.7 canonical loader; PDOS is exposed "
                "through atom/orbital series in canonical DOS"
            )
        else:  # pragma: no cover - guarded by ELECTRONIC_ANALYSIS_TYPES
            raise ApplicationServiceError("unsupported electronic Analysis type")

        return {
            "project_id": str(bundle.project.id),
            "analysis_id": str(analysis.id),
            "analysis_type": analysis.analysis_type.value,
            "analysis_status": analysis.status.value,
            "tool": analysis.tool,
            "tool_version": analysis.tool_version,
            "freshness": status,
            "view": payload,
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
        source_analysis = _require_analysis(bundle, source_analysis_id)
        if source_analysis.analysis_type is not AnalysisType.DOS:
            raise ApplicationServiceError("band-center source must be canonical DOS Analysis")
        source_artifact = _analysis_output(
            bundle,
            source_analysis,
            filename=_DOS_OUTPUT,
            artifact_type=ArtifactType.DERIVED_DATASET,
        )
        observations, invalid_ids = _observed_artifact_state(self.store.root, bundle)
        projection = _analysis_projection(
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
        existing = _find_existing_band_center(
            root=self.store.root,
            bundle=bundle,
            source_analysis=source_analysis,
            source_artifact=source_artifact,
            parameters=parameters,
        )
        if existing is not None:
            analysis, artifact = existing
            return {
                "analysis_id": str(analysis.id),
                "artifact_id": str(artifact.id),
                "source_analysis_id": str(source_analysis.id),
                "reused": True,
            }

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
        return {
            "analysis_id": str(materialization.analysis.id),
            "artifact_id": str(materialization.artifact.id),
            "source_analysis_id": str(source_analysis.id),
            "reused": False,
        }

    def _dos_source_row(
        self,
        bundle: ProjectBundle,
        calculation: Calculation,
    ) -> dict[str, object]:
        latest = _latest_attempt(bundle, calculation.id)
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
            intake = parse_vasp_doscar(
                doscar_bytes=source.doscar_bytes,
                atom_index_map_bytes=source.atom_map_bytes,
                structure_snapshot_id=source.snapshot.id,
                spin_treatment=source.fingerprint.method.spin_treatment,
                soc=source.fingerprint.method.soc,
            )
            existing = _find_existing_dos(
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
    ) -> _DosSource:
        current = self.store.open() if bundle is None else bundle
        calculation = _require_calculation(current, calculation_id)
        if calculation.calculation_type is not CalculationType.DOS_STATIC:
            raise ApplicationServiceError("DOS materialization requires DOS_STATIC Calculation")
        if calculation.status is not CalculationScientificStatus.CONVERGED:
            raise ApplicationServiceError(
                "DOS materialization requires scientifically converged Calculation"
            )
        attempt = _latest_attempt(current, calculation.id)
        if attempt is None:
            raise ApplicationServiceError("DOS Calculation has no managed ExecutionAttempt")
        fingerprint = _require_fingerprint(current, calculation)
        snapshot = _require_snapshot(current, calculation)
        doscar = _single_artifact(
            current,
            producer=ExecutionAttemptProducerRef(attempt.id),
            artifact_type=ArtifactType.DOSCAR,
            filename="DOSCAR",
            label="latest managed DOSCAR",
        )
        atom_map = _single_artifact(
            current,
            producer=CalculationProducerRef(calculation.id),
            artifact_type=ArtifactType.DERIVED_DATASET,
            filename=_ATOM_MAP_OUTPUT,
            label="frozen atom-index-map.json",
        )
        doscar_bytes = _verified_local_bytes(
            root=self.store.root,
            artifact=doscar,
            expected_filename="DOSCAR",
            label="DOSCAR",
        )
        atom_map_bytes = _verified_local_bytes(
            root=self.store.root,
            artifact=atom_map,
            expected_filename=_ATOM_MAP_OUTPUT,
            label="atom-index-map.json",
        )
        return _DosSource(
            bundle=current,
            calculation=calculation,
            attempt=attempt,
            fingerprint=fingerprint,
            snapshot=snapshot,
            doscar=doscar,
            atom_map=atom_map,
            doscar_bytes=doscar_bytes,
            atom_map_bytes=atom_map_bytes,
        )

    def _persist_dos(
        self,
        *,
        source: _DosSource,
        materialization: DurableDosMaterialization,
    ) -> None:
        current = self.store.open()
        _require_source_unchanged(current, source)
        if _matching_analysis_ids(
            current,
            AnalysisType.DOS,
            (source.doscar.id, source.atom_map.id),
        ):
            raise ApplicationServiceError(
                "canonical DOS Analysis appeared while materialization was in progress"
            )
        _persist_analysis_graph(
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
        if _require_analysis(current, source_analysis.id) != source_analysis:
            raise ApplicationServiceError(
                "source DOS Analysis changed while descriptor was being materialized"
            )
        if _require_artifact(current, source_artifact.id) != source_artifact:
            raise ApplicationServiceError(
                "source DOS Artifact changed while descriptor was being materialized"
            )
        if current.project != original_bundle.project:
            raise ApplicationServiceError("project changed while descriptor was being materialized")
        _persist_analysis_graph(
            store=self.store,
            bundle=current,
            analysis=materialization.analysis,
            artifacts=(materialization.artifact,),
            provenance_records=materialization.provenance_records,
            dependency_records=materialization.dependency_records,
        )

    def _dos_view(self, bundle: ProjectBundle, analysis: Analysis) -> dict[str, object]:
        artifact = _analysis_output(
            bundle,
            analysis,
            filename=_DOS_OUTPUT,
            artifact_type=ArtifactType.DERIVED_DATASET,
        )
        result = load_canonical_dos_artifact(
            project_root=self.store.root,
            analysis=analysis,
            artifact=artifact,
        )
        return _dos_payload(result, artifact)

    def _bader_view(self, bundle: ProjectBundle, analysis: Analysis) -> dict[str, object]:
        acf = _analysis_output(
            bundle,
            analysis,
            filename=_BADER_ACF_OUTPUT,
            artifact_type=ArtifactType.ACF_DAT,
        )
        result_artifact = _analysis_output(
            bundle,
            analysis,
            filename=_BADER_OUTPUT,
            artifact_type=ArtifactType.DERIVED_DATASET,
        )
        result = load_canonical_bader_artifact(
            project_root=self.store.root,
            analysis=analysis,
            acf_artifact=acf,
            result_artifact=result_artifact,
        )
        return _bader_payload(result, acf, result_artifact)

    def _charge_difference_view(
        self,
        bundle: ProjectBundle,
        analysis: Analysis,
    ) -> dict[str, object]:
        density = _analysis_output(
            bundle,
            analysis,
            filename=_CHARGE_DENSITY_OUTPUT,
            artifact_type=ArtifactType.DERIVED_DATASET,
        )
        metadata = _analysis_output(
            bundle,
            analysis,
            filename=_CHARGE_METADATA_OUTPUT,
            artifact_type=ArtifactType.DERIVED_DATASET,
        )
        loaded = load_charge_difference_artifacts(
            project_root=self.store.root,
            analysis=analysis,
            density_artifact=density,
            metadata_artifact=metadata,
        )
        return _charge_difference_payload(loaded, density, metadata)

    def _cohp_view(self, bundle: ProjectBundle, analysis: Analysis) -> dict[str, object]:
        cohpcar = _analysis_output(
            bundle,
            analysis,
            filename=_COHP_RAW_OUTPUT,
            artifact_type=ArtifactType.COHPCAR_LOBSTER,
        )
        icohplist = _analysis_output(
            bundle,
            analysis,
            filename=_ICOHP_RAW_OUTPUT,
            artifact_type=ArtifactType.ICOHPLIST_LOBSTER,
        )
        result_artifact = _analysis_output(
            bundle,
            analysis,
            filename=_COHP_OUTPUT,
            artifact_type=ArtifactType.DERIVED_DATASET,
        )
        result = load_canonical_cohp_artifact(
            project_root=self.store.root,
            analysis=analysis,
            cohpcar_artifact=cohpcar,
            icohplist_artifact=icohplist,
            result_artifact=result_artifact,
        )
        return _cohp_payload(result, cohpcar, icohplist, result_artifact)

    def _band_center_view(
        self,
        bundle: ProjectBundle,
        analysis: Analysis,
    ) -> dict[str, object]:
        if len(analysis.input_artifact_ids) != 1:
            raise ApplicationServiceError("band-center Analysis requires one canonical DOS input")
        source_artifact = _require_artifact(bundle, analysis.input_artifact_ids[0])
        producer = source_artifact.producer
        if not isinstance(producer, AnalysisProducerRef):
            raise ApplicationServiceError(
                "band-center source Artifact is not Analysis-produced canonical DOS"
            )
        source_analysis = _require_analysis(bundle, producer.id)
        artifact = _analysis_output(
            bundle,
            analysis,
            filename=_BAND_CENTER_OUTPUT,
            artifact_type=ArtifactType.DERIVED_DATASET,
        )
        result = load_band_center_artifact(
            project_root=self.store.root,
            source_analysis=source_analysis,
            source_artifact=source_artifact,
            analysis=analysis,
            artifact=artifact,
        )
        return _band_center_payload(result, source_analysis, source_artifact, artifact)


def _analysis_catalog_row(
    *,
    store: ProjectStore,
    bundle: ProjectBundle,
    analysis: Analysis,
    current_hash_overrides: dict[UUID, str],
    invalid_ids: set[UUID],
) -> dict[str, object]:
    state = _analysis_projection_payload(
        store=store,
        bundle=bundle,
        analysis=analysis,
        current_hash_overrides=current_hash_overrides,
        invalid_ids=invalid_ids,
    )
    return {
        "analysis_id": str(analysis.id),
        "analysis_type": analysis.analysis_type.value,
        "status": analysis.status.value,
        "tool": analysis.tool,
        "tool_version": analysis.tool_version,
        "input_artifact_count": len(analysis.input_artifact_ids),
        "freshness": state,
        "view_supported": analysis.analysis_type
        in {
            AnalysisType.DOS,
            AnalysisType.BADER,
            AnalysisType.CHARGE_DIFFERENCE,
            AnalysisType.COHP,
            AnalysisType.BAND_CENTER,
        },
    }


def _analysis_projection_payload(
    *,
    store: ProjectStore,
    bundle: ProjectBundle,
    analysis: Analysis,
    current_hash_overrides: dict[UUID, str],
    invalid_ids: set[UUID],
) -> dict[str, object]:
    projection = _analysis_projection(
        store=store,
        bundle=bundle,
        analysis=analysis,
        current_hash_overrides=current_hash_overrides,
        invalid_ids=invalid_ids,
    )
    return {
        "scientific_state": projection.scientific_state.value,
        "readiness": projection.readiness.value,
        "freshness_state": (
            projection.freshness_state.value
            if projection.freshness_state is not None
            else None
        ),
        "reason_codes": list(projection.reason_codes),
    }


def _analysis_projection(
    *,
    store: ProjectStore,
    bundle: ProjectBundle,
    analysis: Analysis,
    current_hash_overrides: dict[UUID, str],
    invalid_ids: set[UUID],
):
    requirement = ElectronicAnalysisRequirement(
        key=str(analysis.id),
        project_id=bundle.project.id,
        analysis_type=analysis.analysis_type,
        input_artifact_ids=analysis.input_artifact_ids,
        parameters_hash=analysis.parameters_hash,
    )
    report = reconcile_electronic_analyses_from_store(
        store=store,
        requirements=(requirement,),
        current_hash_overrides=current_hash_overrides,
        invalid_ids=invalid_ids,
    )
    return report.requirement(requirement.key)


def _observed_artifact_state(
    root: Path,
    bundle: ProjectBundle,
) -> tuple[dict[UUID, str], set[UUID]]:
    overrides: dict[UUID, str] = {}
    invalid: set[UUID] = set()
    resolved_root = root.resolve()
    for artifact in bundle.artifacts:
        if artifact.availability not in {
            ArtifactAvailability.LOCAL,
            ArtifactAvailability.BOTH,
        }:
            continue
        try:
            path = _safe_artifact_path(resolved_root, artifact, "local Artifact")
            body = path.read_bytes()
        except (OSError, ValueError):
            invalid.add(artifact.id)
            continue
        observed_sha = hashlib.sha256(body).hexdigest()
        observed_size = len(body)
        if artifact.sha256 != observed_sha or artifact.size_bytes != observed_size:
            observed = replace(
                artifact,
                sha256=observed_sha,
                size_bytes=observed_size,
            )
            overrides[artifact.id] = scientific_hash(observed)
    return overrides, invalid


def _find_existing_dos(
    *,
    root: Path,
    bundle: ProjectBundle,
    doscar: Artifact,
    atom_map: Artifact,
    expected: CanonicalDosResult,
) -> tuple[Analysis, Artifact] | None:
    candidates = tuple(
        analysis
        for analysis in bundle.analyses
        if analysis.analysis_type is AnalysisType.DOS
        and analysis.status is AnalysisStatus.COMPLETED
        and analysis.input_artifact_ids == (doscar.id, atom_map.id)
    )
    exact: list[tuple[Analysis, Artifact]] = []
    for analysis in candidates:
        artifact = _analysis_output(
            bundle,
            analysis,
            filename=_DOS_OUTPUT,
            artifact_type=ArtifactType.DERIVED_DATASET,
        )
        reopened = load_canonical_dos_artifact(
            project_root=root,
            analysis=analysis,
            artifact=artifact,
        )
        if reopened.content_hash != expected.content_hash:
            raise ApplicationServiceError(
                "existing canonical DOS for exact source Artifacts disagrees with current parser"
            )
        exact.append((analysis, artifact))
    if len(exact) > 1:
        raise ApplicationServiceError("duplicate canonical DOS Analyses exist for exact source")
    return exact[0] if exact else None


def _find_existing_band_center(
    *,
    root: Path,
    bundle: ProjectBundle,
    source_analysis: Analysis,
    source_artifact: Artifact,
    parameters: BandCenterParameters,
) -> tuple[Analysis, Artifact] | None:
    matches: list[tuple[Analysis, Artifact]] = []
    candidates = tuple(
        analysis
        for analysis in bundle.analyses
        if analysis.analysis_type is AnalysisType.BAND_CENTER
        and analysis.status is AnalysisStatus.COMPLETED
        and analysis.input_artifact_ids == (source_artifact.id,)
    )
    for analysis in candidates:
        artifact = _analysis_output(
            bundle,
            analysis,
            filename=_BAND_CENTER_OUTPUT,
            artifact_type=ArtifactType.DERIVED_DATASET,
        )
        result = load_band_center_artifact(
            project_root=root,
            source_analysis=source_analysis,
            source_artifact=source_artifact,
            analysis=analysis,
            artifact=artifact,
        )
        if result.parameters == parameters:
            matches.append((analysis, artifact))
    if len(matches) > 1:
        raise ApplicationServiceError(
            "duplicate band-center Analyses exist for exact source and parameters"
        )
    return matches[0] if matches else None


def _persist_analysis_graph(
    *,
    store: ProjectStore,
    bundle: ProjectBundle,
    analysis: Analysis,
    artifacts: tuple[Artifact, ...],
    provenance_records: tuple[ProvenanceRecord, ...],
    dependency_records: tuple[DependencyRecord, ...],
) -> None:
    if any(item.id == analysis.id for item in bundle.analyses):
        raise ApplicationServiceError("new electronic Analysis id already exists")
    known_artifacts = {item.id for item in bundle.artifacts}
    if any(item.id in known_artifacts for item in artifacts):
        raise ApplicationServiceError("new electronic Artifact id already exists")
    updated = replace(
        bundle,
        analyses=(*bundle.analyses, analysis),
        artifacts=(*bundle.artifacts, *artifacts),
        provenance_records=(*bundle.provenance_records, *provenance_records),
        dependency_records=(*bundle.dependency_records, *dependency_records),
    )
    store.save(updated)
    reopened = store.open()
    if _require_analysis(reopened, analysis.id) != analysis:
        raise ApplicationServiceError("electronic Analysis failed post-save verification")
    for artifact in artifacts:
        if _require_artifact(reopened, artifact.id) != artifact:
            raise ApplicationServiceError("electronic Artifact failed post-save verification")


def _require_source_unchanged(bundle: ProjectBundle, source: _DosSource) -> None:
    if _require_calculation(bundle, source.calculation.id) != source.calculation:
        raise ApplicationServiceError("DOS Calculation changed during materialization")
    if _require_attempt(bundle, source.attempt.id) != source.attempt:
        raise ApplicationServiceError("DOS ExecutionAttempt changed during materialization")
    if _require_fingerprint(bundle, source.calculation) != source.fingerprint:
        raise ApplicationServiceError("DOS MethodFingerprint changed during materialization")
    if _require_snapshot(bundle, source.calculation) != source.snapshot:
        raise ApplicationServiceError("DOS StructureSnapshot changed during materialization")
    if _require_artifact(bundle, source.doscar.id) != source.doscar:
        raise ApplicationServiceError("DOSCAR Artifact changed during materialization")
    if _require_artifact(bundle, source.atom_map.id) != source.atom_map:
        raise ApplicationServiceError("atom-index-map Artifact changed during materialization")


def _matching_analysis_ids(
    bundle: ProjectBundle,
    analysis_type: AnalysisType,
    input_artifact_ids: tuple[object, ...],
) -> tuple[AnalysisId, ...]:
    return tuple(
        item.id
        for item in bundle.analyses
        if item.analysis_type is analysis_type
        and item.input_artifact_ids == input_artifact_ids
    )


def _analysis_output(
    bundle: ProjectBundle,
    analysis: Analysis,
    *,
    filename: str,
    artifact_type: ArtifactType,
) -> Artifact:
    return _single_artifact(
        bundle,
        producer=AnalysisProducerRef(analysis.id),
        artifact_type=artifact_type,
        filename=filename,
        label=f"{analysis.analysis_type.value} {filename}",
    )


def _single_artifact(
    bundle: ProjectBundle,
    *,
    producer: object,
    artifact_type: ArtifactType,
    filename: str,
    label: str,
) -> Artifact:
    matches = tuple(
        item
        for item in bundle.artifacts
        if item.producer == producer
        and item.artifact_type is artifact_type
        and PurePosixPath(item.local_path or "").name == filename
    )
    if len(matches) != 1:
        raise ApplicationServiceError(f"{label} requires exactly one matching Artifact")
    return matches[0]


def _verified_local_bytes(
    *,
    root: Path,
    artifact: Artifact,
    expected_filename: str,
    label: str,
) -> bytes:
    if artifact.availability not in {
        ArtifactAvailability.LOCAL,
        ArtifactAvailability.BOTH,
    }:
        raise ApplicationServiceError(f"{label} must be locally available")
    if artifact.sha256 is None or artifact.size_bytes is None:
        raise ApplicationServiceError(f"{label} requires exact size/SHA-256 metadata")
    path = _safe_artifact_path(root.resolve(), artifact, label)
    if path.name != expected_filename:
        raise ApplicationServiceError(f"{label} has unexpected local filename")
    try:
        body = path.read_bytes()
    except OSError as error:
        raise ApplicationServiceError(f"{label} cannot be read") from error
    if len(body) != artifact.size_bytes:
        raise ApplicationServiceError(f"{label} local byte size changed")
    if hashlib.sha256(body).hexdigest() != artifact.sha256.lower():
        raise ApplicationServiceError(f"{label} local content hash changed")
    return body


def _safe_artifact_path(root: Path, artifact: Artifact, label: str) -> Path:
    if artifact.local_path is None:
        raise ApplicationServiceError(f"{label} requires local_path")
    relative = PurePosixPath(artifact.local_path)
    if (
        relative.is_absolute()
        or artifact.local_path != relative.as_posix()
        or ".." in relative.parts
        or artifact.local_path in {"", "."}
    ):
        raise ApplicationServiceError(f"{label} local_path must be normalized and relative")
    path = (root / Path(*relative.parts)).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ApplicationServiceError(f"{label} local file is missing or outside project_root")
    return path


def _latest_attempt(
    bundle: ProjectBundle,
    calculation_id: CalculationId,
) -> ExecutionAttempt | None:
    attempts = sorted(
        (item for item in bundle.execution_attempts if item.calculation_id == calculation_id),
        key=lambda item: item.attempt_number,
    )
    return attempts[-1] if attempts else None


def _require_calculation(bundle: ProjectBundle, calculation_id: CalculationId) -> Calculation:
    matches = tuple(item for item in bundle.calculations if item.id == calculation_id)
    if len(matches) != 1:
        raise ApplicationServiceError("Calculation is absent or duplicated")
    return matches[0]


def _require_attempt(bundle: ProjectBundle, attempt_id) -> ExecutionAttempt:
    matches = tuple(item for item in bundle.execution_attempts if item.id == attempt_id)
    if len(matches) != 1:
        raise ApplicationServiceError("ExecutionAttempt is absent or duplicated")
    return matches[0]


def _require_fingerprint(bundle: ProjectBundle, calculation: Calculation) -> MethodFingerprint:
    matches = tuple(
        item
        for item in bundle.method_fingerprints
        if item.id == calculation.method_fingerprint_id
    )
    if len(matches) != 1:
        raise ApplicationServiceError("MethodFingerprint is absent or duplicated")
    return matches[0]


def _require_snapshot(bundle: ProjectBundle, calculation: Calculation) -> StructureSnapshot:
    matches = tuple(
        item
        for item in bundle.structure_snapshots
        if item.id == calculation.input_structure_snapshot_id
    )
    if len(matches) != 1:
        raise ApplicationServiceError("StructureSnapshot is absent or duplicated")
    return matches[0]


def _require_analysis(bundle: ProjectBundle, analysis_id: AnalysisId) -> Analysis:
    matches = tuple(item for item in bundle.analyses if item.id == analysis_id)
    if len(matches) != 1:
        raise ApplicationServiceError("Analysis is absent or duplicated")
    return matches[0]


def _require_artifact(bundle: ProjectBundle, artifact_id) -> Artifact:
    matches = tuple(item for item in bundle.artifacts if item.id == artifact_id)
    if len(matches) != 1:
        raise ApplicationServiceError("Artifact is absent or duplicated")
    return matches[0]


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


def _cohp_series_payload(series) -> dict[str, object]:
    return {
        "spin": series.spin.value,
        "cohp_values_native": list(series.cohp_values),
        "negative_cohp_values": [-value for value in series.cohp_values],
        "icohp_values_native": list(series.icohp_values),
        "icohp_at_fermi_ev_native": series.icohp_at_fermi_ev,
    }


def _band_center_payload(result, source_analysis, source_artifact, artifact) -> dict[str, object]:
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


__all__ = ["ProjectElectronicAnalysisApplicationService"]

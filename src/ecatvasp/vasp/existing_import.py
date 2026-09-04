"""v0.5 compatibility adapter for importing pre-existing VASP folders.

Historical folders do not have an ECatVASP ExecutionPlan or atom-index-map. This
adapter therefore does not fabricate either identity. It reuses the normalized
energy/convergence layers through an explicit compatibility intake, preserves
strict VASP-order atom identity, and materializes the same durable scientific
result provenance graph used by managed results.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import cast

from ecatvasp.domain import (
    Analysis,
    Artifact,
    ArtifactId,
    ArtifactType,
    Calculation,
    CalculationScientificStatus,
    CalculationType,
    ExecutionAttempt,
    ExecutionAttemptStatus,
    MethodFingerprint,
    Project,
    StructureSnapshot,
    StructureVariant,
    canonical_sha256,
)
from ecatvasp.domain.ids import CalculationId, ExecutionAttemptId
from ecatvasp.provenance import (
    DependencyKind,
    DependencyRecord,
    ProvenanceRecord,
    scientific_hash,
)
from ecatvasp.vasp.convergence import (
    assess_vasp_convergence,
    collect_vasp_convergence_evidence,
)
from ecatvasp.vasp.importer import (
    VaspFolderInspection,
    VaspImportError,
    _infer_calculation_type,
    _last_force_norm,
    _new_snapshot,
    _parse_incar,
    _parse_poscar,
    _propagate_vasp_order,
    _source_artifacts,
    _validate_fingerprint_against_incar,
    inspect_vasp_folder,
)
from ecatvasp.vasp.result_intake import (
    VaspResultArtifactIntake,
    VaspResultInputFile,
)
from ecatvasp.vasp.result_parser import parse_vasp_energy_metadata
from ecatvasp.vasp.result_provenance import (
    VaspScientificResultMaterialization,
    materialize_vasp_scientific_result,
)
from ecatvasp.vasp.results import (
    ConvergenceVerdict,
    VaspConvergenceAssessment,
    VaspResultDocument,
    VaspResultSource,
    VaspResultSourceRole,
    result_source_artifact_type,
)

_IMPORTER_NAME = "ecatvasp.vasp.existing-folder-importer-v05"
_IMPORTER_VERSION = "2"


@dataclass(frozen=True, slots=True)
class ParsedVaspResult:
    """Backward-compatible projection of normalized v0.5 result contracts."""

    calculation_type: CalculationType
    scientific_status: CalculationScientificStatus
    total_energy_ev: float | None
    fermi_energy_ev: float | None
    max_force_ev_per_angstrom: float | None
    electronic_converged: bool | None
    ionic_converged: bool | None
    ionic_steps: int | None
    electronic_steps: int | None
    vasp_version: str | None


@dataclass(frozen=True, slots=True)
class ExistingVaspImport:
    """Unified scientific objects produced from one pre-existing VASP folder."""

    updated_variant: StructureVariant
    input_snapshot: StructureSnapshot
    final_snapshot: StructureSnapshot
    calculation: Calculation
    execution_attempt: ExecutionAttempt
    inspection: VaspFolderInspection
    artifacts: tuple[Artifact, ...]
    analyses: tuple[Analysis, ...]
    normalized_result: VaspResultDocument
    convergence_assessment: VaspConvergenceAssessment
    parsed_result: ParsedVaspResult
    provenance_records: tuple[ProvenanceRecord, ...]
    dependency_records: tuple[DependencyRecord, ...]


@dataclass(frozen=True, slots=True)
class _ImportedResultIntake:
    """Exact imported-source identity without pretending an ExecutionPlan existed."""

    calculation_id: CalculationId
    calculation_type: CalculationType
    recipe_id: str
    attempt_id: ExecutionAttemptId
    files: tuple[VaspResultInputFile, ...]
    intake_hash: str = field(init=False)

    def __post_init__(self) -> None:
        ordered = tuple(sorted(self.files, key=lambda item: item.source.role.value))
        roles = tuple(item.source.role for item in ordered)
        if len(roles) != len(set(roles)):
            raise VaspImportError("imported result source roles must be unique")
        artifact_ids = tuple(item.source.artifact_id for item in ordered)
        if len(artifact_ids) != len(set(artifact_ids)):
            raise VaspImportError("imported result Artifact ids must be unique")
        if VaspResultSourceRole.OUTCAR not in roles:
            raise VaspImportError("existing-folder scientific import requires OUTCAR")
        object.__setattr__(self, "files", ordered)
        object.__setattr__(
            self,
            "intake_hash",
            canonical_sha256(
                {
                    "origin": "existing_folder_import",
                    "calculation_id": self.calculation_id,
                    "calculation_type": self.calculation_type,
                    "recipe_id": self.recipe_id,
                    "attempt_id": self.attempt_id,
                    "sources": tuple(
                        {
                            "role": item.source.role,
                            "artifact_id": item.source.artifact_id,
                            "artifact_type": item.source.artifact_type,
                            "sha256": item.source.sha256,
                            "size_bytes": item.size_bytes,
                            "source_path": item.local_relative_path,
                        }
                        for item in ordered
                    ),
                }
            ),
        )

    @property
    def sources(self) -> tuple[VaspResultSource, ...]:
        return tuple(item.source for item in self.files)

    @property
    def input_artifact_ids(self) -> tuple[ArtifactId, ...]:
        return tuple(item.source.artifact_id for item in self.files)


def import_existing_vasp_folder(
    *,
    folder: Path | str,
    project_root: Path | str,
    project: Project,
    variant: StructureVariant,
    method_fingerprint: MethodFingerprint,
) -> ExistingVaspImport:
    """Import a relax/static folder through normalized v0.5 result semantics."""

    inspection = inspect_vasp_folder(folder)
    root = inspection.folder.resolve()
    incar = _parse_incar((root / "INCAR").read_text(encoding="utf-8", errors="replace"))
    _validate_fingerprint_against_incar(method_fingerprint, incar)
    calculation_type = _infer_calculation_type(incar)
    if calculation_type not in {CalculationType.RELAX, CalculationType.STATIC}:
        raise VaspImportError(
            "existing-folder compatibility import supports relax and static calculations only"
        )

    input_geometry = _parse_poscar((root / "POSCAR").read_text(encoding="utf-8"))
    input_snapshot = _new_snapshot(
        input_geometry,
        label=f"{variant.name} imported POSCAR",
        origin=_imported_origin(),
    )
    final_snapshot = _reconstruct_final_snapshot(
        root=root,
        variant=variant,
        calculation_type=calculation_type,
        input_snapshot=input_snapshot,
    )

    calculation = Calculation(
        project_id=project.id,
        calculation_type=calculation_type,
        input_structure_snapshot_id=input_snapshot.id,
        recipe_id=method_fingerprint.recipe.recipe_id,
        method_fingerprint_id=method_fingerprint.id,
        slug=f"imported-{calculation_type.value}",
    )
    attempt = ExecutionAttempt(
        calculation_id=calculation.id,
        attempt_number=1,
        status=ExecutionAttemptStatus.PARSED,
    )
    raw_artifacts = _source_artifacts(root=root, attempt=attempt)
    intake = _build_imported_intake(
        calculation=calculation,
        attempt=attempt,
        artifacts=raw_artifacts,
    )

    compatibility_intake = cast(VaspResultArtifactIntake, intake)
    normalized_result = parse_vasp_energy_metadata(
        project_root=root,
        intake=compatibility_intake,
    )
    expected_version = method_fingerprint.method.engine_version
    if (
        expected_version is not None
        and normalized_result.vasp_version is not None
        and expected_version != normalized_result.vasp_version
    ):
        raise VaspImportError(
            "OUTCAR VASP version does not match the caller-supplied MethodFingerprint"
        )

    evidence = collect_vasp_convergence_evidence(
        project_root=root,
        intake=compatibility_intake,
        result=normalized_result,
    )
    assessment = assess_vasp_convergence(
        calculation=calculation,
        fingerprint=method_fingerprint,
        evidence=evidence,
    )
    materialized = materialize_vasp_scientific_result(
        project_root=project_root,
        calculation=calculation,
        intake=intake,
        result=normalized_result,
        assessment=assessment,
    )
    updated_variant, inspection = _apply_imported_structure_policy(
        variant=variant,
        input_snapshot=input_snapshot,
        final_snapshot=final_snapshot,
        calculation_type=calculation_type,
        assessment=assessment,
        inspection=inspection,
    )

    outcar_text = (root / "OUTCAR").read_text(encoding="utf-8", errors="replace")
    compatibility_result = _compatibility_projection(
        normalized=normalized_result,
        assessment=assessment,
        scientific_status=materialized.updated_calculation.status,
        max_force_ev_per_angstrom=_last_force_norm(outcar_text),
    )
    structure_provenance, structure_dependencies = _structure_provenance(
        calculation=materialized.updated_calculation,
        input_snapshot=input_snapshot,
        final_snapshot=final_snapshot,
        raw_artifacts=raw_artifacts,
        method_fingerprint=method_fingerprint,
    )
    calculation_provenance = ProvenanceRecord(
        subject_id=materialized.updated_calculation.id,
        tool=_IMPORTER_NAME,
        tool_version=_IMPORTER_VERSION,
        method_fingerprint_id=method_fingerprint.id,
    )
    calculation_dependency = DependencyRecord(
        upstream_id=input_snapshot.id,
        downstream_id=materialized.updated_calculation.id,
        kind=DependencyKind.SCIENTIFIC,
        role="input_structure",
        recorded_hash=scientific_hash(input_snapshot),
    )

    return ExistingVaspImport(
        updated_variant=updated_variant,
        input_snapshot=input_snapshot,
        final_snapshot=final_snapshot,
        calculation=materialized.updated_calculation,
        execution_attempt=attempt,
        inspection=inspection,
        artifacts=(*raw_artifacts, *materialized.artifacts),
        analyses=materialized.analyses,
        normalized_result=normalized_result,
        convergence_assessment=assessment,
        parsed_result=compatibility_result,
        provenance_records=(
            calculation_provenance,
            *structure_provenance,
            *materialized.provenance_records,
        ),
        dependency_records=(
            calculation_dependency,
            *structure_dependencies,
            *materialized.dependency_records,
        ),
    )


def _imported_origin():
    from ecatvasp.domain import StructureOrigin

    return StructureOrigin.IMPORTED


def _reconstruct_final_snapshot(
    *,
    root: Path,
    variant: StructureVariant,
    calculation_type: CalculationType,
    input_snapshot: StructureSnapshot,
) -> StructureSnapshot:
    contcar = root / "CONTCAR"
    if contcar.is_file():
        geometry = _parse_poscar(contcar.read_text(encoding="utf-8"))
        return _propagate_vasp_order(
            source=input_snapshot,
            target=geometry,
            label=f"{variant.name} imported CONTCAR",
        )
    if calculation_type is CalculationType.RELAX:
        raise VaspImportError("relax import requires CONTCAR to establish a final candidate")
    return input_snapshot


def _build_imported_intake(
    *,
    calculation: Calculation,
    attempt: ExecutionAttempt,
    artifacts: tuple[Artifact, ...],
) -> _ImportedResultIntake:
    files: list[VaspResultInputFile] = []
    role_filename = {
        VaspResultSourceRole.OUTCAR: "OUTCAR",
        VaspResultSourceRole.OSZICAR: "OSZICAR",
        VaspResultSourceRole.CONTCAR: "CONTCAR",
        VaspResultSourceRole.VASPRUN_XML: "vasprun.xml",
    }
    for role, filename in role_filename.items():
        artifact_type = result_source_artifact_type(role)
        matches = tuple(item for item in artifacts if item.artifact_type is artifact_type)
        if not matches:
            if role is VaspResultSourceRole.OUTCAR:
                raise VaspImportError("existing-folder scientific import requires OUTCAR")
            continue
        if len(matches) != 1:
            raise VaspImportError(f"ambiguous imported result source: {role.value}")
        artifact = matches[0]
        if artifact.sha256 is None or artifact.size_bytes is None:
            if role is VaspResultSourceRole.OUTCAR:
                raise VaspImportError("imported OUTCAR requires content hash and size")
            continue
        files.append(
            VaspResultInputFile(
                source=VaspResultSource(
                    role=role,
                    artifact_id=artifact.id,
                    artifact_type=artifact.artifact_type,
                    sha256=artifact.sha256,
                ),
                expected_output_path=filename,
                local_relative_path=filename,
                size_bytes=artifact.size_bytes,
                retrieval_policy=artifact.retrieval_policy,
            )
        )
    return _ImportedResultIntake(
        calculation_id=calculation.id,
        calculation_type=calculation.calculation_type,
        recipe_id=calculation.recipe_id,
        attempt_id=attempt.id,
        files=tuple(files),
    )


def _apply_imported_structure_policy(
    *,
    variant: StructureVariant,
    input_snapshot: StructureSnapshot,
    final_snapshot: StructureSnapshot,
    calculation_type: CalculationType,
    assessment: VaspConvergenceAssessment,
    inspection: VaspFolderInspection,
) -> tuple[StructureVariant, VaspFolderInspection]:
    current = variant.current_structure_snapshot_id
    baseline = (
        replace(variant, current_structure_snapshot_id=input_snapshot.id)
        if current is None
        else variant
    )
    if calculation_type is not CalculationType.RELAX:
        return baseline, inspection
    if assessment.overall is not ConvergenceVerdict.CONVERGED:
        return baseline, inspection
    if baseline.current_structure_snapshot_id != input_snapshot.id:
        warning = (
            "converged imported CONTCAR was retained as a candidate because the "
            "StructureVariant already points to another current snapshot"
        )
        return replace(inspection, warnings=(*inspection.warnings, warning)), baseline
    return replace(baseline, current_structure_snapshot_id=final_snapshot.id), inspection


def _compatibility_projection(
    *,
    normalized: VaspResultDocument,
    assessment: VaspConvergenceAssessment,
    scientific_status: CalculationScientificStatus,
    max_force_ev_per_angstrom: float | None,
) -> ParsedVaspResult:
    return ParsedVaspResult(
        calculation_type=normalized.calculation_type,
        scientific_status=scientific_status,
        total_energy_ev=normalized.energies.free_energy_toten_ev,
        fermi_energy_ev=normalized.energies.fermi_energy_ev,
        max_force_ev_per_angstrom=max_force_ev_per_angstrom,
        electronic_converged=_compatibility_bool(assessment.electronic),
        ionic_converged=_compatibility_bool(assessment.ionic),
        ionic_steps=normalized.ionic_steps,
        electronic_steps=normalized.electronic_steps,
        vasp_version=normalized.vasp_version,
    )


def _compatibility_bool(verdict: ConvergenceVerdict) -> bool | None:
    if verdict is ConvergenceVerdict.CONVERGED:
        return True
    if verdict is ConvergenceVerdict.UNCONVERGED:
        return False
    return None


def _structure_provenance(
    *,
    calculation: Calculation,
    input_snapshot: StructureSnapshot,
    final_snapshot: StructureSnapshot,
    raw_artifacts: tuple[Artifact, ...],
    method_fingerprint: MethodFingerprint,
) -> tuple[tuple[ProvenanceRecord, ...], tuple[DependencyRecord, ...]]:
    if final_snapshot.id == input_snapshot.id:
        return (), ()
    contcar = next(
        (item for item in raw_artifacts if item.artifact_type is ArtifactType.CONTCAR),
        None,
    )
    if contcar is None or contcar.sha256 is None:
        raise VaspImportError("relaxed imported snapshot requires content-addressed CONTCAR")
    provenance = (
        ProvenanceRecord(
            subject_id=final_snapshot.id,
            tool=_IMPORTER_NAME,
            tool_version=_IMPORTER_VERSION,
            parameters_hash=contcar.sha256,
            method_fingerprint_id=method_fingerprint.id,
        ),
    )
    dependencies = (
        DependencyRecord(
            upstream_id=input_snapshot.id,
            downstream_id=final_snapshot.id,
            kind=DependencyKind.SCIENTIFIC,
            role="identity_parent",
            recorded_hash=scientific_hash(input_snapshot),
        ),
        DependencyRecord(
            upstream_id=contcar.id,
            downstream_id=final_snapshot.id,
            kind=DependencyKind.SCIENTIFIC,
            role="reconstructed_from_contcar",
            recorded_hash=scientific_hash(contcar),
        ),
        DependencyRecord(
            upstream_id=calculation.id,
            downstream_id=final_snapshot.id,
            kind=DependencyKind.SCIENTIFIC,
            role="calculation_context",
            recorded_hash=scientific_hash(calculation),
        ),
    )
    return provenance, dependencies

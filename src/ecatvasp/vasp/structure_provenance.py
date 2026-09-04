"""Durable provenance records for managed CONTCAR reconstruction.

This module does not reconstruct, promote, persist, or classify a VASP result. It
only turns an already-validated Block 6 reconstruction into the immutable
ProvenanceRecord/DependencyRecord graph required for project storage and
freshness evaluation.
"""

from __future__ import annotations

from dataclasses import dataclass

from ecatvasp.domain import (
    ArtifactType,
    Calculation,
    StructureSnapshot,
    canonical_sha256,
)
from ecatvasp.provenance import (
    DependencyKind,
    DependencyRecord,
    ProvenanceRecord,
    scientific_hash,
)
from ecatvasp.vasp.execution_plan import ExecutionPlan, StagingInput, StagingInputKind
from ecatvasp.vasp.result_intake import VaspResultArtifactIntake
from ecatvasp.vasp.results import VaspResultSourceRole
from ecatvasp.vasp.structure_promotion import (
    VASP_CONTCAR_RECONSTRUCTOR_NAME,
    VASP_CONTCAR_RECONSTRUCTOR_VERSION,
    VaspContcarReconstruction,
)


class VaspStructureProvenanceError(ValueError):
    """Raised when durable managed-structure provenance cannot be built exactly."""


@dataclass(frozen=True, slots=True)
class VaspContcarReconstructionProvenance:
    """Persistable provenance records for one managed CONTCAR-derived snapshot."""

    provenance_record: ProvenanceRecord
    dependency_records: tuple[DependencyRecord, ...]


def build_vasp_contcar_reconstruction_provenance(
    *,
    calculation: Calculation,
    plan: ExecutionPlan,
    intake: VaspResultArtifactIntake,
    input_snapshot: StructureSnapshot,
    reconstruction: VaspContcarReconstruction,
) -> VaspContcarReconstructionProvenance:
    """Bind a reconstructed snapshot to every immutable identity needed to reproduce it.

    The dependency set deliberately captures scientific reconstruction inputs only:
    Calculation context, immutable input snapshot, exact CONTCAR geometry, exact
    staged POSCAR, and exact atom-index map. Scientific convergence is not an input
    to reconstruction because unconverged CONTCAR candidates are intentionally
    inspectable. Promotion remains a separate explicit decision.
    """

    _validate_identity(
        calculation=calculation,
        plan=plan,
        intake=intake,
        input_snapshot=input_snapshot,
        reconstruction=reconstruction,
    )
    contcar = _require_contcar(intake)
    poscar = _require_staging_input(plan, "poscar")
    atom_map = _require_staging_input(plan, "atom_index_map")
    _validate_staging_role(
        poscar,
        artifact_type=ArtifactType.POSCAR,
        kind=StagingInputKind.VASP_INPUT,
    )
    _validate_staging_role(
        atom_map,
        artifact_type=ArtifactType.DERIVED_DATASET,
        kind=StagingInputKind.METADATA,
    )

    parameters_hash = canonical_sha256(
        {
            "calculation_id": calculation.id,
            "plan_hash": plan.plan_hash,
            "intake_hash": intake.intake_hash,
            "input_snapshot_id": input_snapshot.id,
            "input_snapshot_hash": scientific_hash(input_snapshot),
            "contcar_artifact_id": contcar.source.artifact_id,
            "contcar_sha256": contcar.source.sha256,
            "poscar_artifact_id": poscar.artifact_id,
            "poscar_sha256": poscar.sha256,
            "atom_index_map_artifact_id": atom_map.artifact_id,
            "atom_index_map_sha256": atom_map.sha256,
        }
    )
    provenance = ProvenanceRecord(
        subject_id=reconstruction.snapshot.id,
        tool=VASP_CONTCAR_RECONSTRUCTOR_NAME,
        tool_version=VASP_CONTCAR_RECONSTRUCTOR_VERSION,
        parameters_hash=parameters_hash,
        method_fingerprint_id=calculation.method_fingerprint_id,
    )
    dependencies = (
        DependencyRecord(
            upstream_id=calculation.id,
            downstream_id=reconstruction.snapshot.id,
            kind=DependencyKind.SCIENTIFIC,
            role="calculation_context",
            recorded_hash=scientific_hash(calculation),
        ),
        DependencyRecord(
            upstream_id=input_snapshot.id,
            downstream_id=reconstruction.snapshot.id,
            kind=DependencyKind.SCIENTIFIC,
            role="input_structure_identity",
            recorded_hash=scientific_hash(input_snapshot),
        ),
        DependencyRecord(
            upstream_id=contcar.source.artifact_id,
            downstream_id=reconstruction.snapshot.id,
            kind=DependencyKind.SCIENTIFIC,
            role="contcar_geometry",
            recorded_hash=contcar.source.sha256,
        ),
        DependencyRecord(
            upstream_id=poscar.artifact_id,
            downstream_id=reconstruction.snapshot.id,
            kind=DependencyKind.SCIENTIFIC,
            role="staged_poscar",
            recorded_hash=poscar.sha256,
        ),
        DependencyRecord(
            upstream_id=atom_map.artifact_id,
            downstream_id=reconstruction.snapshot.id,
            kind=DependencyKind.SCIENTIFIC,
            role="atom_index_map",
            recorded_hash=atom_map.sha256,
        ),
    )
    return VaspContcarReconstructionProvenance(
        provenance_record=provenance,
        dependency_records=dependencies,
    )


def _validate_identity(
    *,
    calculation: Calculation,
    plan: ExecutionPlan,
    intake: VaspResultArtifactIntake,
    input_snapshot: StructureSnapshot,
    reconstruction: VaspContcarReconstruction,
) -> None:
    if plan.calculation_id != calculation.id or intake.calculation_id != calculation.id:
        raise VaspStructureProvenanceError("plan/intake belongs to another Calculation")
    if plan.recipe_id != calculation.recipe_id or intake.recipe_id != calculation.recipe_id:
        raise VaspStructureProvenanceError("plan/intake recipe does not match Calculation")
    if calculation.input_structure_snapshot_id != input_snapshot.id:
        raise VaspStructureProvenanceError(
            "Calculation does not reference the supplied input snapshot"
        )
    if reconstruction.calculation_id != calculation.id:
        raise VaspStructureProvenanceError("reconstruction belongs to another Calculation")
    if reconstruction.input_snapshot_id != input_snapshot.id:
        raise VaspStructureProvenanceError("reconstruction belongs to another input snapshot")
    if reconstruction.attempt_id != intake.attempt_id:
        raise VaspStructureProvenanceError("reconstruction belongs to another ExecutionAttempt")
    if reconstruction.intake_hash.lower() != intake.intake_hash.lower():
        raise VaspStructureProvenanceError("reconstruction uses a different result intake")
    if intake.plan_hash != plan.plan_hash:
        raise VaspStructureProvenanceError("result intake does not reference the exact ExecutionPlan")
    if intake.input_manifest_hash != plan.input_manifest_sha256:
        raise VaspStructureProvenanceError(
            "result intake input manifest does not match ExecutionPlan"
        )
    if reconstruction.snapshot.parent_snapshot_id != input_snapshot.id:
        raise VaspStructureProvenanceError(
            "reconstructed snapshot does not directly reference the input snapshot"
        )


def _require_contcar(intake: VaspResultArtifactIntake):
    matches = tuple(
        item for item in intake.files if item.source.role is VaspResultSourceRole.CONTCAR
    )
    if len(matches) != 1:
        raise VaspStructureProvenanceError(
            "managed CONTCAR provenance requires exactly one CONTCAR result source"
        )
    contcar = matches[0]
    if contcar.source.artifact_id != intake.sources[
        tuple(source.role for source in intake.sources).index(VaspResultSourceRole.CONTCAR)
    ].artifact_id:
        raise VaspStructureProvenanceError("CONTCAR source identity is inconsistent")
    return contcar


def _require_staging_input(plan: ExecutionPlan, role: str) -> StagingInput:
    matches = tuple(item for item in plan.staging_inputs if item.role == role)
    if len(matches) != 1:
        raise VaspStructureProvenanceError(
            f"ExecutionPlan requires exactly one staging input with role {role!r}"
        )
    return matches[0]


def _validate_staging_role(
    item: StagingInput,
    *,
    artifact_type: ArtifactType,
    kind: StagingInputKind,
) -> None:
    if item.artifact_type is not artifact_type or item.kind is not kind:
        raise VaspStructureProvenanceError(
            f"staging input role {item.role!r} has incompatible artifact type/kind"
        )

"""Supplemental provenance for managed UID-bound VASP result datasets.

Block 5 forces/magnetization and Block 7 frequency eigenvectors are interpreted
through the exact staged POSCAR and atom-index map. Block 8 materializes raw
result sources generically and therefore cannot infer those managed staging
identities. This module explicitly adds them to the durable RESULT_PARSE
Analysis after the UID-bound result has already been produced.
"""

from __future__ import annotations

from dataclasses import replace

from ecatvasp.domain import ArtifactType, canonical_sha256
from ecatvasp.provenance import DependencyKind, DependencyRecord, scientific_hash
from ecatvasp.vasp.execution_plan import ExecutionPlan, StagingInput, StagingInputKind
from ecatvasp.vasp.result_provenance import VaspScientificResultMaterialization
from ecatvasp.vasp.results import VaspResultDocument


class VaspResultSupportingProvenanceError(ValueError):
    """Raised when managed atom-identity provenance cannot be attached exactly."""


def bind_vasp_atom_identity_result_provenance(
    *,
    plan: ExecutionPlan,
    result: VaspResultDocument,
    materialization: VaspScientificResultMaterialization,
) -> VaspScientificResultMaterialization:
    """Add exact staged POSCAR/atom-map inputs to a UID-bound RESULT_PARSE Analysis.

    The function is pure with respect to storage and files. The parsing adapters
    have already revalidated the staging bytes before creating UID-bound forces,
    magnetization, or frequency eigenvectors; this function records those exact
    content identities in the persistent scientific DAG.
    """

    calculation = materialization.updated_calculation
    if plan.calculation_id != calculation.id or plan.recipe_id != calculation.recipe_id:
        raise VaspResultSupportingProvenanceError(
            "ExecutionPlan does not match the materialized Calculation"
        )
    if result.calculation_type is not calculation.calculation_type:
        raise VaspResultSupportingProvenanceError(
            "result CalculationType does not match the materialized Calculation"
        )
    if result.forces is None and result.magnetization is None and result.frequencies is None:
        raise VaspResultSupportingProvenanceError(
            "atom identity supporting provenance requires a UID-bound result dataset"
        )

    poscar = _require_staging_input(plan, "poscar")
    atom_map = _require_staging_input(plan, "atom_index_map")
    _validate_staging_input(
        poscar,
        artifact_type=ArtifactType.POSCAR,
        kind=StagingInputKind.VASP_INPUT,
        target="POSCAR",
    )
    _validate_staging_input(
        atom_map,
        artifact_type=ArtifactType.DERIVED_DATASET,
        kind=StagingInputKind.METADATA,
        target="atom-index-map.json",
    )
    supporting = tuple(sorted((poscar, atom_map), key=lambda item: item.role))

    parse_analysis = materialization.result_parse_analysis
    existing_ids = set(parse_analysis.input_artifact_ids)
    if any(item.artifact_id in existing_ids for item in supporting):
        raise VaspResultSupportingProvenanceError(
            "atom identity supporting Artifact is already a RESULT_PARSE input"
        )
    parameters_hash = canonical_sha256(
        {
            "base_parameters_hash": parse_analysis.parameters_hash,
            "supporting_inputs": tuple(
                {
                    "role": item.role,
                    "artifact_id": item.artifact_id,
                    "artifact_type": item.artifact_type,
                    "sha256": item.sha256,
                }
                for item in supporting
            ),
        }
    )
    updated_parse = replace(
        parse_analysis,
        input_artifact_ids=(
            *parse_analysis.input_artifact_ids,
            *(item.artifact_id for item in supporting),
        ),
        parameters_hash=parameters_hash,
    )
    updated_parse_hash = scientific_hash(updated_parse)

    provenance_records = tuple(
        replace(record, parameters_hash=parameters_hash)
        if record.subject_id == parse_analysis.id
        else record
        for record in materialization.provenance_records
    )
    dependency_records: list[DependencyRecord] = []
    replaced_parse_output_edge = False
    for record in materialization.dependency_records:
        if (
            record.upstream_id == parse_analysis.id
            and record.downstream_id == materialization.parsed_result_artifact.id
            and record.role == "normalized_result_analysis"
        ):
            dependency_records.append(replace(record, recorded_hash=updated_parse_hash))
            replaced_parse_output_edge = True
        else:
            dependency_records.append(record)
    if not replaced_parse_output_edge:
        raise VaspResultSupportingProvenanceError(
            "materialization is missing the RESULT_PARSE to PARSED_RESULT dependency"
        )
    dependency_records.extend(
        DependencyRecord(
            upstream_id=item.artifact_id,
            downstream_id=updated_parse.id,
            kind=DependencyKind.SCIENTIFIC,
            role=f"atom_identity:{item.role}",
            recorded_hash=item.sha256,
        )
        for item in supporting
    )

    return VaspScientificResultMaterialization(
        updated_calculation=materialization.updated_calculation,
        result_parse_analysis=updated_parse,
        parsed_result_artifact=materialization.parsed_result_artifact,
        convergence_analysis=materialization.convergence_analysis,
        convergence_artifact=materialization.convergence_artifact,
        provenance_records=provenance_records,
        dependency_records=tuple(dependency_records),
    )


def _require_staging_input(plan: ExecutionPlan, role: str) -> StagingInput:
    matches = tuple(item for item in plan.staging_inputs if item.role == role)
    if len(matches) != 1:
        raise VaspResultSupportingProvenanceError(
            f"ExecutionPlan requires exactly one staging input with role {role!r}"
        )
    return matches[0]


def _validate_staging_input(
    item: StagingInput,
    *,
    artifact_type: ArtifactType,
    kind: StagingInputKind,
    target: str,
) -> None:
    if item.artifact_type is not artifact_type or item.kind is not kind:
        raise VaspResultSupportingProvenanceError(
            f"staging input role {item.role!r} has incompatible artifact type/kind"
        )
    if item.target_relative_path != target:
        raise VaspResultSupportingProvenanceError(
            f"staging input role {item.role!r} has unexpected target path"
        )

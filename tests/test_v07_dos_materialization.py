from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from ecatvasp.analysis import parse_vasp_doscar
from ecatvasp.analysis.dos_materialization import (
    CANONICAL_DOS_ARTIFACT_FORMAT,
    DOS_MATERIALIZER_NAME,
    DosMaterializationError,
    DurableDosMaterialization,
    load_canonical_dos_artifact,
    materialize_canonical_dos_analysis,
)
from ecatvasp.domain import (
    AnalysisProducerRef,
    AnalysisStatus,
    AnalysisType,
    Artifact,
    ArtifactAvailability,
    ArtifactType,
    Calculation,
    CalculationProducerRef,
    CalculationScientificStatus,
    CalculationType,
    ExecutionAttempt,
    ExecutionAttemptProducerRef,
    ExecutionAttemptStatus,
    KPointPolicy,
    KPointPolicyKind,
    Lattice,
    MethodDefinition,
    MethodFingerprint,
    PotcarIdentity,
    Project,
    ProtocolDefinition,
    RecipeIdentity,
    RetrievalPolicy,
    SpinTreatment,
    StructureSite,
    StructureSnapshot,
    new_atom_uid,
)
from ecatvasp.provenance import FreshnessEngine, FreshnessState, scientific_hash
from ecatvasp.schema.version import SCHEMA_VERSION
from ecatvasp.storage import ProjectBundle, ProjectStore


@dataclass(frozen=True, slots=True)
class _Case:
    project: Project
    snapshot: StructureSnapshot
    fingerprint: MethodFingerprint
    calculation: Calculation
    attempt: ExecutionAttempt
    doscar_artifact: Artifact
    atom_map_artifact: Artifact
    intake: object


def _write(path: Path, body: bytes) -> tuple[int, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return len(body), hashlib.sha256(body).hexdigest()


def _case(tmp_path: Path) -> _Case:
    project = Project(name="Durable DOS", slug="durable-dos")
    atom_uid = new_atom_uid()
    snapshot = StructureSnapshot(
        lattice=Lattice(
            vectors=((8.0, 0.0, 0.0), (0.0, 8.0, 0.0), (0.0, 0.0, 16.0))
        ),
        sites=(StructureSite(atom_uid, "C", (0.5, 0.5, 0.5)),),
    )
    fingerprint = MethodFingerprint(
        method=MethodDefinition(
            xc_functional="PBE",
            potcar_family="PBE_54",
            potcars=(PotcarIdentity("C", "C", "c" * 64),),
            dispersion_model="NONE",
            spin_treatment=SpinTreatment.UNPOLARIZED,
        ),
        protocol=ProtocolDefinition(
            encut_ev=450.0,
            kpoints=KPointPolicy(KPointPolicyKind.GAMMA_ONLY),
        ),
        recipe=RecipeIdentity("ECatVASP.VASP.DOSPrerequisite"),
    )
    calculation = Calculation(
        project_id=project.id,
        calculation_type=CalculationType.DOS_STATIC,
        input_structure_snapshot_id=snapshot.id,
        recipe_id=fingerprint.recipe.recipe_id,
        method_fingerprint_id=fingerprint.id,
        status=CalculationScientificStatus.CONVERGED,
        slug="durable-dos",
    )
    attempt = ExecutionAttempt(
        calculation_id=calculation.id,
        attempt_number=1,
        status=ExecutionAttemptStatus.PARSED,
    )

    atom_map_payload = {
        "format": "ecatvasp-v03-atom-index-map",
        "version": 1,
        "structure_snapshot_id": str(snapshot.id),
        "structure_sha256": "a" * 64,
        "poscar_sha256": "b" * 64,
        "species_order": ["C"],
        "species_counts": [1],
        "entries": [
            {
                "atom_uid": str(atom_uid),
                "element": "C",
                "snapshot_index": 0,
                "poscar_index": 0,
                "vasp_ordinal": 1,
                "selective_dynamics": None,
            }
        ],
    }
    atom_map_bytes = (
        json.dumps(atom_map_payload, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    atom_map_path = tmp_path / "calculations" / str(calculation.id) / "input" / "atom-index-map.json"
    atom_map_size, atom_map_hash = _write(atom_map_path, atom_map_bytes)
    atom_map_artifact = Artifact(
        artifact_type=ArtifactType.DERIVED_DATASET,
        producer=CalculationProducerRef(calculation.id),
        availability=ArtifactAvailability.LOCAL,
        retrieval_policy=RetrievalPolicy.ALWAYS,
        local_path=atom_map_path.relative_to(tmp_path).as_posix(),
        size_bytes=atom_map_size,
        sha256=atom_map_hash,
    )

    doscar_text = "\n".join(
        (
            "1 1 1 0",
            "header 2",
            "header 3",
            "header 4",
            "header 5",
            "1.0 -1.0 2 0.2",
            "-1.0 1.0 0.0",
            "1.0 2.0 1.0",
            "1.0 -1.0 2 0.2",
            "-1.0 1 2 3 4 5 6 7 8 9",
            "1.0 2 3 4 5 6 7 8 9 10",
            "",
        )
    )
    doscar_bytes = doscar_text.encode()
    doscar_path = tmp_path / "calculations" / str(calculation.id) / "attempt-1" / "DOSCAR"
    doscar_size, doscar_hash = _write(doscar_path, doscar_bytes)
    doscar_artifact = Artifact(
        artifact_type=ArtifactType.DOSCAR,
        producer=ExecutionAttemptProducerRef(attempt.id),
        availability=ArtifactAvailability.LOCAL,
        retrieval_policy=RetrievalPolicy.ALWAYS,
        local_path=doscar_path.relative_to(tmp_path).as_posix(),
        size_bytes=doscar_size,
        sha256=doscar_hash,
    )
    intake = parse_vasp_doscar(
        doscar_bytes=doscar_bytes,
        atom_index_map_bytes=atom_map_bytes,
        structure_snapshot_id=snapshot.id,
        spin_treatment=SpinTreatment.UNPOLARIZED,
    )
    return _Case(
        project=project,
        snapshot=snapshot,
        fingerprint=fingerprint,
        calculation=calculation,
        attempt=attempt,
        doscar_artifact=doscar_artifact,
        atom_map_artifact=atom_map_artifact,
        intake=intake,
    )


def _materialize(tmp_path: Path, case: _Case) -> DurableDosMaterialization:
    return materialize_canonical_dos_analysis(
        project_root=tmp_path,
        calculation=case.calculation,
        execution_attempt=case.attempt,
        doscar_artifact=case.doscar_artifact,
        atom_index_map_artifact=case.atom_map_artifact,
        intake=case.intake,  # type: ignore[arg-type]
    )


def test_durable_dos_materialization_builds_analysis_artifact_chain(tmp_path: Path) -> None:
    case = _case(tmp_path)
    materialized = _materialize(tmp_path, case)

    assert materialized.analysis.analysis_type is AnalysisType.DOS
    assert materialized.analysis.status is AnalysisStatus.COMPLETED
    assert materialized.analysis.input_artifact_ids == (
        case.doscar_artifact.id,
        case.atom_map_artifact.id,
    )
    assert materialized.analysis.tool == case.intake.parser_name  # type: ignore[attr-defined]
    assert materialized.artifact.artifact_type is ArtifactType.DERIVED_DATASET
    assert isinstance(materialized.artifact.producer, AnalysisProducerRef)
    assert materialized.artifact.producer.id == materialized.analysis.id
    assert materialized.provenance_records[1].tool == DOS_MATERIALIZER_NAME

    text = (tmp_path / (materialized.artifact.local_path or "")).read_text(encoding="utf-8")
    assert CANONICAL_DOS_ARTIFACT_FORMAT in text
    assert load_canonical_dos_artifact(
        project_root=tmp_path,
        analysis=materialized.analysis,
        artifact=materialized.artifact,
    ) == case.intake.result  # type: ignore[attr-defined]

    scientific_upstreams = {
        item.upstream_id
        for item in materialized.dependency_records
        if item.downstream_id == materialized.analysis.id
    }
    assert case.calculation.id in scientific_upstreams
    assert case.doscar_artifact.id in scientific_upstreams
    assert case.atom_map_artifact.id in scientific_upstreams
    assert case.attempt.id not in scientific_upstreams


def test_project_store_reopen_preserves_durable_dos_chain(tmp_path: Path) -> None:
    case = _case(tmp_path)
    materialized = _materialize(tmp_path, case)
    bundle = ProjectBundle(
        project=case.project,
        structure_snapshots=(case.snapshot,),
        method_fingerprints=(case.fingerprint,),
        calculations=(case.calculation,),
        execution_attempts=(case.attempt,),
        artifacts=(
            case.doscar_artifact,
            case.atom_map_artifact,
            materialized.artifact,
        ),
        analyses=(materialized.analysis,),
        provenance_records=materialized.provenance_records,
        dependency_records=materialized.dependency_records,
    )
    bundle.validate()
    ProjectStore(tmp_path).save(bundle)
    reopened = ProjectStore(tmp_path).open()

    assert reopened == bundle
    assert SCHEMA_VERSION == 3
    reopened_analysis = reopened.analyses[0]
    reopened_artifact = next(
        item
        for item in reopened.artifacts
        if isinstance(item.producer, AnalysisProducerRef)
    )
    assert load_canonical_dos_artifact(
        project_root=tmp_path,
        analysis=reopened_analysis,
        artifact=reopened_artifact,
    ) == case.intake.result  # type: ignore[attr-defined]


def test_doscar_hash_drift_stales_analysis_and_dataset(tmp_path: Path) -> None:
    case = _case(tmp_path)
    materialized = _materialize(tmp_path, case)
    node_ids = {
        case.calculation.id,
        case.doscar_artifact.id,
        case.atom_map_artifact.id,
        materialized.analysis.id,
        materialized.artifact.id,
    }
    current_hashes = {
        case.calculation.id: scientific_hash(case.calculation),
        case.doscar_artifact.id: "f" * 64,
        case.atom_map_artifact.id: scientific_hash(case.atom_map_artifact),
        materialized.analysis.id: scientific_hash(materialized.analysis),
    }
    freshness = FreshnessEngine(materialized.dependency_records).evaluate(
        node_ids=node_ids,
        current_hashes=current_hashes,
    )

    assert freshness[materialized.analysis.id].state is FreshnessState.STALE
    assert freshness[materialized.artifact.id].state is FreshnessState.STALE


def test_materialization_requires_scientifically_converged_dos_static(tmp_path: Path) -> None:
    case = _case(tmp_path)
    unconverged = replace(
        case.calculation,
        status=CalculationScientificStatus.COMPLETED_UNCONVERGED,
    )
    with pytest.raises(DosMaterializationError, match="scientifically converged"):
        materialize_canonical_dos_analysis(
            project_root=tmp_path,
            calculation=unconverged,
            execution_attempt=case.attempt,
            doscar_artifact=case.doscar_artifact,
            atom_index_map_artifact=case.atom_map_artifact,
            intake=case.intake,  # type: ignore[arg-type]
        )


def test_execution_attempt_is_validated_but_not_scientific_identity(tmp_path: Path) -> None:
    case = _case(tmp_path)
    other_attempt = ExecutionAttempt(
        calculation_id=case.calculation.id,
        attempt_number=2,
        status=ExecutionAttemptStatus.PARSED,
    )
    with pytest.raises(DosMaterializationError, match="producer does not match"):
        materialize_canonical_dos_analysis(
            project_root=tmp_path,
            calculation=case.calculation,
            execution_attempt=other_attempt,
            doscar_artifact=case.doscar_artifact,
            atom_index_map_artifact=case.atom_map_artifact,
            intake=case.intake,  # type: ignore[arg-type]
        )


def test_loader_detects_canonical_dataset_tampering(tmp_path: Path) -> None:
    case = _case(tmp_path)
    materialized = _materialize(tmp_path, case)
    path = tmp_path / (materialized.artifact.local_path or "")
    path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")

    with pytest.raises(DosMaterializationError, match="content hash changed"):
        load_canonical_dos_artifact(
            project_root=tmp_path,
            analysis=materialized.analysis,
            artifact=materialized.artifact,
        )

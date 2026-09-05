from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from ecatvasp.analysis.bader import (
    BaderAnalysisError,
    BaderReferenceMode,
    CanonicalBaderIntake,
    DurableBaderMaterialization,
    load_canonical_bader_artifact,
    materialize_bader_analysis,
    parse_bader_acf,
)
from ecatvasp.analysis.electronic import ExternalInputDigest, ExternalToolInvocation
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
    charge_artifact: Artifact
    atom_map_artifact: Artifact
    atom_map_bytes: bytes
    acf_bytes: bytes
    intake: CanonicalBaderIntake


def _write(path: Path, body: bytes) -> tuple[int, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return len(body), hashlib.sha256(body).hexdigest()


def _case(tmp_path: Path) -> _Case:
    project = Project(name="Bader", slug="bader")
    carbon_uid = new_atom_uid()
    oxygen_uid = new_atom_uid()
    snapshot = StructureSnapshot(
        lattice=Lattice(
            vectors=((9.0, 0.0, 0.0), (0.0, 9.0, 0.0), (0.0, 0.0, 18.0))
        ),
        sites=(
            StructureSite(carbon_uid, "C", (0.25, 0.25, 0.5)),
            StructureSite(oxygen_uid, "O", (0.75, 0.75, 0.5)),
        ),
    )
    fingerprint = MethodFingerprint(
        method=MethodDefinition(
            xc_functional="PBE",
            potcar_family="PBE_54",
            potcars=(
                PotcarIdentity("C", "C", "c" * 64),
                PotcarIdentity("O", "O", "d" * 64),
            ),
            dispersion_model="NONE",
            spin_treatment=SpinTreatment.UNPOLARIZED,
        ),
        protocol=ProtocolDefinition(
            encut_ev=450.0,
            kpoints=KPointPolicy(KPointPolicyKind.GAMMA_ONLY),
        ),
        recipe=RecipeIdentity("ECatVASP.VASP.ChargeDensityStatic"),
    )
    calculation = Calculation(
        project_id=project.id,
        calculation_type=CalculationType.CHARGE_STATIC,
        input_structure_snapshot_id=snapshot.id,
        recipe_id=fingerprint.recipe.recipe_id,
        method_fingerprint_id=fingerprint.id,
        status=CalculationScientificStatus.CONVERGED,
        slug="bader-source",
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
        "species_order": ["C", "O"],
        "species_counts": [1, 1],
        "entries": [
            {
                "atom_uid": str(carbon_uid),
                "element": "C",
                "snapshot_index": 0,
                "poscar_index": 0,
                "vasp_ordinal": 1,
                "selective_dynamics": None,
            },
            {
                "atom_uid": str(oxygen_uid),
                "element": "O",
                "snapshot_index": 1,
                "poscar_index": 1,
                "vasp_ordinal": 2,
                "selective_dynamics": None,
            },
        ],
    }
    atom_map_bytes = (
        json.dumps(atom_map_payload, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    atom_map_path = (
        tmp_path
        / "calculations"
        / str(calculation.id)
        / "input"
        / "atom-index-map.json"
    )
    map_size, map_hash = _write(atom_map_path, atom_map_bytes)
    atom_map_artifact = Artifact(
        artifact_type=ArtifactType.DERIVED_DATASET,
        producer=CalculationProducerRef(calculation.id),
        availability=ArtifactAvailability.LOCAL,
        retrieval_policy=RetrievalPolicy.ALWAYS,
        local_path=atom_map_path.relative_to(tmp_path).as_posix(),
        size_bytes=map_size,
        sha256=map_hash,
    )

    charge_bytes = b"synthetic CHGCAR bytes for provenance test\n"
    charge_path = (
        tmp_path / "calculations" / str(calculation.id) / "attempt-1" / "CHGCAR"
    )
    charge_size, charge_hash = _write(charge_path, charge_bytes)
    charge_artifact = Artifact(
        artifact_type=ArtifactType.CHGCAR,
        producer=ExecutionAttemptProducerRef(attempt.id),
        availability=ArtifactAvailability.LOCAL,
        retrieval_policy=RetrievalPolicy.ALWAYS,
        local_path=charge_path.relative_to(tmp_path).as_posix(),
        size_bytes=charge_size,
        sha256=charge_hash,
    )
    invocation = ExternalToolInvocation(
        tool="bader",
        tool_version="1.05",
        argv=("bader", "CHGCAR"),
        inputs=(ExternalInputDigest("charge_density", charge_hash),),
    )
    # Coordinates are intentionally unrelated to the StructureSnapshot. Identity must
    # come exclusively from row ordinal + frozen atom-index-map.json.
    acf_text = "\n".join(
        (
            "# X Y Z CHARGE MIN DIST ATOMIC VOL",
            "------------------------------------------------------------",
            "1 99.0 98.0 97.0 4.250000 0.410000 12.500000",
            "2 -50.0 -49.0 -48.0 6.750000 0.390000 11.500000",
            "------------------------------------------------------------",
            "VACUUM CHARGE: 0.000000",
            "VACUUM VOLUME: 0.000000",
            "NUMBER OF ELECTRONS: 11.000000",
            "",
        )
    )
    acf_bytes = acf_text.encode()
    intake = parse_bader_acf(
        acf_bytes=acf_bytes,
        atom_index_map_bytes=atom_map_bytes,
        structure_snapshot_id=snapshot.id,
        invocation=invocation,
        reference_mode=BaderReferenceMode.CHGCAR_ONLY,
    )
    return _Case(
        project=project,
        snapshot=snapshot,
        fingerprint=fingerprint,
        calculation=calculation,
        attempt=attempt,
        charge_artifact=charge_artifact,
        atom_map_artifact=atom_map_artifact,
        atom_map_bytes=atom_map_bytes,
        acf_bytes=acf_bytes,
        intake=intake,
    )


def _materialize(tmp_path: Path, case: _Case) -> DurableBaderMaterialization:
    return materialize_bader_analysis(
        project_root=tmp_path,
        calculation=case.calculation,
        execution_attempt=case.attempt,
        charge_density_artifact=case.charge_artifact,
        atom_index_map_artifact=case.atom_map_artifact,
        intake=case.intake,
        acf_bytes=case.acf_bytes,
    )


def test_bader_parser_binds_by_ordinal_not_acf_coordinates(tmp_path: Path) -> None:
    case = _case(tmp_path)

    assert case.intake.result.sites[0].atom_uid == case.snapshot.sites[0].atom_uid
    assert case.intake.result.sites[1].atom_uid == case.snapshot.sites[1].atom_uid
    assert case.intake.result.sites[0].electron_count == pytest.approx(4.25)
    assert case.intake.result.sites[1].electron_count == pytest.approx(6.75)
    assert case.intake.result.number_of_electrons == pytest.approx(11.0)
    assert case.intake.result.reference_mode is BaderReferenceMode.CHGCAR_ONLY


def test_bader_materialization_builds_raw_and_normalized_outputs(tmp_path: Path) -> None:
    case = _case(tmp_path)
    result = _materialize(tmp_path, case)

    assert result.analysis.analysis_type is AnalysisType.BADER
    assert result.analysis.status is AnalysisStatus.COMPLETED
    assert result.analysis.input_artifact_ids == (
        case.charge_artifact.id,
        case.atom_map_artifact.id,
    )
    assert result.acf_artifact.artifact_type is ArtifactType.ACF_DAT
    assert result.result_artifact.artifact_type is ArtifactType.DERIVED_DATASET
    assert isinstance(result.acf_artifact.producer, AnalysisProducerRef)
    assert isinstance(result.result_artifact.producer, AnalysisProducerRef)
    assert result.acf_artifact.producer.id == result.analysis.id
    assert result.result_artifact.producer.id == result.analysis.id
    reopened = load_canonical_bader_artifact(
        project_root=tmp_path,
        analysis=result.analysis,
        acf_artifact=result.acf_artifact,
        result_artifact=result.result_artifact,
    )
    assert reopened == case.intake.result


def test_bader_project_store_roundtrip_and_freshness(tmp_path: Path) -> None:
    case = _case(tmp_path)
    result = _materialize(tmp_path, case)
    bundle = ProjectBundle(
        project=case.project,
        structure_snapshots=(case.snapshot,),
        method_fingerprints=(case.fingerprint,),
        calculations=(case.calculation,),
        execution_attempts=(case.attempt,),
        artifacts=(
            case.charge_artifact,
            case.atom_map_artifact,
            result.acf_artifact,
            result.result_artifact,
        ),
        analyses=(result.analysis,),
        provenance_records=result.provenance_records,
        dependency_records=result.dependency_records,
    )
    bundle.validate()
    ProjectStore(tmp_path).save(bundle)
    reopened_bundle = ProjectStore(tmp_path).open()
    assert reopened_bundle == bundle
    assert SCHEMA_VERSION == 3

    node_ids = {
        case.calculation.id,
        case.charge_artifact.id,
        case.atom_map_artifact.id,
        result.analysis.id,
        result.acf_artifact.id,
        result.result_artifact.id,
    }
    current_hashes = {
        case.calculation.id: scientific_hash(case.calculation),
        case.charge_artifact.id: "f" * 64,
        case.atom_map_artifact.id: scientific_hash(case.atom_map_artifact),
        result.analysis.id: scientific_hash(result.analysis),
    }
    freshness = FreshnessEngine(result.dependency_records).evaluate(
        node_ids=node_ids,
        current_hashes=current_hashes,
    )
    assert freshness[result.analysis.id].state is FreshnessState.STALE
    assert freshness[result.acf_artifact.id].state is FreshnessState.STALE
    assert freshness[result.result_artifact.id].state is FreshnessState.STALE
    scientific_upstreams = {
        item.upstream_id
        for item in result.dependency_records
        if item.downstream_id == result.analysis.id
    }
    assert case.attempt.id not in scientific_upstreams


def test_explicit_reference_is_a_scientific_input(tmp_path: Path) -> None:
    case = _case(tmp_path)
    reference_bytes = b"synthetic CHGCAR_sum reference density\n"
    reference_path = (
        tmp_path
        / "calculations"
        / str(case.calculation.id)
        / "analysis-input"
        / "CHGCAR_sum"
    )
    reference_size, reference_hash = _write(reference_path, reference_bytes)
    reference_artifact = Artifact(
        artifact_type=ArtifactType.DERIVED_DATASET,
        producer=CalculationProducerRef(case.calculation.id),
        availability=ArtifactAvailability.LOCAL,
        retrieval_policy=RetrievalPolicy.ALWAYS,
        local_path=reference_path.relative_to(tmp_path).as_posix(),
        size_bytes=reference_size,
        sha256=reference_hash,
    )
    invocation = ExternalToolInvocation(
        tool="bader",
        tool_version="1.05",
        argv=("bader", "CHGCAR", "-ref", "CHGCAR_sum"),
        inputs=(
            ExternalInputDigest("charge_density", case.charge_artifact.sha256 or ""),
            ExternalInputDigest("reference_charge_density", reference_hash),
        ),
    )
    intake = parse_bader_acf(
        acf_bytes=case.acf_bytes,
        atom_index_map_bytes=case.atom_map_bytes,
        structure_snapshot_id=case.snapshot.id,
        invocation=invocation,
        reference_mode=BaderReferenceMode.EXPLICIT_REFERENCE,
    )
    result = materialize_bader_analysis(
        project_root=tmp_path,
        calculation=case.calculation,
        execution_attempt=case.attempt,
        charge_density_artifact=case.charge_artifact,
        atom_index_map_artifact=case.atom_map_artifact,
        intake=intake,
        acf_bytes=case.acf_bytes,
        reference_artifact=reference_artifact,
    )

    assert result.analysis.input_artifact_ids[-1] == reference_artifact.id
    reference_edges = tuple(
        item
        for item in result.dependency_records
        if item.role == "reference_charge_density"
    )
    assert len(reference_edges) == 1
    assert reference_edges[0].upstream_id == reference_artifact.id


def test_reference_mode_and_invocation_must_agree(tmp_path: Path) -> None:
    case = _case(tmp_path)
    with pytest.raises(BaderAnalysisError, match="requires charge/reference"):
        parse_bader_acf(
            acf_bytes=case.acf_bytes,
            atom_index_map_bytes=case.atom_map_bytes,
            structure_snapshot_id=case.snapshot.id,
            invocation=case.intake.invocation,
            reference_mode=BaderReferenceMode.EXPLICIT_REFERENCE,
        )


def test_bader_requires_converged_charge_static(tmp_path: Path) -> None:
    case = _case(tmp_path)
    unconverged = replace(
        case.calculation,
        status=CalculationScientificStatus.COMPLETED_UNCONVERGED,
    )
    with pytest.raises(BaderAnalysisError, match="scientifically converged"):
        materialize_bader_analysis(
            project_root=tmp_path,
            calculation=unconverged,
            execution_attempt=case.attempt,
            charge_density_artifact=case.charge_artifact,
            atom_index_map_artifact=case.atom_map_artifact,
            intake=case.intake,
            acf_bytes=case.acf_bytes,
        )


def test_acf_row_count_and_bytes_are_fail_closed(tmp_path: Path) -> None:
    case = _case(tmp_path)
    truncated = case.acf_bytes.replace(
        b"2 -50.0 -49.0 -48.0 6.750000 0.390000 11.500000\n",
        b"",
    )
    with pytest.raises(BaderAnalysisError, match="atom count"):
        parse_bader_acf(
            acf_bytes=truncated,
            atom_index_map_bytes=case.atom_map_bytes,
            structure_snapshot_id=case.snapshot.id,
            invocation=case.intake.invocation,
            reference_mode=BaderReferenceMode.CHGCAR_ONLY,
        )

    with pytest.raises(BaderAnalysisError, match="bytes differ"):
        materialize_bader_analysis(
            project_root=tmp_path,
            calculation=case.calculation,
            execution_attempt=case.attempt,
            charge_density_artifact=case.charge_artifact,
            atom_index_map_artifact=case.atom_map_artifact,
            intake=case.intake,
            acf_bytes=case.acf_bytes + b"tamper",
        )

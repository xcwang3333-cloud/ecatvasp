from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from ecatvasp import domain, vasp
from ecatvasp.analysis.electronic import ExternalInputDigest, ExternalToolInvocation, SpinChannel
from ecatvasp.analysis.lobster import (
    CohpEnergyReference,
    DurableCohpMaterialization,
    LobsterCohpError,
    load_canonical_cohp_artifact,
    materialize_lobster_cohp_analysis,
    parse_lobster_cohp,
)
from ecatvasp.provenance import FreshnessEngine, FreshnessState, scientific_hash
from ecatvasp.schema.version import SCHEMA_VERSION
from ecatvasp.storage import ProjectBundle, ProjectStore


@dataclass(frozen=True, slots=True)
class _Case:
    project: domain.Project
    snapshot: domain.StructureSnapshot
    fingerprint: domain.MethodFingerprint
    calculation: domain.Calculation
    attempt: domain.ExecutionAttempt
    wavecar: domain.Artifact
    atom_map: domain.Artifact
    prerequisite_inputs: tuple[domain.Artifact, ...]
    cohpcar_bytes: bytes
    icohplist_bytes: bytes
    invocation: ExternalToolInvocation


def _write(root: Path, relative: str, body: bytes) -> tuple[str, int, str]:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return relative, len(body), hashlib.sha256(body).hexdigest()


def _artifact(
    *,
    root: Path,
    calculation: domain.Calculation,
    artifact_type: domain.ArtifactType,
    filename: str,
    body: bytes,
) -> domain.Artifact:
    relative, size, digest = _write(
        root,
        f"calculations/{calculation.id}/input/{filename}",
        body,
    )
    return domain.Artifact(
        artifact_type=artifact_type,
        producer=domain.CalculationProducerRef(calculation.id),
        availability=domain.ArtifactAvailability.LOCAL,
        retrieval_policy=domain.RetrievalPolicy.ALWAYS,
        local_path=relative,
        size_bytes=size,
        sha256=digest,
    )


def _cohpcar_unpolarized() -> bytes:
    return (
        b"COHP# synthetic\n"
        b"2 1 3 -1.0 1.0 5.500000\n"
        b"Average\n"
        b"No.1:C1[0 0 0]->O2[0 0 0](1.500000)\n"
        b"-1.0 -0.10 -0.10 -0.20 -0.20\n"
        b"0.0 -0.30 -0.40 -0.50 -0.70\n"
        b"1.0 -0.20 -0.60 -0.10 -0.80\n"
    )


def _icohplist_unpolarized(value: float = -0.7) -> bytes:
    return (
        "# ICOHPLIST synthetic\n"
        "1 C1 O2 1.500000 0 0 0 " + f"{value:.6f}" + "\n"
    ).encode()


def _case(tmp_path: Path) -> _Case:
    project = domain.Project(name="LOBSTER COHP", slug="lobster-cohp-v07")
    carbon_uid = domain.new_atom_uid()
    oxygen_uid = domain.new_atom_uid()
    snapshot = domain.StructureSnapshot(
        lattice=domain.Lattice(
            vectors=((5.0, 0.0, 0.0), (0.0, 5.0, 0.0), (0.0, 0.0, 18.0))
        ),
        sites=(
            domain.StructureSite(carbon_uid, "C", (0.25, 0.25, 0.50)),
            domain.StructureSite(oxygen_uid, "O", (0.25, 0.25, 0.5833333333)),
        ),
        periodic=(True, True, False),
    )
    fingerprint = domain.MethodFingerprint(
        method=domain.MethodDefinition(
            xc_functional="PBE",
            potcar_family="PBE_54",
            potcars=(
                domain.PotcarIdentity("C", "C", "c" * 64),
                domain.PotcarIdentity("O", "O", "d" * 64),
            ),
            dispersion_model="NONE",
            spin_treatment=domain.SpinTreatment.UNPOLARIZED,
        ),
        protocol=domain.ProtocolDefinition(
            encut_ev=450.0,
            kpoints=domain.KPointPolicy(domain.KPointPolicyKind.GAMMA_ONLY),
            isym=0,
        ),
        recipe=domain.RecipeIdentity(
            vasp.RECIPE_LOBSTER_PREREQUISITE,
            parameters=vasp.lobster_recipe_parameters(nbands=80),
        ),
    )
    calculation = domain.Calculation(
        project_id=project.id,
        calculation_type=domain.CalculationType.LOBSTER_PREREQUISITE,
        input_structure_snapshot_id=snapshot.id,
        recipe_id=fingerprint.recipe.recipe_id,
        method_fingerprint_id=fingerprint.id,
        status=domain.CalculationScientificStatus.CONVERGED,
        slug="lobster-prerequisite",
    )
    attempt = domain.ExecutionAttempt(
        calculation_id=calculation.id,
        attempt_number=1,
        status=domain.ExecutionAttemptStatus.PARSED,
    )

    poscar_body = b"POSCAR exact prerequisite\n"
    incar_body = b"INCAR exact prerequisite\n"
    kpoints_body = b"KPOINTS exact prerequisite\n"
    potcar_spec_body = b"POTCAR.spec exact prerequisite\n"
    prerequisite_inputs = (
        _artifact(
            root=tmp_path,
            calculation=calculation,
            artifact_type=domain.ArtifactType.POSCAR,
            filename="POSCAR",
            body=poscar_body,
        ),
        _artifact(
            root=tmp_path,
            calculation=calculation,
            artifact_type=domain.ArtifactType.INCAR,
            filename="INCAR",
            body=incar_body,
        ),
        _artifact(
            root=tmp_path,
            calculation=calculation,
            artifact_type=domain.ArtifactType.KPOINTS,
            filename="KPOINTS",
            body=kpoints_body,
        ),
        _artifact(
            root=tmp_path,
            calculation=calculation,
            artifact_type=domain.ArtifactType.POTCAR_SPEC,
            filename="POTCAR.spec",
            body=potcar_spec_body,
        ),
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
    atom_map_body = (
        json.dumps(atom_map_payload, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    atom_map = _artifact(
        root=tmp_path,
        calculation=calculation,
        artifact_type=domain.ArtifactType.DERIVED_DATASET,
        filename="atom-index-map.json",
        body=atom_map_body,
    )

    wavecar_body = b"synthetic exact WAVECAR bytes"
    relative, size, wavecar_hash = _write(
        tmp_path,
        f"calculations/{calculation.id}/attempt-1/WAVECAR",
        wavecar_body,
    )
    wavecar = domain.Artifact(
        artifact_type=domain.ArtifactType.WAVECAR,
        producer=domain.ExecutionAttemptProducerRef(attempt.id),
        availability=domain.ArtifactAvailability.LOCAL,
        retrieval_policy=domain.RetrievalPolicy.ALWAYS,
        local_path=relative,
        size_bytes=size,
        sha256=wavecar_hash,
    )
    invocation = ExternalToolInvocation(
        tool="lobster",
        tool_version="5.1.1",
        argv=("lobster",),
        inputs=(
            ExternalInputDigest("wavefunction", wavecar_hash),
            ExternalInputDigest(
                "poscar",
                hashlib.sha256(poscar_body).hexdigest(),
            ),
            ExternalInputDigest("potcar", hashlib.sha256(b"licensed POTCAR body").hexdigest()),
            ExternalInputDigest("lobsterin", hashlib.sha256(b"lobsterin exact body").hexdigest()),
        ),
    )
    return _Case(
        project=project,
        snapshot=snapshot,
        fingerprint=fingerprint,
        calculation=calculation,
        attempt=attempt,
        wavecar=wavecar,
        atom_map=atom_map,
        prerequisite_inputs=prerequisite_inputs,
        cohpcar_bytes=_cohpcar_unpolarized(),
        icohplist_bytes=_icohplist_unpolarized(),
        invocation=invocation,
    )


def _intake(case: _Case):
    atom_map_path = Path(case.atom_map.local_path or "")
    root = case_root = None
    del root, case_root
    # Artifact paths are project-relative; callers provide the bytes from the exact file fixture.
    return atom_map_path


def _parse(case: _Case, tmp_path: Path):
    atom_map_bytes = (tmp_path / (case.atom_map.local_path or "")).read_bytes()
    return parse_lobster_cohp(
        cohpcar_bytes=case.cohpcar_bytes,
        icohplist_bytes=case.icohplist_bytes,
        atom_index_map_bytes=atom_map_bytes,
        structure_snapshot_id=case.snapshot.id,
        invocation=case.invocation,
    )


def _materialize(case: _Case, tmp_path: Path) -> DurableCohpMaterialization:
    intake = _parse(case, tmp_path)
    return materialize_lobster_cohp_analysis(
        project_root=tmp_path,
        calculation=case.calculation,
        snapshot=case.snapshot,
        method_fingerprint=case.fingerprint,
        execution_attempt=case.attempt,
        wavecar_artifact=case.wavecar,
        atom_index_map_artifact=case.atom_map,
        prerequisite_input_artifacts=case.prerequisite_inputs,
        cohpcar_bytes=case.cohpcar_bytes,
        icohplist_bytes=case.icohplist_bytes,
        intake=intake,
    )


def test_lobster_parser_preserves_native_sign_and_atom_identity(tmp_path: Path) -> None:
    case = _case(tmp_path)
    intake = _parse(case, tmp_path)
    result = intake.result

    assert result.energy_reference is CohpEnergyReference.LOBSTER_FERMI_RELATIVE
    assert result.energies_ev_relative_to_fermi == (-1.0, 0.0, 1.0)
    assert result.source_fermi_energy_ev == pytest.approx(5.5)
    interaction = result.interactions[0]
    assert interaction.atom_uid_a == case.snapshot.sites[0].atom_uid
    assert interaction.atom_uid_b == case.snapshot.sites[1].atom_uid
    assert interaction.cell_a == (0, 0, 0)
    assert interaction.cell_b == (0, 0, 0)
    assert interaction.series[0].spin is SpinChannel.TOTAL
    assert interaction.series[0].cohp_values == (-0.2, -0.5, -0.1)
    assert interaction.series[0].icohp_values == (-0.2, -0.7, -0.8)
    assert interaction.series[0].icohp_at_fermi_ev == pytest.approx(-0.7)
    assert all(value < 0 for value in interaction.series[0].cohp_values)


def test_lobster_parser_supports_collinear_up_down(tmp_path: Path) -> None:
    case = _case(tmp_path)
    cohpcar = (
        b"COHP# synthetic spin\n"
        b"2 2 3 -1.0 1.0 5.500000\n"
        b"Average\n"
        b"No.1:C1->O2(1.500000)\n"
        b"-1.0 -0.10 -0.10 -0.20 -0.20 -0.11 -0.11 -0.21 -0.21\n"
        b"0.0 -0.30 -0.40 -0.50 -0.70 -0.31 -0.41 -0.51 -0.71\n"
        b"1.0 -0.20 -0.60 -0.10 -0.80 -0.22 -0.62 -0.12 -0.82\n"
    )
    icohplist = b"1 C1 O2 1.500000 0 0 0 -0.700000 -0.710000\n"
    atom_map_bytes = (tmp_path / (case.atom_map.local_path or "")).read_bytes()
    intake = parse_lobster_cohp(
        cohpcar_bytes=cohpcar,
        icohplist_bytes=icohplist,
        atom_index_map_bytes=atom_map_bytes,
        structure_snapshot_id=case.snapshot.id,
        invocation=case.invocation,
    )

    pair = intake.result.interactions[0]
    assert tuple(item.spin for item in pair.series) == (SpinChannel.UP, SpinChannel.DOWN)
    assert pair.series[0].icohp_at_fermi_ev == pytest.approx(-0.7)
    assert pair.series[1].icohp_at_fermi_ev == pytest.approx(-0.71)
    assert pair.series[1].cohp_values == (-0.21, -0.51, -0.12)


def test_lobster_materialization_roundtrip_and_freshness(tmp_path: Path) -> None:
    case = _case(tmp_path)
    durable = _materialize(case, tmp_path)
    assert durable.analysis.analysis_type is domain.AnalysisType.COHP
    assert durable.cohpcar_artifact.artifact_type is domain.ArtifactType.COHPCAR_LOBSTER
    assert durable.icohplist_artifact.artifact_type is domain.ArtifactType.ICOHPLIST_LOBSTER
    assert durable.result_artifact.artifact_type is domain.ArtifactType.DERIVED_DATASET
    assert isinstance(durable.cohpcar_artifact.producer, domain.AnalysisProducerRef)
    assert isinstance(durable.icohplist_artifact.producer, domain.AnalysisProducerRef)

    reopened = load_canonical_cohp_artifact(
        project_root=tmp_path,
        analysis=durable.analysis,
        cohpcar_artifact=durable.cohpcar_artifact,
        icohplist_artifact=durable.icohplist_artifact,
        result_artifact=durable.result_artifact,
    )
    assert reopened.content_hash == _parse(case, tmp_path).result.content_hash

    bundle = ProjectBundle(
        project=case.project,
        structure_snapshots=(case.snapshot,),
        method_fingerprints=(case.fingerprint,),
        calculations=(case.calculation,),
        execution_attempts=(case.attempt,),
        artifacts=(
            case.wavecar,
            case.atom_map,
            *case.prerequisite_inputs,
            durable.cohpcar_artifact,
            durable.icohplist_artifact,
            durable.result_artifact,
        ),
        analyses=(durable.analysis,),
        provenance_records=durable.provenance_records,
        dependency_records=durable.dependency_records,
    )
    bundle.validate()
    ProjectStore(tmp_path).save(bundle)
    assert ProjectStore(tmp_path).open() == bundle
    assert SCHEMA_VERSION == 3

    upstreams = (
        case.calculation,
        case.snapshot,
        case.fingerprint,
        case.wavecar,
        case.atom_map,
        *case.prerequisite_inputs,
    )
    current_hashes = {item.id: scientific_hash(item) for item in upstreams}
    current_hashes[case.wavecar.id] = "f" * 64
    current_hashes[durable.analysis.id] = scientific_hash(durable.analysis)
    node_ids = {
        *(item.id for item in upstreams),
        durable.analysis.id,
        durable.cohpcar_artifact.id,
        durable.icohplist_artifact.id,
        durable.result_artifact.id,
    }
    freshness = FreshnessEngine(durable.dependency_records).evaluate(
        node_ids=node_ids,
        current_hashes=current_hashes,
    )
    assert freshness[durable.analysis.id].state is FreshnessState.STALE
    assert freshness[durable.result_artifact.id].state is FreshnessState.STALE
    analysis_upstreams = {
        item.upstream_id
        for item in durable.dependency_records
        if item.downstream_id == durable.analysis.id
    }
    assert case.attempt.id not in analysis_upstreams


def test_lobster_rejects_wrong_prerequisite_and_invocation_hash(tmp_path: Path) -> None:
    case = _case(tmp_path)
    intake = _parse(case, tmp_path)
    wrong = replace(case.calculation, calculation_type=domain.CalculationType.STATIC)
    with pytest.raises(LobsterCohpError, match="LOBSTER_PREREQUISITE"):
        materialize_lobster_cohp_analysis(
            project_root=tmp_path,
            calculation=wrong,
            snapshot=case.snapshot,
            method_fingerprint=case.fingerprint,
            execution_attempt=case.attempt,
            wavecar_artifact=case.wavecar,
            atom_index_map_artifact=case.atom_map,
            prerequisite_input_artifacts=case.prerequisite_inputs,
            cohpcar_bytes=case.cohpcar_bytes,
            icohplist_bytes=case.icohplist_bytes,
            intake=intake,
        )

    bad_invocation = replace(
        case.invocation,
        inputs=(
            ExternalInputDigest("wavefunction", "0" * 64),
            *case.invocation.inputs[1:],
        ),
    )
    bad_intake = replace(intake, invocation=bad_invocation)
    with pytest.raises(LobsterCohpError, match="wavefunction digest"):
        materialize_lobster_cohp_analysis(
            project_root=tmp_path,
            calculation=case.calculation,
            snapshot=case.snapshot,
            method_fingerprint=case.fingerprint,
            execution_attempt=case.attempt,
            wavecar_artifact=case.wavecar,
            atom_index_map_artifact=case.atom_map,
            prerequisite_input_artifacts=case.prerequisite_inputs,
            cohpcar_bytes=case.cohpcar_bytes,
            icohplist_bytes=case.icohplist_bytes,
            intake=bad_intake,
        )


def test_lobster_rejects_icohp_fermi_disagreement(tmp_path: Path) -> None:
    case = _case(tmp_path)
    atom_map_bytes = (tmp_path / (case.atom_map.local_path or "")).read_bytes()
    with pytest.raises(LobsterCohpError, match="ICOHP\(E_F\) values disagree"):
        parse_lobster_cohp(
            cohpcar_bytes=case.cohpcar_bytes,
            icohplist_bytes=_icohplist_unpolarized(-0.5),
            atom_index_map_bytes=atom_map_bytes,
            structure_snapshot_id=case.snapshot.id,
            invocation=case.invocation,
        )


def test_lobster_loader_rejects_analysis_input_drift(tmp_path: Path) -> None:
    case = _case(tmp_path)
    durable = _materialize(case, tmp_path)
    drifted = replace(
        durable.analysis,
        input_artifact_ids=(case.wavecar.id,),
    )
    with pytest.raises(LobsterCohpError, match="source inputs differ from Analysis"):
        load_canonical_cohp_artifact(
            project_root=tmp_path,
            analysis=drifted,
            cohpcar_artifact=durable.cohpcar_artifact,
            icohplist_artifact=durable.icohplist_artifact,
            result_artifact=durable.result_artifact,
        )


def test_lobster_loader_detects_raw_tamper(tmp_path: Path) -> None:
    case = _case(tmp_path)
    durable = _materialize(case, tmp_path)
    path = tmp_path / (durable.cohpcar_artifact.local_path or "")
    path.write_bytes(path.read_bytes() + b"tamper")
    with pytest.raises(LobsterCohpError, match="byte size changed"):
        load_canonical_cohp_artifact(
            project_root=tmp_path,
            analysis=durable.analysis,
            cohpcar_artifact=durable.cohpcar_artifact,
            icohplist_artifact=durable.icohplist_artifact,
            result_artifact=durable.result_artifact,
        )

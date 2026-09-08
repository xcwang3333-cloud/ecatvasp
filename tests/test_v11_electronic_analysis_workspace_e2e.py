from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from ecatvasp.analysis import (
    BandCenterEnergyReference,
    BandCenterKind,
    BandCenterSpinMode,
    ProjectionScope,
)
from ecatvasp.api.application import ApplicationServiceError
from ecatvasp.api.electronic_workspace import ProjectElectronicAnalysisApplicationService
from ecatvasp.domain import (
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
from ecatvasp.storage import ProjectBundle, ProjectStore


def _write(path: Path, body: bytes) -> tuple[int, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return len(body), hashlib.sha256(body).hexdigest()


def _store_with_dos_source(tmp_path: Path) -> tuple[ProjectStore, Calculation, str]:
    project = Project(name="Block 6 application E2E", slug="block6-application-e2e")
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
        slug="block6-dos-source",
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
    atom_map_path = (
        tmp_path
        / "calculations"
        / str(calculation.id)
        / "input"
        / "atom-index-map.json"
    )
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

    doscar_bytes = (
        b"1 1 1 0\n"
        b"header 2\n"
        b"header 3\n"
        b"header 4\n"
        b"header 5\n"
        b"1.0 -1.0 2 0.2\n"
        b"-1.0 1.0 0.0\n"
        b"1.0 2.0 1.0\n"
        b"1.0 -1.0 2 0.2\n"
        b"-1.0 1 2 3 4 5 6 7 8 9\n"
        b"1.0 2 3 4 5 6 7 8 9 10\n"
    )
    doscar_path = (
        tmp_path
        / "calculations"
        / str(calculation.id)
        / "attempt-1"
        / "DOSCAR"
    )
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

    store = ProjectStore(tmp_path)
    store.save(
        ProjectBundle(
            project=project,
            structure_snapshots=(snapshot,),
            method_fingerprints=(fingerprint,),
            calculations=(calculation,),
            execution_attempts=(attempt,),
            artifacts=(doscar_artifact, atom_map_artifact),
        )
    )
    return store, calculation, str(atom_uid)


def test_block6_application_e2e_materializes_reuses_reopens_and_blocks_drift(
    tmp_path: Path,
) -> None:
    store, calculation, atom_uid = _store_with_dos_source(tmp_path)
    service = ProjectElectronicAnalysisApplicationService(store)

    initial = service.catalog()
    assert initial["dos_sources"] == [
        pytest.approx(initial["dos_sources"][0], abs=0)  # type: ignore[index]
    ]
    source = initial["dos_sources"][0]  # type: ignore[index]
    assert source["calculation_id"] == str(calculation.id)
    assert source["materialization_ready"] is True
    assert source["materialized_analysis_id"] is None

    first = service.materialize_dos(calculation_id=calculation.id)
    assert first["reused"] is False
    bundle = store.open()
    dos_analysis = next(item for item in bundle.analyses if str(item.id) == first["analysis_id"])

    dos_view = service.analysis_view(analysis_id=dos_analysis.id)
    assert dos_view["analysis_type"] == "dos"
    assert dos_view["freshness"]["readiness"] == "satisfied"  # type: ignore[index]
    view = dos_view["view"]  # type: ignore[assignment]
    assert view["kind"] == "dos"  # type: ignore[index]
    assert view["energies_ev_native"] == [-1.0, 1.0]  # type: ignore[index]
    assert view["energies_ev_relative_to_fermi"] == pytest.approx([-1.2, 0.8])  # type: ignore[index]
    projected_uids = {
        row["atom_uid"]
        for row in view["series"]  # type: ignore[index]
        if row["scope"] == "atom"
    }
    assert projected_uids == {atom_uid}

    second = service.materialize_dos(calculation_id=calculation.id)
    assert second["reused"] is True
    assert second["analysis_id"] == first["analysis_id"]
    assert second["artifact_id"] == first["artifact_id"]

    descriptor = service.materialize_band_center(
        source_analysis_id=dos_analysis.id,
        kind=BandCenterKind.D_BAND,
        scope=ProjectionScope.ATOM,
        spin=BandCenterSpinMode.TOTAL,
        atom_uid=store.open().structure_snapshots[0].sites[0].atom_uid,
        element="C",
        energy_reference=BandCenterEnergyReference.VASP_NATIVE,
        window_lower_ev=-1.0,
        window_upper_ev=1.0,
    )
    assert descriptor["reused"] is False
    descriptor_analysis = next(
        item for item in store.open().analyses if str(item.id) == descriptor["analysis_id"]
    )
    descriptor_view = service.analysis_view(analysis_id=descriptor_analysis.id)
    assert descriptor_view["view"]["kind"] == "band_center"  # type: ignore[index]
    assert descriptor_view["view"]["selector"]["atom_uid"] == atom_uid  # type: ignore[index]

    descriptor_reuse = service.materialize_band_center(
        source_analysis_id=dos_analysis.id,
        kind=BandCenterKind.D_BAND,
        scope=ProjectionScope.ATOM,
        spin=BandCenterSpinMode.TOTAL,
        atom_uid=store.open().structure_snapshots[0].sites[0].atom_uid,
        element="C",
        energy_reference=BandCenterEnergyReference.VASP_NATIVE,
        window_lower_ev=-1.0,
        window_upper_ev=1.0,
    )
    assert descriptor_reuse["reused"] is True
    assert descriptor_reuse["analysis_id"] == descriptor["analysis_id"]

    doscar = next(
        item
        for item in store.open().artifacts
        if item.artifact_type is ArtifactType.DOSCAR
    )
    doscar_path = tmp_path / (doscar.local_path or "")
    doscar_path.write_bytes(doscar_path.read_bytes() + b"drift\n")

    drifted = service.catalog()
    dos_row = next(
        item for item in drifted["analyses"] if item["analysis_id"] == first["analysis_id"]  # type: ignore[index]
    )
    assert dos_row["freshness"]["scientific_state"] == "stale"
    assert dos_row["freshness"]["readiness"] == "blocked"

    with pytest.raises(ApplicationServiceError, match="local byte size changed"):
        service.materialize_dos(calculation_id=calculation.id)
    with pytest.raises(ApplicationServiceError, match="not fresh/satisfied"):
        service.materialize_band_center(
            source_analysis_id=dos_analysis.id,
            kind=BandCenterKind.D_BAND,
            scope=ProjectionScope.ATOM,
            spin=BandCenterSpinMode.TOTAL,
            atom_uid=store.open().structure_snapshots[0].sites[0].atom_uid,
            element="C",
            energy_reference=BandCenterEnergyReference.VASP_NATIVE,
            window_lower_ev=-1.0,
            window_upper_ev=1.0,
        )

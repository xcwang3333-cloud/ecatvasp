from __future__ import annotations

from hashlib import sha256

import pytest

from ecatvasp.domain import (
    Calculation,
    CalculationType,
    KPointPolicy,
    KPointPolicyKind,
    Lattice,
    MethodDefinition,
    MethodFingerprint,
    PotcarIdentity,
    Project,
    ProtocolDefinition,
    RecipeIdentity,
    StructureSite,
    StructureSnapshot,
    new_atom_uid,
)
from ecatvasp.provenance import (
    DependencyKind,
    DependencyRecord,
    ProvenanceRecord,
    scientific_hash,
)
from ecatvasp.storage import ProjectBundle, ProjectIntegrityError, ProjectStore


def _digest(label: str) -> str:
    return sha256(label.encode()).hexdigest()


def _minimal_project_with_provenance() -> ProjectBundle:
    project = Project(name="Storage provenance", slug="storage-provenance")
    snapshot = StructureSnapshot(
        lattice=Lattice(
            vectors=((8.0, 0.0, 0.0), (0.0, 8.0, 0.0), (0.0, 0.0, 15.0))
        ),
        sites=(StructureSite(new_atom_uid(), "C", (0.5, 0.5, 0.5)),),
        label="clean",
    )
    method = MethodFingerprint(
        method=MethodDefinition(
            xc_functional="PBE",
            potcar_family="PBE_54",
            potcars=(PotcarIdentity("C", "C", _digest("C-potcar")),),
        ),
        protocol=ProtocolDefinition(
            encut_ev=450.0,
            kpoints=KPointPolicy(KPointPolicyKind.GAMMA_ONLY),
            ediffg_ev_per_angstrom=-0.02,
        ),
        recipe=RecipeIdentity("WXC.VASP.SlabRelax"),
    )
    calculation = Calculation(
        project_id=project.id,
        calculation_type=CalculationType.RELAX,
        input_structure_snapshot_id=snapshot.id,
        recipe_id="WXC.VASP.SlabRelax",
        method_fingerprint_id=method.id,
    )
    provenance = ProvenanceRecord(
        subject_id=calculation.id,
        tool="ecatvasp",
        tool_version="0.1.0.dev0",
        parameters_hash=_digest("prepare-relax"),
        method_fingerprint_id=method.id,
    )
    dependencies = (
        DependencyRecord(
            upstream_id=snapshot.id,
            downstream_id=calculation.id,
            kind=DependencyKind.SCIENTIFIC,
            role="input_structure",
            recorded_hash=scientific_hash(snapshot),
        ),
        DependencyRecord(
            upstream_id=method.id,
            downstream_id=calculation.id,
            kind=DependencyKind.SCIENTIFIC,
            role="method_fingerprint",
            recorded_hash=scientific_hash(method),
        ),
    )
    return ProjectBundle(
        project=project,
        structure_snapshots=(snapshot,),
        method_fingerprints=(method,),
        calculations=(calculation,),
        provenance_records=(provenance,),
        dependency_records=dependencies,
    )


def test_provenance_and_dependencies_round_trip_through_project_store(tmp_path) -> None:
    bundle = _minimal_project_with_provenance()
    ProjectStore(tmp_path).save(bundle)

    reopened = ProjectStore(tmp_path).open()

    assert reopened.provenance_records == bundle.provenance_records
    assert reopened.dependency_records == bundle.dependency_records
    assert reopened.calculations == bundle.calculations
    assert reopened.method_fingerprints[0].instance_hash == (
        bundle.method_fingerprints[0].instance_hash
    )


def test_multiple_provenance_records_for_one_subject_round_trip(tmp_path) -> None:
    bundle = _minimal_project_with_provenance()
    calculation = bundle.calculations[0]
    method = bundle.method_fingerprints[0]
    parser_record = ProvenanceRecord(
        subject_id=calculation.id,
        tool="ecatvasp-parser",
        tool_version="0.1.0.dev0",
        parameters_hash=_digest("parse-result"),
        method_fingerprint_id=method.id,
    )
    with_history = ProjectBundle(
        project=bundle.project,
        structure_snapshots=bundle.structure_snapshots,
        method_fingerprints=bundle.method_fingerprints,
        calculations=bundle.calculations,
        provenance_records=(*bundle.provenance_records, parser_record),
        dependency_records=bundle.dependency_records,
    )

    ProjectStore(tmp_path).save(with_history)
    reopened = ProjectStore(tmp_path).open()

    assert reopened.provenance_records == with_history.provenance_records


def test_persisted_dependency_requires_downstream_provenance() -> None:
    bundle = _minimal_project_with_provenance()
    without_provenance = ProjectBundle(
        project=bundle.project,
        structure_snapshots=bundle.structure_snapshots,
        method_fingerprints=bundle.method_fingerprints,
        calculations=bundle.calculations,
        dependency_records=bundle.dependency_records,
    )

    with pytest.raises(ProjectIntegrityError, match="requires a ProvenanceRecord"):
        without_provenance.validate()

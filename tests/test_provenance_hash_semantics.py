from __future__ import annotations

from dataclasses import replace
from hashlib import sha256

from ecatvasp.domain import (
    Calculation,
    CalculationScientificStatus,
    CalculationType,
    Catalyst,
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
    StructureVariant,
    VariantType,
    new_atom_uid,
)
from ecatvasp.provenance import scientific_hash


def _digest(label: str) -> str:
    return sha256(label.encode()).hexdigest()


def _fixture_objects() -> tuple[StructureVariant, Calculation]:
    project = Project(name="Hash semantics", slug="hash-semantics")
    catalyst = Catalyst(project_id=project.id, name="Pb2-NC", slug="pb2-nc")
    snapshot = StructureSnapshot(
        lattice=Lattice(
            vectors=((8.0, 0.0, 0.0), (0.0, 8.0, 0.0), (0.0, 0.0, 15.0))
        ),
        sites=(StructureSite(new_atom_uid(), "C", (0.5, 0.5, 0.5)),),
    )
    variant = StructureVariant(
        catalyst_id=catalyst.id,
        name="opposite-side Pb2",
        variant_type=VariantType.SITE_TOPOLOGY,
        topology_tags=("opposite-side",),
        current_structure_snapshot_id=snapshot.id,
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
        status=CalculationScientificStatus.READY,
        slug="pb2-relax",
    )
    return variant, calculation


def test_scientific_hash_excludes_organizational_and_lifecycle_metadata() -> None:
    variant, calculation = _fixture_objects()

    renamed_variant = replace(variant, name="renamed for presentation")
    completed_calculation = replace(
        calculation,
        status=CalculationScientificStatus.CONVERGED,
        slug="renamed-slug",
    )

    assert scientific_hash(renamed_variant) == scientific_hash(variant)
    assert scientific_hash(completed_calculation) == scientific_hash(calculation)

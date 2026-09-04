from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from ecatvasp.domain import (
    ArtifactType,
    Calculation,
    CalculationType,
    ExecutionSettings,
    KPointPolicy,
    KPointPolicyKind,
    Lattice,
    MethodDefinition,
    MethodFingerprint,
    ParameterEntry,
    PotcarIdentity,
    ProtocolDefinition,
    RecipeIdentity,
    RetrievalPolicy,
    StructureOrigin,
    StructureSite,
    StructureSnapshot,
    StructureVariant,
    VariantType,
)
from ecatvasp.domain.ids import (
    new_artifact_id,
    new_atom_uid,
    new_catalyst_id,
    new_execution_attempt_id,
    new_project_id,
    new_structure_snapshot_id,
)
from ecatvasp.vasp import (
    RECIPE_SLAB_RELAX,
    ExecutionPlan,
    ExpectedOutput,
    PotcarResolutionEntry,
    PotcarResolutionRequest,
    StagingInput,
    StagingInputKind,
    VaspConvergenceEvidence,
    VaspResultArtifactIntake,
    VaspResultInputFile,
    VaspResultSource,
    VaspResultSourceRole,
    VaspRuntimeConstraints,
    VaspStructurePromotionError,
    VaspSystemContext,
    VaspSystemKind,
    promote_vasp_contcar_snapshot,
    reconstruct_vasp_contcar_snapshot,
    result_source_artifact_type,
)


@dataclass(frozen=True)
class _Case:
    root: Path
    calculation: Calculation
    fingerprint: MethodFingerprint
    plan: ExecutionPlan
    intake: VaspResultArtifactIntake
    input_snapshot: StructureSnapshot
    variant: StructureVariant
    contcar_path: Path


def _write(root: Path, relative: str, body: bytes) -> tuple[str, int]:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return hashlib.sha256(body).hexdigest(), len(body)


def _fingerprint() -> MethodFingerprint:
    return MethodFingerprint(
        method=MethodDefinition(
            xc_functional="PBE",
            potcar_family="PBE_54",
            potcars=(
                PotcarIdentity("H", "H", "1" * 64),
                PotcarIdentity("O", "O", "2" * 64),
            ),
            dispersion_model="NONE",
        ),
        protocol=ProtocolDefinition(
            encut_ev=450.0,
            kpoints=KPointPolicy(KPointPolicyKind.GAMMA_ONLY),
        ),
        recipe=RecipeIdentity(
            RECIPE_SLAB_RELAX,
            parameters=(ParameterEntry("NSW", 200),),
        ),
    )


def _poscar(lattice_scale: float = 10.0) -> bytes:
    return (
        "generated\n"
        "1.0\n"
        f"{lattice_scale:.6f} 0 0\n"
        f"0 {lattice_scale:.6f} 0\n"
        f"0 0 {lattice_scale:.6f}\n"
        "O H\n"
        "1 1\n"
        "Direct\n"
        "0.500000 0.500000 0.500000\n"
        "0.100000 0.100000 0.100000\n"
    ).encode()


def _contcar(*, symbols: str = "O H", counts: str = "1 1") -> bytes:
    return (
        "relaxed\n"
        "1.0\n"
        "11.000000 0 0\n"
        "0 12.000000 0\n"
        "0 0 13.000000\n"
        f"{symbols}\n"
        f"{counts}\n"
        "Direct\n"
        "0.520000 0.510000 0.500000\n"
        "0.120000 0.110000 0.100000\n"
    ).encode()


def _case(tmp_path: Path, *, contcar: bytes | None = None) -> _Case:
    fingerprint = _fingerprint()
    h_uid = new_atom_uid()
    o_uid = new_atom_uid()
    input_snapshot = StructureSnapshot(
        lattice=Lattice(
            vectors=(
                (10.0, 0.0, 0.0),
                (0.0, 10.0, 0.0),
                (0.0, 0.0, 10.0),
            )
        ),
        sites=(
            StructureSite(h_uid, "H", (0.1, 0.1, 0.1)),
            StructureSite(o_uid, "O", (0.5, 0.5, 0.5)),
        ),
        label="input",
        origin=StructureOrigin.BUILT,
    )
    calculation = Calculation(
        project_id=new_project_id(),
        calculation_type=CalculationType.RELAX,
        input_structure_snapshot_id=input_snapshot.id,
        recipe_id=RECIPE_SLAB_RELAX,
        method_fingerprint_id=fingerprint.id,
        slug="v05-contcar-test",
    )
    variant = StructureVariant(
        catalyst_id=new_catalyst_id(),
        name="candidate",
        variant_type=VariantType.GEOMETRY,
        current_structure_snapshot_id=input_snapshot.id,
    )

    poscar_body = _poscar()
    poscar_sha, poscar_size = _write(tmp_path, "inputs/POSCAR", poscar_body)
    atom_map_body = json.dumps(
        {
            "format": "ecatvasp-v03-atom-index-map",
            "version": 1,
            "structure_snapshot_id": str(input_snapshot.id),
            "structure_sha256": "e" * 64,
            "poscar_sha256": poscar_sha,
            "species_order": ["O", "H"],
            "species_counts": [1, 1],
            "entries": [
                {
                    "atom_uid": str(o_uid),
                    "element": "O",
                    "snapshot_index": 1,
                    "poscar_index": 0,
                    "vasp_ordinal": 1,
                    "selective_dynamics": None,
                },
                {
                    "atom_uid": str(h_uid),
                    "element": "H",
                    "snapshot_index": 0,
                    "poscar_index": 1,
                    "vasp_ordinal": 2,
                    "selective_dynamics": None,
                },
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    atom_map_sha, atom_map_size = _write(
        tmp_path,
        "inputs/atom-index-map.json",
        atom_map_body,
    )

    plan = ExecutionPlan(
        calculation_id=calculation.id,
        recipe_id=calculation.recipe_id,
        system_context=VaspSystemContext(VaspSystemKind.PERIODIC_3D),
        input_manifest_artifact_id=new_artifact_id(),
        input_manifest_sha256="a" * 64,
        preparation_hash="b" * 64,
        staging_inputs=(
            StagingInput(
                role="atom_index_map",
                kind=StagingInputKind.METADATA,
                artifact_id=new_artifact_id(),
                artifact_type=ArtifactType.DERIVED_DATASET,
                source_relative_path="inputs/atom-index-map.json",
                target_relative_path="atom-index-map.json",
                sha256=atom_map_sha,
                size_bytes=atom_map_size,
            ),
            StagingInput(
                role="poscar",
                kind=StagingInputKind.VASP_INPUT,
                artifact_id=new_artifact_id(),
                artifact_type=ArtifactType.POSCAR,
                source_relative_path="inputs/POSCAR",
                target_relative_path="POSCAR",
                sha256=poscar_sha,
                size_bytes=poscar_size,
            ),
        ),
        potcar_resolution=PotcarResolutionRequest(
            family="PBE_54",
            core_method_hash=fingerprint.core_method_hash,
            metadata_hash="d" * 64,
            entries=(
                PotcarResolutionEntry("H", "H", "1" * 64),
                PotcarResolutionEntry("O", "O", "2" * 64),
            ),
        ),
        expected_outputs=(
            ExpectedOutput(
                role="contcar",
                artifact_type=ArtifactType.CONTCAR,
                relative_path="CONTCAR",
                retrieval_policy=RetrievalPolicy.ALWAYS,
                required=True,
            ),
            ExpectedOutput(
                role="outcar",
                artifact_type=ArtifactType.OUTCAR,
                relative_path="OUTCAR",
                retrieval_policy=RetrievalPolicy.ALWAYS,
                required=True,
            ),
        ),
        runtime_constraints=VaspRuntimeConstraints(),
        execution_settings=ExecutionSettings(),
    )

    outcar_body = b"vasp.6.4.3\n"
    outcar_sha, outcar_size = _write(tmp_path, "outputs/OUTCAR", outcar_body)
    contcar_body = _contcar() if contcar is None else contcar
    contcar_sha, contcar_size = _write(tmp_path, "outputs/CONTCAR", contcar_body)
    files = (
        VaspResultInputFile(
            source=VaspResultSource(
                role=VaspResultSourceRole.OUTCAR,
                artifact_id=new_artifact_id(),
                artifact_type=result_source_artifact_type(VaspResultSourceRole.OUTCAR),
                sha256=outcar_sha,
            ),
            expected_output_path="OUTCAR",
            local_relative_path="outputs/OUTCAR",
            size_bytes=outcar_size,
            retrieval_policy=RetrievalPolicy.ALWAYS,
        ),
        VaspResultInputFile(
            source=VaspResultSource(
                role=VaspResultSourceRole.CONTCAR,
                artifact_id=new_artifact_id(),
                artifact_type=result_source_artifact_type(VaspResultSourceRole.CONTCAR),
                sha256=contcar_sha,
            ),
            expected_output_path="CONTCAR",
            local_relative_path="outputs/CONTCAR",
            size_bytes=contcar_size,
            retrieval_policy=RetrievalPolicy.ALWAYS,
        ),
    )
    intake = VaspResultArtifactIntake(
        calculation_id=calculation.id,
        calculation_type=calculation.calculation_type,
        recipe_id=calculation.recipe_id,
        attempt_id=new_execution_attempt_id(),
        attempt_number=1,
        plan_hash=plan.plan_hash,
        input_manifest_hash=plan.input_manifest_sha256,
        files=files,
    )
    return _Case(
        root=tmp_path,
        calculation=calculation,
        fingerprint=fingerprint,
        plan=plan,
        intake=intake,
        input_snapshot=input_snapshot,
        variant=variant,
        contcar_path=tmp_path / "outputs/CONTCAR",
    )


def _reconstruct(case: _Case):
    return reconstruct_vasp_contcar_snapshot(
        project_root=case.root,
        calculation=case.calculation,
        plan=case.plan,
        intake=case.intake,
        input_snapshot=case.input_snapshot,
    )


def _evidence(case: _Case, *, converged: bool) -> VaspConvergenceEvidence:
    return VaspConvergenceEvidence(
        calculation_id=case.calculation.id,
        intake_hash=case.intake.intake_hash,
        calculation_type=CalculationType.RELAX,
        recipe_id=RECIPE_SLAB_RELAX,
        termination_observed=True,
        final_toten_observed=True,
        electronic_ediff_reached=True,
        ionic_required_accuracy_reached=converged,
        electronic_step_limit=60,
        ionic_step_limit=200,
        ionic_steps=2 if converged else 200,
        final_electronic_steps=5,
        max_electronic_steps=5,
    )


def test_reconstruction_uses_contcar_lattice_and_atom_map_order(tmp_path: Path) -> None:
    case = _case(tmp_path)

    reconstruction = _reconstruct(case)

    snapshot = reconstruction.snapshot
    assert snapshot.parent_snapshot_id == case.input_snapshot.id
    assert snapshot.origin is StructureOrigin.RELAXED
    assert snapshot.lattice.vectors == (
        (11.0, 0.0, 0.0),
        (0.0, 12.0, 0.0),
        (0.0, 0.0, 13.0),
    )
    assert tuple(site.element for site in snapshot.sites) == ("O", "H")
    assert snapshot.sites[0].atom_uid == case.input_snapshot.sites[1].atom_uid
    assert snapshot.sites[1].atom_uid == case.input_snapshot.sites[0].atom_uid


def test_reconstruction_rejects_contcar_species_order_mismatch(tmp_path: Path) -> None:
    case = _case(tmp_path, contcar=_contcar(symbols="H O"))

    with pytest.raises(VaspStructurePromotionError, match="atom count/species/order"):
        _reconstruct(case)


def test_reconstruction_rejects_contcar_drift_after_intake(tmp_path: Path) -> None:
    case = _case(tmp_path)
    case.contcar_path.write_bytes(case.contcar_path.read_bytes() + b"drift\n")

    with pytest.raises(VaspStructurePromotionError, match="size changed after intake"):
        _reconstruct(case)


def test_explicit_promotion_requires_scientific_convergence(tmp_path: Path) -> None:
    case = _case(tmp_path)
    reconstruction = _reconstruct(case)

    with pytest.raises(VaspStructurePromotionError, match="scientifically converged"):
        promote_vasp_contcar_snapshot(
            variant=case.variant,
            calculation=case.calculation,
            fingerprint=case.fingerprint,
            evidence=_evidence(case, converged=False),
            input_snapshot=case.input_snapshot,
            reconstruction=reconstruction,
        )


def test_explicit_promotion_updates_only_variant_current_pointer(tmp_path: Path) -> None:
    case = _case(tmp_path)
    reconstruction = _reconstruct(case)

    result = promote_vasp_contcar_snapshot(
        variant=case.variant,
        calculation=case.calculation,
        fingerprint=case.fingerprint,
        evidence=_evidence(case, converged=True),
        input_snapshot=case.input_snapshot,
        reconstruction=reconstruction,
    )

    assert result.updated_variant.current_structure_snapshot_id == reconstruction.snapshot.id
    assert case.variant.current_structure_snapshot_id == case.input_snapshot.id
    assert result.snapshot.parent_snapshot_id == case.input_snapshot.id


def test_promotion_rejects_variant_that_advanced_after_calculation_started(tmp_path: Path) -> None:
    case = _case(tmp_path)
    reconstruction = _reconstruct(case)
    advanced = replace(
        case.variant,
        current_structure_snapshot_id=new_structure_snapshot_id(),
    )

    with pytest.raises(VaspStructurePromotionError, match="has moved"):
        promote_vasp_contcar_snapshot(
            variant=advanced,
            calculation=case.calculation,
            fingerprint=case.fingerprint,
            evidence=_evidence(case, converged=True),
            input_snapshot=case.input_snapshot,
            reconstruction=reconstruction,
        )

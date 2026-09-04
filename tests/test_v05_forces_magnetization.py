from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from ecatvasp.domain import (
    ArtifactType,
    Calculation,
    CalculationType,
    ExecutionSettings,
    KPointPolicy,
    KPointPolicyKind,
    MethodDefinition,
    MethodFingerprint,
    PotcarIdentity,
    ProtocolDefinition,
    RecipeIdentity,
    RetrievalPolicy,
    SpinTreatment,
)
from ecatvasp.domain.ids import (
    AtomUid,
    new_artifact_id,
    new_atom_uid,
    new_execution_attempt_id,
    new_project_id,
    new_structure_snapshot_id,
)
from ecatvasp.vasp import (
    RECIPE_GROUND_STATE_STATIC,
    ExpectedOutput,
    PotcarResolutionEntry,
    PotcarResolutionRequest,
    StagingInput,
    StagingInputKind,
    VaspCollinearMagnetization,
    VaspNoncollinearMagnetization,
    VaspObservableParseError,
    VaspResultArtifactIntake,
    VaspResultDocument,
    VaspResultInputFile,
    VaspResultSource,
    VaspResultSourceRole,
    VaspRuntimeConstraints,
    VaspSystemContext,
    VaspSystemKind,
    parse_vasp_energy_metadata,
    parse_vasp_forces_magnetization,
    result_source_artifact_type,
)
from ecatvasp.vasp.execution_plan import ExecutionPlan


@dataclass(frozen=True)
class _Case:
    root: Path
    calculation: Calculation
    fingerprint: MethodFingerprint
    plan: ExecutionPlan
    intake: VaspResultArtifactIntake
    result: VaspResultDocument
    atom_uids: tuple[AtomUid, ...]
    outcar_path: Path


def _write(root: Path, relative: str, body: bytes) -> tuple[str, int]:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return hashlib.sha256(body).hexdigest(), len(body)


def _fingerprint(spin: SpinTreatment) -> MethodFingerprint:
    return MethodFingerprint(
        method=MethodDefinition(
            xc_functional="PBE",
            potcar_family="PBE_54",
            potcars=(PotcarIdentity("C", "C", "c" * 64),),
            dispersion_model="NONE",
            spin_treatment=spin,
        ),
        protocol=ProtocolDefinition(
            encut_ev=450.0,
            kpoints=KPointPolicy(KPointPolicyKind.GAMMA_ONLY),
        ),
        recipe=RecipeIdentity(RECIPE_GROUND_STATE_STATIC),
    )


def _case(
    tmp_path: Path,
    *,
    spin: SpinTreatment,
    outcar: bytes,
    atom_map_poscar_sha: str | None = None,
) -> _Case:
    fingerprint = _fingerprint(spin)
    snapshot_id = new_structure_snapshot_id()
    calculation = Calculation(
        project_id=new_project_id(),
        calculation_type=CalculationType.STATIC,
        input_structure_snapshot_id=snapshot_id,
        recipe_id=RECIPE_GROUND_STATE_STATIC,
        method_fingerprint_id=fingerprint.id,
        slug="v05-observables-test",
    )
    atom_uids = (new_atom_uid(), new_atom_uid())
    poscar = b"ECatVASP\n1.0\nmock deterministic POSCAR\n"
    poscar_sha, poscar_size = _write(tmp_path, "inputs/POSCAR", poscar)
    bound_poscar_sha = poscar_sha if atom_map_poscar_sha is None else atom_map_poscar_sha
    atom_map = json.dumps(
        {
            "format": "ecatvasp-v03-atom-index-map",
            "version": 1,
            "structure_snapshot_id": str(snapshot_id),
            "structure_sha256": "e" * 64,
            "poscar_sha256": bound_poscar_sha,
            "species_order": ["C"],
            "species_counts": [2],
            "entries": [
                {
                    "atom_uid": str(atom_uids[0]),
                    "element": "C",
                    "snapshot_index": 0,
                    "poscar_index": 0,
                    "vasp_ordinal": 1,
                    "selective_dynamics": None,
                },
                {
                    "atom_uid": str(atom_uids[1]),
                    "element": "C",
                    "snapshot_index": 1,
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
        atom_map,
    )
    input_manifest_sha = "a" * 64
    plan = ExecutionPlan(
        calculation_id=calculation.id,
        recipe_id=calculation.recipe_id,
        system_context=VaspSystemContext(VaspSystemKind.PERIODIC_3D),
        input_manifest_artifact_id=new_artifact_id(),
        input_manifest_sha256=input_manifest_sha,
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
            entries=(PotcarResolutionEntry("C", "C", "f" * 64),),
        ),
        expected_outputs=(
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
    outcar_sha, outcar_size = _write(tmp_path, "outputs/OUTCAR", outcar)
    outcar_input = VaspResultInputFile(
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
    )
    intake = VaspResultArtifactIntake(
        calculation_id=calculation.id,
        calculation_type=calculation.calculation_type,
        recipe_id=calculation.recipe_id,
        attempt_id=new_execution_attempt_id(),
        attempt_number=1,
        plan_hash=plan.plan_hash,
        input_manifest_hash=input_manifest_sha,
        files=(outcar_input,),
    )
    result = parse_vasp_energy_metadata(project_root=tmp_path, intake=intake)
    return _Case(
        root=tmp_path,
        calculation=calculation,
        fingerprint=fingerprint,
        plan=plan,
        intake=intake,
        result=result,
        atom_uids=atom_uids,
        outcar_path=tmp_path / "outputs/OUTCAR",
    )


def _base_outcar() -> str:
    return (
        "vasp.6.4.3\n"
        " free energy TOTEN = -10.000000 eV\n"
        " aborting loop because EDIFF is reached\n"
    )


def _force_block(first: str, second: str | None) -> str:
    lines = [
        " POSITION                                       TOTAL-FORCE (eV/Angst)\n",
        " -------------------------------------------------------------------\n",
        f" 0.000 0.000 0.000 {first}\n",
    ]
    if second is not None:
        lines.append(f" 0.500 0.500 0.500 {second}\n")
    lines.append(" -------------------------------------------------------------------\n")
    return "".join(lines)


def _mag_table(component: str, first: float, second: float, total: float) -> str:
    return (
        f" magnetization ({component})\n"
        " # of ion       s       p       d       tot\n"
        " --------------------------------------------\n"
        f" 1 0.000 0.000 0.000 {first:.6f}\n"
        f" 2 0.000 0.000 0.000 {second:.6f}\n"
        " --------------------------------------------\n"
        f" tot 0.000 0.000 0.000 {total:.6f}\n"
    )


def _finish(text: str) -> bytes:
    return (text + " General timing and accounting informations for this job:\n").encode()


def _enrich(case: _Case) -> VaspResultDocument:
    return parse_vasp_forces_magnetization(
        project_root=case.root,
        calculation=case.calculation,
        fingerprint=case.fingerprint,
        plan=case.plan,
        intake=case.intake,
        result=case.result,
    )


def test_collinear_forces_and_magnetization_are_uid_addressed(tmp_path: Path) -> None:
    outcar = _finish(
        _base_outcar()
        + _force_block("0.100 0.000 0.000", "0.000 0.200 0.000")
        + " number of electron 8.000000 magnetization 1.500000\n"
        + _mag_table("x", 0.6, 0.7, 1.3)
    )
    case = _case(tmp_path, spin=SpinTreatment.COLLINEAR, outcar=outcar)

    result = _enrich(case)

    assert result.forces is not None
    assert tuple(item.atom_uid for item in result.forces.site_forces) == case.atom_uids
    assert result.forces.max_force_ev_per_angstrom == pytest.approx(0.2)
    assert isinstance(result.magnetization, VaspCollinearMagnetization)
    assert tuple(item.atom_uid for item in result.magnetization.site_moments) == case.atom_uids
    assert result.magnetization.projected_total_mu_b == pytest.approx(1.3)
    assert result.magnetization.cell_total_mu_b == pytest.approx(1.5)


def test_noncollinear_magnetization_preserves_vector_shape(tmp_path: Path) -> None:
    outcar = _finish(
        _base_outcar()
        + " number of electron 8.0 magnetization 0.10 0.20 0.30\n"
        + _mag_table("x", 0.1, 0.2, 0.3)
        + _mag_table("y", 0.4, 0.5, 0.9)
        + _mag_table("z", 0.6, 0.7, 1.3)
    )
    case = _case(tmp_path, spin=SpinTreatment.NONCOLLINEAR, outcar=outcar)

    result = _enrich(case)

    assert isinstance(result.magnetization, VaspNoncollinearMagnetization)
    first = result.magnetization.site_moments[0]
    assert first.atom_uid == case.atom_uids[0]
    assert first.projected_moment_mu_b == pytest.approx((0.1, 0.4, 0.6))
    assert result.magnetization.projected_total_mu_b == pytest.approx((0.3, 0.9, 1.3))
    assert result.magnetization.cell_total_mu_b == pytest.approx((0.1, 0.2, 0.3))


def test_unpolarized_zero_cell_magnetization_is_not_a_spin_dataset(tmp_path: Path) -> None:
    outcar = _finish(_base_outcar() + " number of electron 8.0 magnetization 0.000000\n")
    case = _case(tmp_path, spin=SpinTreatment.UNPOLARIZED, outcar=outcar)

    result = _enrich(case)

    assert result.magnetization is None


def test_atom_index_map_must_bind_exact_staged_poscar(tmp_path: Path) -> None:
    case = _case(
        tmp_path,
        spin=SpinTreatment.COLLINEAR,
        outcar=_finish(_base_outcar()),
        atom_map_poscar_sha="9" * 64,
    )

    with pytest.raises(VaspObservableParseError, match="exact staged POSCAR"):
        _enrich(case)


def test_outcar_drift_after_energy_parse_is_rejected(tmp_path: Path) -> None:
    case = _case(
        tmp_path,
        spin=SpinTreatment.COLLINEAR,
        outcar=_finish(_base_outcar()),
    )
    case.outcar_path.write_bytes(case.outcar_path.read_bytes() + b"drift\n")

    with pytest.raises(VaspObservableParseError, match="size changed"):
        _enrich(case)


def test_incomplete_final_force_block_does_not_reuse_previous_step(tmp_path: Path) -> None:
    outcar = _finish(
        _base_outcar()
        + _force_block("0.100 0.000 0.000", "0.000 0.200 0.000")
        + _force_block("0.300 0.000 0.000", None)
    )
    case = _case(tmp_path, spin=SpinTreatment.COLLINEAR, outcar=outcar)

    with pytest.raises(VaspObservableParseError, match="final OUTCAR force block is incomplete"):
        _enrich(case)


def test_noncollinear_final_projection_group_requires_xyz_together(tmp_path: Path) -> None:
    outcar = _finish(
        _base_outcar()
        + _mag_table("x", 0.1, 0.2, 0.3)
        + _mag_table("y", 0.4, 0.5, 0.9)
        + _mag_table("z", 0.6, 0.7, 1.3)
        + _mag_table("x", 0.2, 0.3, 0.5)
        + _mag_table("y", 0.5, 0.6, 1.1)
    )
    case = _case(tmp_path, spin=SpinTreatment.NONCOLLINEAR, outcar=outcar)

    with pytest.raises(VaspObservableParseError, match="requires x/y/z tables together"):
        _enrich(case)

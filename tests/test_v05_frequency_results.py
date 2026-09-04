from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from ecatvasp import domain, vasp
from ecatvasp.domain.ids import new_project_id
from ecatvasp.provenance import scientific_hash
from ecatvasp.vasp.execution_plan import ExecutionPlan


@dataclass(frozen=True)
class _Case:
    root: Path
    snapshot: domain.StructureSnapshot
    fingerprint: domain.MethodFingerprint
    calculation: domain.Calculation
    plan: ExecutionPlan
    intake: vasp.VaspResultArtifactIntake
    result: vasp.VaspResultDocument
    outcar_path: Path


def _write(root: Path, relative: str, body: bytes) -> tuple[str, int]:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return hashlib.sha256(body).hexdigest(), len(body)


def _mode_block(mode_count: int, atom_count: int, *, imaginary_index: int | None = None) -> str:
    lines = [
        " Eigenvectors and eigenvalues of the dynamical matrix\n",
        " ----------------------------------------------------\n",
    ]
    for mode_index in range(1, mode_count + 1):
        marker = "f/i" if mode_index == imaginary_index else "f"
        frequency = 10.0 + mode_index
        lines.append(
            f" {mode_index:3d} {marker} = {frequency:.6f} THz "
            f"{frequency * 6.283185:.6f} 2PiTHz "
            f"{frequency * 33.3564:.6f} cm-1 {frequency * 4.13567:.6f} meV\n"
        )
        lines.append(" X         Y         Z           dx          dy          dz\n")
        for atom_index in range(atom_count):
            scale = float((mode_index * 10) + atom_index + 1) / 100.0
            lines.append(
                f" 0.000000 0.000000 0.000000 {scale:.6f} "
                f"{scale + 0.01:.6f} {scale + 0.02:.6f}\n"
            )
    return "".join(lines)


def _case(
    tmp_path: Path,
    *,
    recipe_id: str,
    outcar: str,
    fingerprint_selected_index: int = 1,
    map_selected_index: int = 1,
) -> _Case:
    atom_uids = (domain.new_atom_uid(), domain.new_atom_uid())
    periodic = recipe_id != vasp.RECIPE_GAS_FREQUENCY
    snapshot = domain.StructureSnapshot(
        lattice=domain.Lattice(
            vectors=((8.0, 0.0, 0.0), (0.0, 8.0, 0.0), (0.0, 0.0, 16.0))
        ),
        sites=(
            domain.StructureSite(atom_uids[0], "C", (0.25, 0.25, 0.40)),
            domain.StructureSite(atom_uids[1], "C", (0.75, 0.75, 0.60)),
        ),
        periodic=(periodic, periodic, periodic),
    )
    selection = (
        vasp.FrequencySelection((atom_uids[fingerprint_selected_index],))
        if recipe_id == vasp.RECIPE_SELECTED_ATOM_FREQUENCY
        else None
    )
    recipe = domain.RecipeIdentity(
        recipe_id,
        parameters=vasp.frequency_recipe_parameters(potim_angstrom=0.015),
    )
    fingerprint = domain.MethodFingerprint(
        method=domain.MethodDefinition(
            xc_functional="PBE",
            potcar_family="PBE_54",
            potcars=(domain.PotcarIdentity("C", "C", "c" * 64),),
            dispersion_model="NONE",
        ),
        protocol=domain.ProtocolDefinition(
            encut_ev=450.0,
            kpoints=domain.KPointPolicy(domain.KPointPolicyKind.GAMMA_ONLY),
            ediff_ev=1e-8,
        ),
        recipe=recipe,
        input_digests=() if selection is None else (selection.input_digest,),
    )
    calculation_type = (
        domain.CalculationType.GAS_FREQUENCY
        if recipe_id == vasp.RECIPE_GAS_FREQUENCY
        else domain.CalculationType.FREQUENCY
    )
    calculation = domain.Calculation(
        project_id=new_project_id(),
        calculation_type=calculation_type,
        input_structure_snapshot_id=snapshot.id,
        recipe_id=recipe.recipe_id,
        method_fingerprint_id=fingerprint.id,
        slug="v05-frequency-results",
    )

    poscar = b"ECatVASP frequency POSCAR\n"
    poscar_sha, poscar_size = _write(tmp_path, "inputs/POSCAR", poscar)
    entries = []
    for index, (atom_uid, site) in enumerate(zip(atom_uids, snapshot.sites, strict=True)):
        flags: list[bool] | None
        if recipe_id == vasp.RECIPE_SELECTED_ATOM_FREQUENCY:
            selected = index == map_selected_index
            flags = [selected, selected, selected]
        else:
            flags = None
        entries.append(
            {
                "atom_uid": str(atom_uid),
                "element": site.element,
                "snapshot_index": index,
                "poscar_index": index,
                "vasp_ordinal": index + 1,
                "selective_dynamics": flags,
            }
        )
    atom_map = json.dumps(
        {
            "format": "ecatvasp-v03-atom-index-map",
            "version": 1,
            "structure_snapshot_id": str(snapshot.id),
            "structure_sha256": scientific_hash(snapshot),
            "poscar_sha256": poscar_sha,
            "species_order": ["C"],
            "species_counts": [2],
            "entries": entries,
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
    if recipe_id == vasp.RECIPE_GAS_FREQUENCY:
        system_context = vasp.VaspSystemContext(vasp.VaspSystemKind.MOLECULE_0D)
    else:
        system_context = vasp.VaspSystemContext(
            vasp.VaspSystemKind.SLAB_2D,
            vacuum_axis=vasp.LatticeAxis.C,
        )
    plan = ExecutionPlan(
        calculation_id=calculation.id,
        recipe_id=recipe.recipe_id,
        system_context=system_context,
        input_manifest_artifact_id=domain.new_artifact_id(),
        input_manifest_sha256=input_manifest_sha,
        preparation_hash="b" * 64,
        staging_inputs=(
            vasp.StagingInput(
                role="atom_index_map",
                kind=vasp.StagingInputKind.METADATA,
                artifact_id=domain.new_artifact_id(),
                artifact_type=domain.ArtifactType.DERIVED_DATASET,
                source_relative_path="inputs/atom-index-map.json",
                target_relative_path="atom-index-map.json",
                sha256=atom_map_sha,
                size_bytes=atom_map_size,
            ),
            vasp.StagingInput(
                role="poscar",
                kind=vasp.StagingInputKind.VASP_INPUT,
                artifact_id=domain.new_artifact_id(),
                artifact_type=domain.ArtifactType.POSCAR,
                source_relative_path="inputs/POSCAR",
                target_relative_path="POSCAR",
                sha256=poscar_sha,
                size_bytes=poscar_size,
            ),
        ),
        potcar_resolution=vasp.PotcarResolutionRequest(
            family="PBE_54",
            core_method_hash=fingerprint.core_method_hash,
            metadata_hash="d" * 64,
            entries=(vasp.PotcarResolutionEntry("C", "C", "f" * 64),),
        ),
        expected_outputs=(
            vasp.ExpectedOutput(
                role="outcar",
                artifact_type=domain.ArtifactType.OUTCAR,
                relative_path="OUTCAR",
                retrieval_policy=domain.RetrievalPolicy.ALWAYS,
                required=True,
            ),
        ),
        runtime_constraints=vasp.VaspRuntimeConstraints(),
        execution_settings=domain.ExecutionSettings(),
    )

    outcar_body = outcar.encode()
    outcar_sha, outcar_size = _write(tmp_path, "outputs/OUTCAR", outcar_body)
    outcar_input = vasp.VaspResultInputFile(
        source=vasp.VaspResultSource(
            role=vasp.VaspResultSourceRole.OUTCAR,
            artifact_id=domain.new_artifact_id(),
            artifact_type=domain.ArtifactType.OUTCAR,
            sha256=outcar_sha,
        ),
        expected_output_path="OUTCAR",
        local_relative_path="outputs/OUTCAR",
        size_bytes=outcar_size,
        retrieval_policy=domain.RetrievalPolicy.ALWAYS,
    )
    intake = vasp.VaspResultArtifactIntake(
        calculation_id=calculation.id,
        calculation_type=calculation.calculation_type,
        recipe_id=calculation.recipe_id,
        attempt_id=domain.new_execution_attempt_id(),
        attempt_number=1,
        plan_hash=plan.plan_hash,
        input_manifest_hash=input_manifest_sha,
        files=(outcar_input,),
    )
    result = vasp.VaspResultDocument(
        calculation_type=calculation.calculation_type,
        sources=intake.sources,
    )
    return _Case(
        root=tmp_path,
        snapshot=snapshot,
        fingerprint=fingerprint,
        calculation=calculation,
        plan=plan,
        intake=intake,
        result=result,
        outcar_path=tmp_path / "outputs/OUTCAR",
    )


def _parse(case: _Case) -> vasp.VaspResultDocument:
    return vasp.parse_vasp_frequency_results(
        project_root=case.root,
        calculation=case.calculation,
        fingerprint=case.fingerprint,
        plan=case.plan,
        intake=case.intake,
        input_snapshot=case.snapshot,
        result=case.result,
    )


def test_selected_frequency_parses_real_imaginary_modes_and_uid_vectors(tmp_path: Path) -> None:
    case = _case(
        tmp_path,
        recipe_id=vasp.RECIPE_SELECTED_ATOM_FREQUENCY,
        outcar=_mode_block(3, 2, imaginary_index=2),
    )
    parsed = _parse(case)

    assert parsed.frequencies is not None
    assert parsed.frequencies.degrees_of_freedom == 3
    assert parsed.frequencies.displaced_atom_uids == (case.snapshot.sites[1].atom_uid,)
    assert parsed.frequencies.imaginary_mode_count == 1
    assert parsed.frequencies.modes[1].kind is vasp.VaspFrequencyModeKind.IMAGINARY
    assert tuple(
        vector.atom_uid for vector in parsed.frequencies.modes[0].eigenvectors
    ) == tuple(site.atom_uid for site in case.snapshot.sites)


def test_full_frequency_requires_complete_three_n_mode_set(tmp_path: Path) -> None:
    case = _case(
        tmp_path,
        recipe_id=vasp.RECIPE_FULL_FREQUENCY,
        outcar=_mode_block(6, 2),
    )
    parsed = _parse(case)

    assert parsed.frequencies is not None
    assert parsed.frequencies.degrees_of_freedom == 6
    assert parsed.frequencies.imaginary_mode_count == 0


def test_gas_frequency_uses_all_atoms_as_frequency_degrees_of_freedom(tmp_path: Path) -> None:
    case = _case(
        tmp_path,
        recipe_id=vasp.RECIPE_GAS_FREQUENCY,
        outcar=_mode_block(6, 2),
    )
    parsed = _parse(case)

    assert parsed.frequencies is not None
    assert parsed.frequencies.displaced_atom_uids == tuple(
        site.atom_uid for site in case.snapshot.sites
    )
    assert parsed.frequencies.degrees_of_freedom == 6


def test_frequency_parser_ignores_optional_sqrt_mass_duplicate_block(tmp_path: Path) -> None:
    standard = _mode_block(3, 2, imaginary_index=3)
    divided = (
        " Eigenvectors after division by SQRT(mass)\n"
        + _mode_block(3, 2, imaginary_index=3)
    )
    case = _case(
        tmp_path,
        recipe_id=vasp.RECIPE_SELECTED_ATOM_FREQUENCY,
        outcar=standard + divided,
    )
    parsed = _parse(case)

    assert parsed.frequencies is not None
    assert len(parsed.frequencies.modes) == 3
    assert parsed.frequencies.imaginary_mode_count == 1


def test_frequency_parser_rejects_incomplete_mode_set(tmp_path: Path) -> None:
    case = _case(
        tmp_path,
        recipe_id=vasp.RECIPE_SELECTED_ATOM_FREQUENCY,
        outcar=_mode_block(2, 2),
    )

    with pytest.raises(ValueError, match="mode count"):
        _parse(case)


def test_selected_frequency_rejects_atom_map_selection_not_bound_to_fingerprint(
    tmp_path: Path,
) -> None:
    case = _case(
        tmp_path,
        recipe_id=vasp.RECIPE_SELECTED_ATOM_FREQUENCY,
        outcar=_mode_block(3, 2),
        fingerprint_selected_index=1,
        map_selected_index=0,
    )

    with pytest.raises(vasp.VaspFrequencyResultError, match="fingerprint"):
        _parse(case)


def test_frequency_parser_rejects_outcar_drift_after_intake(tmp_path: Path) -> None:
    case = _case(
        tmp_path,
        recipe_id=vasp.RECIPE_SELECTED_ATOM_FREQUENCY,
        outcar=_mode_block(3, 2),
    )
    case.outcar_path.write_text(case.outcar_path.read_text() + "drift\n")

    with pytest.raises(vasp.VaspFrequencyResultError, match="size changed"):
        _parse(case)


def test_frequency_parser_rejects_multiple_canonical_dynamical_matrix_blocks(
    tmp_path: Path,
) -> None:
    case = _case(
        tmp_path,
        recipe_id=vasp.RECIPE_SELECTED_ATOM_FREQUENCY,
        outcar=_mode_block(3, 2) + _mode_block(3, 2),
    )

    with pytest.raises(vasp.VaspFrequencyResultError, match="multiple canonical"):
        _parse(case)

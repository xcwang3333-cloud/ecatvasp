from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from ecatvasp import domain, vasp


@dataclass(frozen=True)
class _Case:
    project_root: Path
    snapshot: domain.StructureSnapshot
    fingerprint: domain.MethodFingerprint
    calculation: domain.Calculation
    context: vasp.VaspSystemContext
    library: vasp.LocalPotcarLibrary
    lock: vasp.ProjectNumericalLock
    encut_evidence: vasp.EncCutValidationEvidence
    kpoint_evidence: vasp.KPointValidationEvidence | None


def _write_potcar(
    root: Path,
    *,
    symbol: str,
    zval: float,
    enmax_ev: float,
) -> str:
    text = (
        f"TITEL = PAW_PBE {symbol}\n"
        f"ZVAL = {zval:.6f}\n"
        f"ENMAX = {enmax_ev:.6f}; ENMIN = {enmax_ev * 0.75:.6f}\n"
    )
    path = root / symbol / "POTCAR"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _case(
    tmp_path: Path,
    *,
    recipe_id: str,
    calculation_type: domain.CalculationType,
    molecule: bool,
) -> _Case:
    project = domain.Project(name="Block 7", slug=f"block-7-{recipe_id.split('.')[-1].lower()}")
    project_root = tmp_path / "project"
    potcar_root = tmp_path / "licensed-potcars"

    if molecule:
        h_hash = _write_potcar(potcar_root, symbol="H", zval=1.0, enmax_ev=350.0)
        snapshot = domain.StructureSnapshot(
            lattice=domain.Lattice(
                vectors=((15.0, 0.0, 0.0), (0.0, 15.0, 0.0), (0.0, 0.0, 15.0))
            ),
            sites=(
                domain.StructureSite(domain.new_atom_uid(), "H", (0.48, 0.5, 0.5)),
                domain.StructureSite(domain.new_atom_uid(), "H", (0.52, 0.5, 0.5)),
            ),
            periodic=(False, False, False),
        )
        method = domain.MethodDefinition(
            xc_functional="PBE",
            potcar_family="PBE_54",
            potcars=(domain.PotcarIdentity("H", "H", h_hash),),
            dispersion_model="NONE",
            spin_treatment=domain.SpinTreatment.UNPOLARIZED,
        )
        context = vasp.VaspSystemContext(vasp.VaspSystemKind.MOLECULE_0D)
        policy = domain.KPointPolicy(domain.KPointPolicyKind.GAMMA_ONLY)
        protocol_extras = (
            *vasp.ecat_standard_protocol_parameters(),
            domain.ParameterEntry(vasp.ECATVASP_KPOINT_CENTERING, "gamma"),
        )
    else:
        c_hash = _write_potcar(potcar_root, symbol="C", zval=4.0, enmax_ev=400.0)
        o_hash = _write_potcar(potcar_root, symbol="O", zval=6.0, enmax_ev=420.0)
        snapshot = domain.StructureSnapshot(
            lattice=domain.Lattice(
                vectors=((4.0, 0.0, 0.0), (0.0, 4.0, 0.0), (0.0, 0.0, 24.0))
            ),
            sites=(
                domain.StructureSite(domain.new_atom_uid(), "C", (0.0, 0.0, 0.45)),
                domain.StructureSite(domain.new_atom_uid(), "O", (0.5, 0.5, 0.55)),
                domain.StructureSite(domain.new_atom_uid(), "C", (0.5, 0.0, 0.45)),
            ),
            periodic=(True, True, False),
        )
        method = domain.MethodDefinition(
            xc_functional="PBE",
            potcar_family="PBE_54",
            potcars=(
                domain.PotcarIdentity("C", "C", c_hash),
                domain.PotcarIdentity("O", "O", o_hash),
            ),
            dispersion_model="NONE",
            spin_treatment=domain.SpinTreatment.UNPOLARIZED,
        )
        context = vasp.VaspSystemContext(
            vasp.VaspSystemKind.SLAB_2D,
            vacuum_axis=vasp.LatticeAxis.C,
        )
        policy = domain.KPointPolicy(
            domain.KPointPolicyKind.EXPLICIT_MESH,
            mesh=(3, 3, 1),
        )
        protocol_extras = (
            *vasp.ecat_standard_protocol_parameters(vacuum_axis="c"),
            domain.ParameterEntry(vasp.ECATVASP_KPOINT_CENTERING, "gamma"),
        )

    protocol = domain.ProtocolDefinition(
        encut_ev=450.0,
        kpoints=policy,
        precision="Accurate",
        ediff_ev=1e-6,
        ediffg_ev_per_angstrom=-0.02,
        ismear=0,
        sigma_ev=0.05,
        dipole_policy=domain.DipolePolicy.AUTO,
        lreal=False,
        extra_parameters=protocol_extras,
    )
    recipe = domain.RecipeIdentity(recipe_id)
    fingerprint = domain.MethodFingerprint(method=method, protocol=protocol, recipe=recipe)
    calculation = domain.Calculation(
        project_id=project.id,
        calculation_type=calculation_type,
        input_structure_snapshot_id=snapshot.id,
        recipe_id=recipe.recipe_id,
        method_fingerprint_id=fingerprint.id,
    )

    prepared_poscar = vasp.prepare_poscar(snapshot)
    prepared_kpoints = vasp.prepare_kpoints(
        snapshot,
        policy=policy,
        system_context=context,
        centering=vasp.KPointCentering.GAMMA,
    )
    library = vasp.LocalPotcarLibrary("PBE_54", potcar_root)
    resolved = library.resolve(prepared_poscar=prepared_poscar, method=method)

    encut_hash = "c" * 64
    kpoint_hash = None if molecule else "d" * 64
    encut_evidence = vasp.EncCutValidationEvidence(
        core_method_hash=fingerprint.core_method_hash,
        potcar_spec_hash=resolved.spec.metadata_hash,
        tested_encuts_ev=(420.0, 450.0, 500.0),
        selected_encut_ev=450.0,
        analysis_hash=encut_hash,
    )
    kpoint_evidence = (
        None
        if molecule
        else vasp.KPointValidationEvidence(
            core_method_hash=fingerprint.core_method_hash,
            system_kind=context.kind,
            tested_plan_hashes=(prepared_kpoints.identity_hash,),
            selected_plan_hash=prepared_kpoints.identity_hash,
            analysis_hash=kpoint_hash,
        )
    )
    lock = vasp.ProjectNumericalLock(
        project_id=project.id,
        system_kind=context.kind,
        core_method_hash=fingerprint.core_method_hash,
        encut_ev=protocol.encut_ev,
        encut_validation_hash=encut_hash,
        kpoints=protocol.kpoints,
        kpoints_validation_hash=kpoint_hash,
    )
    return _Case(
        project_root=project_root,
        snapshot=snapshot,
        fingerprint=fingerprint,
        calculation=calculation,
        context=context,
        library=library,
        lock=lock,
        encut_evidence=encut_evidence,
        kpoint_evidence=kpoint_evidence,
    )


@pytest.mark.parametrize(
    ("recipe_id", "calculation_type", "molecule"),
    (
        (vasp.RECIPE_SLAB_RELAX, domain.CalculationType.RELAX, False),
        (vasp.RECIPE_ADSORBATE_RELAX, domain.CalculationType.RELAX, False),
        (vasp.RECIPE_GROUND_STATE_STATIC, domain.CalculationType.STATIC, False),
        (vasp.RECIPE_GAS_RELAX, domain.CalculationType.GAS_RELAX, True),
        (vasp.RECIPE_GROUND_STATE_STATIC, domain.CalculationType.STATIC, True),
    ),
)
def test_core_pipeline_materializes_supported_production_recipes(
    tmp_path: Path,
    recipe_id: str,
    calculation_type: domain.CalculationType,
    molecule: bool,
) -> None:
    case = _case(
        tmp_path,
        recipe_id=recipe_id,
        calculation_type=calculation_type,
        molecule=molecule,
    )
    result = vasp.prepare_core_calculation_inputs(
        project_root=case.project_root,
        calculation=case.calculation,
        snapshot=case.snapshot,
        fingerprint=case.fingerprint,
        system_context=case.context,
        potcar_library=case.library,
        project_lock=case.lock,
        encut_evidence=case.encut_evidence,
        kpoint_evidence=case.kpoint_evidence,
    )

    assert result.calculation_id == case.calculation.id
    assert result.recipe_id == recipe_id
    assert result.prepared_incar.recipe_id == recipe_id
    assert result.prepared_kpoints.system_context == case.context
    assert all(path.is_file() for path in result.resolved_potcars.ordered_paths)
    assert all(
        case.project_root not in path.parents
        for path in result.resolved_potcars.ordered_paths
    )

    input_dir = case.project_root / result.materialized.input_directory
    assert not (input_dir / "POTCAR").exists()
    assert (input_dir / "POTCAR.spec").is_file()
    assert (input_dir / "input-manifest.json").is_file()
    payload = json.loads((input_dir / "input-manifest.json").read_text(encoding="utf-8"))
    assert payload["recipe"]["id"] == recipe_id
    assert payload["system_context"]["kind"] == case.context.kind.value

    if molecule:
        assert result.prepared_kpoints.mesh == (1, 1, 1)
    else:
        assert result.prepared_kpoints.mesh == (3, 3, 1)

    parameters = {item.name: item.value for item in result.prepared_incar.parameters}
    if calculation_type is domain.CalculationType.STATIC:
        assert parameters["IBRION"] == -1
        assert parameters["NSW"] == 0
    else:
        assert parameters["IBRION"] == 2
        assert parameters["NSW"] == 200


def test_solid_core_pipeline_requires_matching_kpoint_evidence(tmp_path: Path) -> None:
    case = _case(
        tmp_path,
        recipe_id=vasp.RECIPE_SLAB_RELAX,
        calculation_type=domain.CalculationType.RELAX,
        molecule=False,
    )
    with pytest.raises(
        vasp.KPointPreparationError,
        match="solid production k-point lock requires convergence evidence",
    ):
        vasp.prepare_core_calculation_inputs(
            project_root=case.project_root,
            calculation=case.calculation,
            snapshot=case.snapshot,
            fingerprint=case.fingerprint,
            system_context=case.context,
            potcar_library=case.library,
            project_lock=case.lock,
            encut_evidence=case.encut_evidence,
            kpoint_evidence=None,
        )


def test_core_pipeline_rejects_non_block7_recipe_before_materialization(tmp_path: Path) -> None:
    base = _case(
        tmp_path,
        recipe_id=vasp.RECIPE_SLAB_RELAX,
        calculation_type=domain.CalculationType.RELAX,
        molecule=False,
    )
    recipe = domain.RecipeIdentity(vasp.RECIPE_SELECTED_ATOM_FREQUENCY)
    fingerprint = domain.MethodFingerprint(
        method=base.fingerprint.method,
        protocol=base.fingerprint.protocol,
        recipe=recipe,
    )
    calculation = domain.Calculation(
        project_id=base.calculation.project_id,
        calculation_type=domain.CalculationType.FREQUENCY,
        input_structure_snapshot_id=base.snapshot.id,
        recipe_id=recipe.recipe_id,
        method_fingerprint_id=fingerprint.id,
    )

    with pytest.raises(
        vasp.CoreInputPipelineError,
        match=r"outside the v0.3 Block 7 core pipeline",
    ):
        vasp.prepare_core_calculation_inputs(
            project_root=base.project_root,
            calculation=calculation,
            snapshot=base.snapshot,
            fingerprint=fingerprint,
            system_context=base.context,
            potcar_library=base.library,
            project_lock=base.lock,
            encut_evidence=base.encut_evidence,
            kpoint_evidence=base.kpoint_evidence,
        )

    assert not base.project_root.exists()


def test_core_pipeline_rejects_calculation_fingerprint_identity_mismatch(tmp_path: Path) -> None:
    case = _case(
        tmp_path,
        recipe_id=vasp.RECIPE_GAS_RELAX,
        calculation_type=domain.CalculationType.GAS_RELAX,
        molecule=True,
    )
    foreign_fingerprint = domain.MethodFingerprint(
        method=case.fingerprint.method,
        protocol=case.fingerprint.protocol,
        recipe=case.fingerprint.recipe,
    )

    with pytest.raises(
        vasp.CoreInputPipelineError,
        match="MethodFingerprint id does not match",
    ):
        vasp.prepare_core_calculation_inputs(
            project_root=case.project_root,
            calculation=case.calculation,
            snapshot=case.snapshot,
            fingerprint=foreign_fingerprint,
            system_context=case.context,
            potcar_library=case.library,
            project_lock=case.lock,
            encut_evidence=case.encut_evidence,
            kpoint_evidence=None,
        )

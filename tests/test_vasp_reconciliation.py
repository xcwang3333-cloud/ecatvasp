from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import pytest

from ecatvasp import domain, vasp


@dataclass(frozen=True)
class _RoundtripCase:
    project_root: Path
    snapshot: domain.StructureSnapshot
    fingerprint: domain.MethodFingerprint
    calculation: domain.Calculation
    context: vasp.VaspSystemContext
    library: vasp.LocalPotcarLibrary
    lock: vasp.ProjectNumericalLock
    encut_evidence: vasp.EncCutValidationEvidence
    kpoint_evidence: vasp.KPointValidationEvidence


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


def _roundtrip_case(tmp_path: Path) -> _RoundtripCase:
    project = domain.Project(name="Roundtrip", slug="roundtrip")
    project_root = tmp_path / "project"
    potcar_root = tmp_path / "licensed-potcars"
    c_hash = _write_potcar(
        potcar_root,
        symbol="C",
        zval=4.0,
        enmax_ev=400.0,
    )
    o_hash = _write_potcar(
        potcar_root,
        symbol="O",
        zval=6.0,
        enmax_ev=420.0,
    )
    snapshot = domain.StructureSnapshot(
        lattice=domain.Lattice(
            vectors=((4.0, 0.0, 0.0), (0.0, 4.0, 0.0), (0.0, 0.0, 24.0))
        ),
        sites=(
            domain.StructureSite(
                domain.new_atom_uid(),
                "C",
                (0.0, 0.0, 0.45),
            ),
            domain.StructureSite(
                domain.new_atom_uid(),
                "O",
                (0.5, 0.5, 0.55),
            ),
            domain.StructureSite(
                domain.new_atom_uid(),
                "C",
                (0.5, 0.0, 0.45),
            ),
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
    kpoints = domain.KPointPolicy(
        domain.KPointPolicyKind.EXPLICIT_MESH,
        mesh=(3, 3, 1),
    )
    protocol = domain.ProtocolDefinition(
        encut_ev=450.0,
        kpoints=kpoints,
        precision="Accurate",
        ediff_ev=1e-6,
        ediffg_ev_per_angstrom=-0.02,
        ismear=0,
        sigma_ev=0.05,
        dipole_policy=domain.DipolePolicy.AUTO,
        lreal=False,
        extra_parameters=(
            *vasp.ecat_standard_protocol_parameters(vacuum_axis="c"),
            domain.ParameterEntry(vasp.ECATVASP_KPOINT_CENTERING, "gamma"),
        ),
    )
    recipe = domain.RecipeIdentity(vasp.RECIPE_GROUND_STATE_STATIC)
    fingerprint = domain.MethodFingerprint(
        method=method,
        protocol=protocol,
        recipe=recipe,
    )
    calculation = domain.Calculation(
        project_id=project.id,
        calculation_type=domain.CalculationType.STATIC,
        input_structure_snapshot_id=snapshot.id,
        recipe_id=recipe.recipe_id,
        method_fingerprint_id=fingerprint.id,
    )
    prepared_poscar = vasp.prepare_poscar(snapshot)
    prepared_kpoints = vasp.prepare_kpoints(
        snapshot,
        policy=kpoints,
        system_context=context,
        centering=vasp.KPointCentering.GAMMA,
    )
    library = vasp.LocalPotcarLibrary("PBE_54", potcar_root)
    resolved = library.resolve(
        prepared_poscar=prepared_poscar,
        method=method,
    )
    encut_hash = "a" * 64
    kpoint_hash = "b" * 64
    encut_evidence = vasp.EncCutValidationEvidence(
        core_method_hash=fingerprint.core_method_hash,
        potcar_spec_hash=resolved.spec.metadata_hash,
        tested_encuts_ev=(420.0, 450.0, 500.0),
        selected_encut_ev=450.0,
        analysis_hash=encut_hash,
    )
    kpoint_evidence = vasp.KPointValidationEvidence(
        core_method_hash=fingerprint.core_method_hash,
        system_kind=context.kind,
        tested_plan_hashes=(prepared_kpoints.identity_hash,),
        selected_plan_hash=prepared_kpoints.identity_hash,
        analysis_hash=kpoint_hash,
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
    return _RoundtripCase(
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


def _materialize(case: _RoundtripCase) -> Path:
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
    return case.project_root / result.materialized.input_directory


def test_fail_closed_matrix_covers_every_stable_code() -> None:
    assert set(vasp.VASP_FAIL_CLOSED_RULES) == set(vasp.VaspFailClosedCode)
    assert all(rule.code is code for code, rule in vasp.VASP_FAIL_CLOSED_RULES.items())


def test_generated_core_inputs_roundtrip_through_reconciliation(tmp_path: Path) -> None:
    case = _roundtrip_case(tmp_path)
    input_dir = _materialize(case)

    reconciled = vasp.reconcile_generated_input_directory(
        folder=input_dir,
        calculation=case.calculation,
        snapshot=case.snapshot,
        fingerprint=case.fingerprint,
        system_context=case.context,
        project_lock=case.lock,
    )

    assert reconciled.calculation is case.calculation
    assert reconciled.snapshot is case.snapshot
    assert reconciled.fingerprint is case.fingerprint
    assert reconciled.prepared_poscar.text == (input_dir / "POSCAR").read_text()
    assert reconciled.prepared_incar.text == (input_dir / "INCAR").read_text()
    assert reconciled.prepared_kpoints.text == (input_dir / "KPOINTS").read_text()
    assert reconciled.preparation_hash


def test_reconciliation_rejects_tampered_generated_file(tmp_path: Path) -> None:
    case = _roundtrip_case(tmp_path)
    input_dir = _materialize(case)
    incar_path = input_dir / "INCAR"
    incar_path.write_text(incar_path.read_text() + "LORBIT = 11\n")

    with pytest.raises(vasp.GeneratedInputReconciliationError) as exc_info:
        vasp.reconcile_generated_input_directory(
            folder=input_dir,
            calculation=case.calculation,
            snapshot=case.snapshot,
            fingerprint=case.fingerprint,
            system_context=case.context,
            project_lock=case.lock,
        )

    assert exc_info.value.code is vasp.VaspFailClosedCode.INPUT_FILE_SIZE_MISMATCH


def test_reconciliation_rejects_wrong_snapshot_identity(tmp_path: Path) -> None:
    case = _roundtrip_case(tmp_path)
    input_dir = _materialize(case)
    wrong_snapshot = domain.StructureSnapshot(
        lattice=case.snapshot.lattice,
        sites=case.snapshot.sites,
        periodic=case.snapshot.periodic,
    )

    with pytest.raises(vasp.GeneratedInputReconciliationError) as exc_info:
        vasp.reconcile_generated_input_directory(
            folder=input_dir,
            calculation=case.calculation,
            snapshot=wrong_snapshot,
            fingerprint=case.fingerprint,
            system_context=case.context,
            project_lock=case.lock,
        )

    assert exc_info.value.code is vasp.VaspFailClosedCode.SNAPSHOT_FINGERPRINT_MISMATCH


def test_reconciliation_requires_manifest(tmp_path: Path) -> None:
    case = _roundtrip_case(tmp_path)
    input_dir = _materialize(case)
    (input_dir / "input-manifest.json").unlink()

    with pytest.raises(vasp.GeneratedInputReconciliationError) as exc_info:
        vasp.reconcile_generated_input_directory(
            folder=input_dir,
            calculation=case.calculation,
            snapshot=case.snapshot,
            fingerprint=case.fingerprint,
            system_context=case.context,
            project_lock=case.lock,
        )

    assert exc_info.value.code is vasp.VaspFailClosedCode.INPUT_MANIFEST_MISSING

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import pytest

from ecatvasp import domain, vasp


@dataclass(frozen=True)
class _FrequencyCase:
    project_root: Path
    snapshot: domain.StructureSnapshot
    fingerprint: domain.MethodFingerprint
    calculation: domain.Calculation
    context: vasp.VaspSystemContext
    library: vasp.LocalPotcarLibrary
    lock: vasp.ProjectNumericalLock
    encut_evidence: vasp.EncCutValidationEvidence
    kpoint_evidence: vasp.KPointValidationEvidence | None
    selection: vasp.FrequencySelection | None


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


def _frequency_case(
    tmp_path: Path,
    *,
    recipe_id: str,
) -> _FrequencyCase:
    project = domain.Project(name="Frequency", slug=f"frequency-{recipe_id.split('.')[-1].lower()}")
    project_root = tmp_path / "project"
    potcar_root = tmp_path / "licensed-potcars"

    if recipe_id == vasp.RECIPE_GAS_FREQUENCY:
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
        kpoints = domain.KPointPolicy(domain.KPointPolicyKind.GAMMA_ONLY)
        protocol_extras = (
            *vasp.ecat_standard_protocol_parameters(),
            domain.ParameterEntry(vasp.ECATVASP_KPOINT_CENTERING, "gamma"),
        )
        calculation_type = domain.CalculationType.GAS_FREQUENCY
        selection = None
    else:
        c_hash = _write_potcar(potcar_root, symbol="C", zval=4.0, enmax_ev=400.0)
        o_hash = _write_potcar(potcar_root, symbol="O", zval=6.0, enmax_ev=420.0)
        selected_uid = domain.new_atom_uid()
        snapshot = domain.StructureSnapshot(
            lattice=domain.Lattice(
                vectors=((4.0, 0.0, 0.0), (0.0, 4.0, 0.0), (0.0, 0.0, 24.0))
            ),
            sites=(
                domain.StructureSite(domain.new_atom_uid(), "C", (0.0, 0.0, 0.45)),
                domain.StructureSite(selected_uid, "O", (0.5, 0.5, 0.55)),
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
        kpoints = domain.KPointPolicy(
            domain.KPointPolicyKind.EXPLICIT_MESH,
            mesh=(3, 3, 1),
        )
        protocol_extras = (
            *vasp.ecat_standard_protocol_parameters(vacuum_axis="c"),
            domain.ParameterEntry(vasp.ECATVASP_KPOINT_CENTERING, "gamma"),
        )
        calculation_type = domain.CalculationType.FREQUENCY
        selection = (
            vasp.FrequencySelection((selected_uid,))
            if recipe_id == vasp.RECIPE_SELECTED_ATOM_FREQUENCY
            else None
        )

    protocol = domain.ProtocolDefinition(
        encut_ev=450.0,
        kpoints=kpoints,
        precision="Accurate",
        ediff_ev=1e-8,
        ediffg_ev_per_angstrom=-0.02,
        ismear=0,
        sigma_ev=0.05,
        dipole_policy=domain.DipolePolicy.AUTO,
        lreal=False,
        extra_parameters=protocol_extras,
    )
    recipe = domain.RecipeIdentity(
        recipe_id,
        parameters=vasp.frequency_recipe_parameters(potim_angstrom=0.015),
    )
    input_digests = () if selection is None else (selection.input_digest,)
    fingerprint = domain.MethodFingerprint(
        method=method,
        protocol=protocol,
        recipe=recipe,
        input_digests=input_digests,
    )
    calculation = domain.Calculation(
        project_id=project.id,
        calculation_type=calculation_type,
        input_structure_snapshot_id=snapshot.id,
        recipe_id=recipe.recipe_id,
        method_fingerprint_id=fingerprint.id,
    )

    prepared_poscar = vasp.prepare_frequency_poscar(
        snapshot,
        fingerprint=fingerprint,
        selection=selection,
    )
    prepared_kpoints = vasp.prepare_kpoints(
        snapshot,
        policy=kpoints,
        system_context=context,
        centering=vasp.KPointCentering.GAMMA,
    )
    library = vasp.LocalPotcarLibrary("PBE_54", potcar_root)
    resolved = library.resolve(prepared_poscar=prepared_poscar, method=method)
    encut_hash = "e" * 64
    kpoint_hash = None if context.kind is vasp.VaspSystemKind.MOLECULE_0D else "f" * 64
    encut_evidence = vasp.EncCutValidationEvidence(
        core_method_hash=fingerprint.core_method_hash,
        potcar_spec_hash=resolved.spec.metadata_hash,
        tested_encuts_ev=(420.0, 450.0, 500.0),
        selected_encut_ev=450.0,
        analysis_hash=encut_hash,
    )
    kpoint_evidence = (
        None
        if kpoint_hash is None
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
    return _FrequencyCase(
        project_root=project_root,
        snapshot=snapshot,
        fingerprint=fingerprint,
        calculation=calculation,
        context=context,
        library=library,
        lock=lock,
        encut_evidence=encut_evidence,
        kpoint_evidence=kpoint_evidence,
        selection=selection,
    )


@pytest.mark.parametrize(
    "recipe_id",
    (
        vasp.RECIPE_SELECTED_ATOM_FREQUENCY,
        vasp.RECIPE_FULL_FREQUENCY,
        vasp.RECIPE_GAS_FREQUENCY,
    ),
)
def test_frequency_pipeline_materializes_supported_recipes(
    tmp_path: Path,
    recipe_id: str,
) -> None:
    case = _frequency_case(tmp_path, recipe_id=recipe_id)
    result = vasp.prepare_frequency_calculation_inputs(
        project_root=case.project_root,
        calculation=case.calculation,
        snapshot=case.snapshot,
        fingerprint=case.fingerprint,
        system_context=case.context,
        potcar_library=case.library,
        project_lock=case.lock,
        encut_evidence=case.encut_evidence,
        kpoint_evidence=case.kpoint_evidence,
        selection=case.selection,
    )

    parameters = {item.name: item.value for item in result.prepared_incar.parameters}
    assert parameters["NFREE"] == 2
    assert parameters["POTIM"] == pytest.approx(0.015)
    assert parameters["NSW"] == 1
    assert parameters["LCHARG"] is False
    assert parameters["LWAVE"] is False
    expected_ibrion = 5 if recipe_id == vasp.RECIPE_SELECTED_ATOM_FREQUENCY else 6
    assert parameters["IBRION"] == expected_ibrion

    input_dir = case.project_root / result.materialized.input_directory
    assert (input_dir / "INCAR").is_file()
    assert (input_dir / "POSCAR").is_file()
    assert (input_dir / "POTCAR.spec").is_file()
    assert not (input_dir / "POTCAR").exists()


def test_selected_frequency_tracks_uid_after_poscar_species_regrouping(tmp_path: Path) -> None:
    case = _frequency_case(tmp_path, recipe_id=vasp.RECIPE_SELECTED_ATOM_FREQUENCY)
    assert case.selection is not None
    prepared = vasp.prepare_frequency_poscar(
        case.snapshot,
        fingerprint=case.fingerprint,
        selection=case.selection,
    )
    selected_uid = case.selection.atom_uids[0]
    selected_index = prepared.index_map.poscar_index(selected_uid)

    assert selected_index == 2
    assert prepared.selective_flags is not None
    assert prepared.selective_flags[selected_index] == (True, True, True)
    assert all(
        flags == (False, False, False)
        for index, flags in enumerate(prepared.selective_flags)
        if index != selected_index
    )


def test_selected_frequency_requires_exact_selection_digest(tmp_path: Path) -> None:
    case = _frequency_case(tmp_path, recipe_id=vasp.RECIPE_SELECTED_ATOM_FREQUENCY)
    wrong = vasp.FrequencySelection((case.snapshot.sites[0].atom_uid,))

    with pytest.raises(
        vasp.FrequencyPreparationError,
        match="fingerprint does not bind the exact UID selection",
    ):
        vasp.prepare_frequency_poscar(
            case.snapshot,
            fingerprint=case.fingerprint,
            selection=wrong,
        )


def test_frequency_recipe_parameters_are_explicit_and_fail_closed() -> None:
    parameters = vasp.frequency_recipe_parameters(potim_angstrom=0.012)
    assert tuple(item.name for item in parameters) == ("NFREE", "POTIM")
    assert parameters[0].value == 2
    assert parameters[1].value == pytest.approx(0.012)

    with pytest.raises(vasp.FrequencyPreparationError, match="NFREE=2"):
        vasp.frequency_recipe_parameters(potim_angstrom=0.012, nfree=4)
    with pytest.raises(vasp.FrequencyPreparationError, match="finite and positive"):
        vasp.frequency_recipe_parameters(potim_angstrom=0.0)


def test_frequency_incar_rejects_loose_electronic_convergence(tmp_path: Path) -> None:
    case = _frequency_case(tmp_path, recipe_id=vasp.RECIPE_GAS_FREQUENCY)
    loose_protocol = domain.ProtocolDefinition(
        encut_ev=case.fingerprint.protocol.encut_ev,
        kpoints=case.fingerprint.protocol.kpoints,
        precision=case.fingerprint.protocol.precision,
        ediff_ev=1e-6,
        ediffg_ev_per_angstrom=case.fingerprint.protocol.ediffg_ev_per_angstrom,
        ismear=case.fingerprint.protocol.ismear,
        sigma_ev=case.fingerprint.protocol.sigma_ev,
        dipole_policy=case.fingerprint.protocol.dipole_policy,
        lreal=case.fingerprint.protocol.lreal,
        extra_parameters=case.fingerprint.protocol.extra_parameters,
    )
    loose_fingerprint = domain.MethodFingerprint(
        method=case.fingerprint.method,
        protocol=loose_protocol,
        recipe=case.fingerprint.recipe,
    )
    prepared_poscar = vasp.prepare_frequency_poscar(
        case.snapshot,
        fingerprint=loose_fingerprint,
        selection=None,
    )
    prepared_kpoints = vasp.prepare_kpoints(
        case.snapshot,
        policy=loose_protocol.kpoints,
        system_context=case.context,
        centering=vasp.KPointCentering.GAMMA,
    )
    resolved = case.library.resolve(
        prepared_poscar=prepared_poscar,
        method=loose_fingerprint.method,
    )

    with pytest.raises(
        vasp.FrequencyPreparationError,
        match="EDIFF <= 1e-8",
    ):
        vasp.prepare_frequency_incar(
            snapshot=case.snapshot,
            method=loose_fingerprint.method,
            protocol=loose_protocol,
            recipe=loose_fingerprint.recipe,
            system_context=case.context,
            prepared_poscar=prepared_poscar,
            prepared_kpoints=prepared_kpoints,
            potcar_spec=resolved.spec,
            project_lock=case.lock,
        )


def test_materialization_guard_rejects_tampered_selected_poscar(tmp_path: Path) -> None:
    case = _frequency_case(tmp_path, recipe_id=vasp.RECIPE_SELECTED_ATOM_FREQUENCY)
    assert case.selection is not None
    prepared_poscar = vasp.prepare_frequency_poscar(
        case.snapshot,
        fingerprint=case.fingerprint,
        selection=case.selection,
    )
    prepared_kpoints = vasp.prepare_kpoints(
        case.snapshot,
        policy=case.fingerprint.protocol.kpoints,
        system_context=case.context,
        centering=vasp.KPointCentering.GAMMA,
    )
    resolved = case.library.resolve(
        prepared_poscar=prepared_poscar,
        method=case.fingerprint.method,
    )
    prepared_incar = vasp.prepare_frequency_incar(
        snapshot=case.snapshot,
        method=case.fingerprint.method,
        protocol=case.fingerprint.protocol,
        recipe=case.fingerprint.recipe,
        system_context=case.context,
        prepared_poscar=prepared_poscar,
        prepared_kpoints=prepared_kpoints,
        potcar_spec=resolved.spec,
        project_lock=case.lock,
    )
    tampered_poscar = vasp.prepare_poscar(case.snapshot)

    with pytest.raises(
        vasp.InputMaterializationError,
        match="SelectedAtomFrequency requires Selective Dynamics",
    ):
        vasp.materialize_calculation_inputs(
            project_root=case.project_root,
            calculation=case.calculation,
            snapshot=case.snapshot,
            fingerprint=case.fingerprint,
            recipe=case.fingerprint.recipe,
            system_context=case.context,
            prepared_poscar=tampered_poscar,
            prepared_incar=prepared_incar,
            prepared_kpoints=prepared_kpoints,
            potcar_spec=resolved.spec,
            project_lock=case.lock,
        )

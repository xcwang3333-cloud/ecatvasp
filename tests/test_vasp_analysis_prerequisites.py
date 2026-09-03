from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from ecatvasp import domain, vasp


@dataclass(frozen=True)
class _PrerequisiteCase:
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


def _protocol(*, isym: int | None = None) -> domain.ProtocolDefinition:
    return domain.ProtocolDefinition(
        encut_ev=450.0,
        kpoints=domain.KPointPolicy(
            domain.KPointPolicyKind.EXPLICIT_MESH,
            mesh=(3, 3, 1),
        ),
        precision="Accurate",
        ediff_ev=1e-6,
        ediffg_ev_per_angstrom=-0.02,
        ismear=0,
        sigma_ev=0.05,
        dipole_policy=domain.DipolePolicy.OFF,
        lreal=False,
        isym=isym,
        extra_parameters=(
            *vasp.ecat_standard_protocol_parameters(),
            domain.ParameterEntry(vasp.ECATVASP_KPOINT_CENTERING, "gamma"),
        ),
    )


def _snapshot(*, c_uid: domain.AtomUid, o_uid: domain.AtomUid) -> domain.StructureSnapshot:
    return domain.StructureSnapshot(
        lattice=domain.Lattice(
            vectors=((4.0, 0.0, 0.0), (0.0, 4.0, 0.0), (0.0, 0.0, 24.0))
        ),
        sites=(
            domain.StructureSite(c_uid, "C", (0.25, 0.25, 0.45)),
            domain.StructureSite(o_uid, "O", (0.50, 0.50, 0.57)),
        ),
        periodic=(True, True, False),
    )


def _recipe(recipe_id: str) -> domain.RecipeIdentity:
    if recipe_id == vasp.RECIPE_DOS_PREREQUISITE:
        parameters = vasp.dos_recipe_parameters(nedos=2001)
    elif recipe_id == vasp.RECIPE_LOBSTER_PREREQUISITE:
        parameters = vasp.lobster_recipe_parameters(nbands=96)
    else:
        parameters = ()
    return domain.RecipeIdentity(recipe_id, parameters=parameters)


def _calculation_type(recipe_id: str) -> domain.CalculationType:
    if recipe_id == vasp.RECIPE_DOS_PREREQUISITE:
        return domain.CalculationType.DOS_STATIC
    if recipe_id == vasp.RECIPE_CHARGE_DENSITY_STATIC:
        return domain.CalculationType.CHARGE_STATIC
    if recipe_id == vasp.RECIPE_LOBSTER_PREREQUISITE:
        return domain.CalculationType.LOBSTER_PREREQUISITE
    raise AssertionError(recipe_id)


def _build_case(
    tmp_path: Path,
    *,
    recipe_id: str,
) -> _PrerequisiteCase:
    project = domain.Project(name="Analysis prerequisites", slug=recipe_id.split(".")[-1].lower())
    project_root = tmp_path / "project"
    potcar_root = tmp_path / "licensed-potcars"
    c_hash = _write_potcar(potcar_root, symbol="C", zval=4.0, enmax_ev=400.0)
    o_hash = _write_potcar(potcar_root, symbol="O", zval=6.0, enmax_ev=420.0)
    snapshot = _snapshot(c_uid=domain.new_atom_uid(), o_uid=domain.new_atom_uid())
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
    protocol = _protocol(isym=0 if recipe_id == vasp.RECIPE_LOBSTER_PREREQUISITE else None)
    recipe = _recipe(recipe_id)
    fingerprint = domain.MethodFingerprint(method=method, protocol=protocol, recipe=recipe)
    calculation = domain.Calculation(
        project_id=project.id,
        calculation_type=_calculation_type(recipe_id),
        input_structure_snapshot_id=snapshot.id,
        recipe_id=recipe.recipe_id,
        method_fingerprint_id=fingerprint.id,
    )
    context = vasp.VaspSystemContext(
        vasp.VaspSystemKind.SLAB_2D,
        vacuum_axis=vasp.LatticeAxis.C,
    )
    prepared_poscar = vasp.prepare_poscar(snapshot)
    prepared_kpoints = vasp.prepare_kpoints(
        snapshot,
        policy=protocol.kpoints,
        system_context=context,
        centering=vasp.KPointCentering.GAMMA,
    )
    library = vasp.LocalPotcarLibrary("PBE_54", potcar_root)
    resolved = library.resolve(prepared_poscar=prepared_poscar, method=method)
    encut_hash = hashlib.sha256(f"encut-{fingerprint.core_method_hash}".encode()).hexdigest()
    kpoint_hash = hashlib.sha256(f"kpoint-{fingerprint.core_method_hash}".encode()).hexdigest()
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
    return _PrerequisiteCase(
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
    ("recipe_id", "expected"),
    (
        (
            vasp.RECIPE_DOS_PREREQUISITE,
            {"IBRION": -1, "NSW": 0, "LORBIT": 11, "NEDOS": 2001, "LWAVE": False},
        ),
        (
            vasp.RECIPE_CHARGE_DENSITY_STATIC,
            {"IBRION": -1, "NSW": 0, "LCHARG": True, "LAECHG": False, "LWAVE": False},
        ),
        (
            vasp.RECIPE_LOBSTER_PREREQUISITE,
            {"IBRION": -1, "NSW": 0, "NBANDS": 96, "LWAVE": True, "ISYM": 0},
        ),
    ),
)
def test_analysis_prerequisite_pipeline_materializes_supported_recipes(
    tmp_path: Path,
    recipe_id: str,
    expected: dict[str, object],
) -> None:
    case = _build_case(tmp_path, recipe_id=recipe_id)
    result = vasp.prepare_analysis_prerequisite_inputs(
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

    parameters = {item.name: item.value for item in result.prepared_incar.parameters}
    for name, value in expected.items():
        assert parameters[name] == value
    input_dir = case.project_root / result.materialized.input_directory
    assert (input_dir / "INCAR").is_file()
    assert (input_dir / "POSCAR").is_file()
    assert (input_dir / "POTCAR.spec").is_file()
    assert not (input_dir / "POTCAR").exists()


def test_analysis_prerequisite_recipe_controls_fail_closed() -> None:
    assert vasp.dos_recipe_parameters(nedos=1000)[0].value == 1000
    assert vasp.lobster_recipe_parameters(nbands=80)[0].value == 80
    with pytest.raises(vasp.AnalysisPrerequisitePreparationError, match="NEDOS"):
        vasp.dos_recipe_parameters(nedos=1)
    with pytest.raises(vasp.AnalysisPrerequisitePreparationError, match="NBANDS"):
        vasp.lobster_recipe_parameters(nbands=0)


def test_lobster_prerequisite_requires_isym_zero(tmp_path: Path) -> None:
    case = _build_case(tmp_path, recipe_id=vasp.RECIPE_LOBSTER_PREREQUISITE)
    bad_protocol = replace(case.fingerprint.protocol, isym=2)
    bad_fingerprint = domain.MethodFingerprint(
        method=case.fingerprint.method,
        protocol=bad_protocol,
        recipe=case.fingerprint.recipe,
    )
    prepared_poscar = vasp.prepare_poscar(case.snapshot)
    prepared_kpoints = vasp.prepare_kpoints(
        case.snapshot,
        policy=bad_protocol.kpoints,
        system_context=case.context,
        centering=vasp.KPointCentering.GAMMA,
    )
    resolved = case.library.resolve(prepared_poscar=prepared_poscar, method=bad_fingerprint.method)

    with pytest.raises(vasp.AnalysisPrerequisitePreparationError, match="ISYM=0"):
        vasp.prepare_analysis_prerequisite_incar(
            snapshot=case.snapshot,
            method=bad_fingerprint.method,
            protocol=bad_protocol,
            recipe=bad_fingerprint.recipe,
            system_context=case.context,
            prepared_poscar=prepared_poscar,
            prepared_kpoints=prepared_kpoints,
            potcar_spec=resolved.spec,
            project_lock=case.lock,
        )


def test_materialization_guard_rejects_tampered_dos_incar(tmp_path: Path) -> None:
    case = _build_case(tmp_path, recipe_id=vasp.RECIPE_DOS_PREREQUISITE)
    prepared_poscar = vasp.prepare_poscar(case.snapshot)
    prepared_kpoints = vasp.prepare_kpoints(
        case.snapshot,
        policy=case.fingerprint.protocol.kpoints,
        system_context=case.context,
        centering=vasp.KPointCentering.GAMMA,
    )
    resolved = case.library.resolve(prepared_poscar=prepared_poscar, method=case.fingerprint.method)
    prepared = vasp.prepare_analysis_prerequisite_incar(
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
    parameters = tuple(
        vasp.EffectiveIncarParameter(item.name, 10, item.source)
        if item.name == "LORBIT"
        else item
        for item in prepared.parameters
    )
    text = "".join(
        f"{item.name} = {'.TRUE.' if item.value is True else '.FALSE.' if item.value is False else item.value}\n"
        for item in parameters
    )
    tampered = vasp.PreparedIncar(
        structure_snapshot_id=prepared.structure_snapshot_id,
        recipe_id=prepared.recipe_id,
        parameters=parameters,
        text=text,
        sha256=hashlib.sha256(text.encode()).hexdigest(),
    )

    with pytest.raises(vasp.InputMaterializationError, match="does not match recompilation"):
        vasp.materialize_calculation_inputs(
            project_root=case.project_root,
            calculation=case.calculation,
            snapshot=case.snapshot,
            fingerprint=case.fingerprint,
            recipe=case.fingerprint.recipe,
            system_context=case.context,
            prepared_poscar=prepared_poscar,
            prepared_incar=tampered,
            prepared_kpoints=prepared_kpoints,
            potcar_spec=resolved.spec,
            project_lock=case.lock,
        )


def _triplet_member(
    *,
    project: domain.Project,
    snapshot: domain.StructureSnapshot,
    method: domain.MethodDefinition,
    protocol: domain.ProtocolDefinition,
    context: vasp.VaspSystemContext,
    library: vasp.LocalPotcarLibrary,
) -> vasp.ChargeDifferenceTripletMember:
    recipe = domain.RecipeIdentity(vasp.RECIPE_CHARGE_DENSITY_STATIC)
    fingerprint = domain.MethodFingerprint(method=method, protocol=protocol, recipe=recipe)
    calculation = domain.Calculation(
        project_id=project.id,
        calculation_type=domain.CalculationType.CHARGE_STATIC,
        input_structure_snapshot_id=snapshot.id,
        recipe_id=recipe.recipe_id,
        method_fingerprint_id=fingerprint.id,
    )
    prepared_poscar = vasp.prepare_poscar(snapshot)
    prepared_kpoints = vasp.prepare_kpoints(
        snapshot,
        policy=protocol.kpoints,
        system_context=context,
        centering=vasp.KPointCentering.GAMMA,
    )
    resolved = library.resolve(prepared_poscar=prepared_poscar, method=method)
    encut_hash = hashlib.sha256(f"triplet-encut-{fingerprint.core_method_hash}".encode()).hexdigest()
    kpoint_hash = hashlib.sha256(f"triplet-kpoint-{fingerprint.core_method_hash}".encode()).hexdigest()
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
    return vasp.ChargeDifferenceTripletMember(
        calculation=calculation,
        snapshot=snapshot,
        fingerprint=fingerprint,
        project_lock=lock,
        encut_evidence=encut_evidence,
        kpoint_evidence=kpoint_evidence,
    )


def _charge_triplet(
    tmp_path: Path,
) -> tuple[vasp.ChargeDifferenceTriplet, vasp.LocalPotcarLibrary, Path]:
    project = domain.Project(name="Charge difference", slug="charge-difference")
    potcar_root = tmp_path / "licensed-triplet-potcars"
    c_hash = _write_potcar(potcar_root, symbol="C", zval=4.0, enmax_ev=400.0)
    o_hash = _write_potcar(potcar_root, symbol="O", zval=6.0, enmax_ev=420.0)
    c_uid = domain.new_atom_uid()
    o_uid = domain.new_atom_uid()
    combined = _snapshot(c_uid=c_uid, o_uid=o_uid)
    slab = domain.StructureSnapshot(
        lattice=combined.lattice,
        sites=(combined.sites[0],),
        periodic=combined.periodic,
    )
    adsorbate = domain.StructureSnapshot(
        lattice=combined.lattice,
        sites=(combined.sites[1],),
        periodic=combined.periodic,
    )
    common = dict(
        xc_functional="PBE",
        potcar_family="PBE_54",
        dispersion_model="NONE",
        spin_treatment=domain.SpinTreatment.UNPOLARIZED,
    )
    combined_method = domain.MethodDefinition(
        potcars=(
            domain.PotcarIdentity("C", "C", c_hash),
            domain.PotcarIdentity("O", "O", o_hash),
        ),
        **common,
    )
    slab_method = domain.MethodDefinition(
        potcars=(domain.PotcarIdentity("C", "C", c_hash),),
        **common,
    )
    adsorbate_method = domain.MethodDefinition(
        potcars=(domain.PotcarIdentity("O", "O", o_hash),),
        **common,
    )
    protocol = _protocol()
    context = vasp.VaspSystemContext(
        vasp.VaspSystemKind.SLAB_2D,
        vacuum_axis=vasp.LatticeAxis.C,
    )
    library = vasp.LocalPotcarLibrary("PBE_54", potcar_root)
    triplet = vasp.ChargeDifferenceTriplet(
        combined=_triplet_member(
            project=project,
            snapshot=combined,
            method=combined_method,
            protocol=protocol,
            context=context,
            library=library,
        ),
        slab=_triplet_member(
            project=project,
            snapshot=slab,
            method=slab_method,
            protocol=protocol,
            context=context,
            library=library,
        ),
        adsorbate=_triplet_member(
            project=project,
            snapshot=adsorbate,
            method=adsorbate_method,
            protocol=protocol,
            context=context,
            library=library,
        ),
        system_context=context,
    )
    return triplet, library, tmp_path / "triplet-project"


def test_charge_difference_triplet_allows_exact_fragment_potcar_subsets(tmp_path: Path) -> None:
    triplet, library, project_root = _charge_triplet(tmp_path)
    assert len(
        {
            triplet.combined.fingerprint.core_method_hash,
            triplet.slab.fingerprint.core_method_hash,
            triplet.adsorbate.fingerprint.core_method_hash,
        }
    ) == 3

    result = vasp.prepare_charge_difference_triplet_inputs(
        project_root=project_root,
        triplet=triplet,
        potcar_library=library,
    )
    assert result.contract_hash == triplet.contract_hash
    for member in (result.combined, result.slab, result.adsorbate):
        parameters = {item.name: item.value for item in member.prepared_incar.parameters}
        assert parameters["LCHARG"] is True
        assert parameters["IBRION"] == -1
        input_dir = project_root / member.materialized.input_directory
        assert (input_dir / "POTCAR.spec").is_file()
        assert not (input_dir / "POTCAR").exists()


def test_charge_difference_triplet_rejects_moved_fragment_atom(tmp_path: Path) -> None:
    triplet, _, _ = _charge_triplet(tmp_path)
    site = triplet.adsorbate.snapshot.sites[0]
    moved_snapshot = replace(
        triplet.adsorbate.snapshot,
        sites=(replace(site, fractional_coords=(0.50, 0.50, 0.58)),),
    )
    moved_member = replace(triplet.adsorbate, snapshot=moved_snapshot)

    with pytest.raises(
        vasp.AnalysisPrerequisiteInputPipelineError,
        match="preserve frozen element and coordinates",
    ):
        vasp.ChargeDifferenceTriplet(
            combined=triplet.combined,
            slab=triplet.slab,
            adsorbate=moved_member,
            system_context=triplet.system_context,
        )


def test_charge_difference_triplet_rejects_shared_potcar_mismatch(tmp_path: Path) -> None:
    triplet, _, _ = _charge_triplet(tmp_path)
    bad_method = replace(
        triplet.slab.fingerprint.method,
        potcars=(domain.PotcarIdentity("C", "C", "0" * 64),),
    )
    bad_fingerprint = domain.MethodFingerprint(
        method=bad_method,
        protocol=triplet.slab.fingerprint.protocol,
        recipe=triplet.slab.fingerprint.recipe,
    )
    bad_calculation = replace(
        triplet.slab.calculation,
        method_fingerprint_id=bad_fingerprint.id,
    )
    bad_member = replace(
        triplet.slab,
        calculation=bad_calculation,
        fingerprint=bad_fingerprint,
    )

    with pytest.raises(
        vasp.AnalysisPrerequisiteInputPipelineError,
        match="identical POTCAR identities",
    ):
        vasp.ChargeDifferenceTriplet(
            combined=triplet.combined,
            slab=bad_member,
            adsorbate=triplet.adsorbate,
            system_context=triplet.system_context,
        )


def test_charge_difference_triplet_rejects_protocol_mismatch(tmp_path: Path) -> None:
    triplet, _, _ = _charge_triplet(tmp_path)
    bad_protocol = replace(triplet.adsorbate.fingerprint.protocol, sigma_ev=0.08)
    bad_fingerprint = domain.MethodFingerprint(
        method=triplet.adsorbate.fingerprint.method,
        protocol=bad_protocol,
        recipe=triplet.adsorbate.fingerprint.recipe,
    )
    bad_calculation = replace(
        triplet.adsorbate.calculation,
        method_fingerprint_id=bad_fingerprint.id,
    )
    bad_member = replace(
        triplet.adsorbate,
        calculation=bad_calculation,
        fingerprint=bad_fingerprint,
    )

    with pytest.raises(
        vasp.AnalysisPrerequisiteInputPipelineError,
        match="same numerical/electronic Protocol",
    ):
        vasp.ChargeDifferenceTriplet(
            combined=triplet.combined,
            slab=triplet.slab,
            adsorbate=bad_member,
            system_context=triplet.system_context,
        )

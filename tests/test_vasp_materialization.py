from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from ecatvasp import domain, vasp
from ecatvasp.storage import ProjectBundle, ProjectStore


def _case(
    *,
    kpoints: domain.KPointPolicy | None = None,
) -> tuple[
    domain.Project,
    domain.StructureSnapshot,
    domain.MethodFingerprint,
    domain.Calculation,
    domain.RecipeIdentity,
    vasp.VaspSystemContext,
    vasp.PreparedPoscar,
    vasp.PreparedIncar,
    vasp.PreparedKPoints,
    vasp.PotcarSpec,
    vasp.ProjectNumericalLock,
]:
    project = domain.Project(name="Block 6", slug="block-6")
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
            domain.PotcarIdentity("C", "C", "a" * 64),
            domain.PotcarIdentity("O", "O", "b" * 64),
        ),
        dispersion_model="NONE",
        spin_treatment=domain.SpinTreatment.UNPOLARIZED,
    )
    policy = kpoints or domain.KPointPolicy(
        domain.KPointPolicyKind.EXPLICIT_MESH,
        mesh=(3, 3, 1),
    )
    context = vasp.VaspSystemContext(
        vasp.VaspSystemKind.SLAB_2D,
        vacuum_axis=vasp.LatticeAxis.C,
    )
    prepared_poscar = vasp.prepare_poscar(snapshot)
    prepared_kpoints = vasp.prepare_kpoints(
        snapshot,
        policy=policy,
        system_context=context,
        centering=vasp.KPointCentering.GAMMA,
    )
    extra_parameters = (
        *vasp.ecat_standard_protocol_parameters(vacuum_axis="c"),
        prepared_kpoints.protocol_centering_parameter,
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
        extra_parameters=extra_parameters,
    )
    recipe = domain.RecipeIdentity(vasp.RECIPE_ADSORBATE_RELAX)
    fingerprint = domain.MethodFingerprint(method=method, protocol=protocol, recipe=recipe)
    calculation = domain.Calculation(
        project_id=project.id,
        calculation_type=domain.CalculationType.RELAX,
        input_structure_snapshot_id=snapshot.id,
        recipe_id=recipe.recipe_id,
        method_fingerprint_id=fingerprint.id,
    )
    potcar_text = "C\nO\n"
    potcar_spec = vasp.PotcarSpec(
        core_method_hash=fingerprint.core_method_hash,
        entries=(
            vasp.PotcarSpecEntry(
                "C", "C", "PBE_54", "PAW_PBE C", 4.0, 400.0, "a" * 64
            ),
            vasp.PotcarSpecEntry(
                "O", "O", "PBE_54", "PAW_PBE O", 6.0, 400.0, "b" * 64
            ),
        ),
        text=potcar_text,
        sha256=hashlib.sha256(potcar_text.encode("utf-8")).hexdigest(),
    )
    lock = vasp.ProjectNumericalLock(
        project_id=project.id,
        system_kind=context.kind,
        core_method_hash=fingerprint.core_method_hash,
        encut_ev=protocol.encut_ev,
        encut_validation_hash="c" * 64,
        kpoints=protocol.kpoints,
        kpoints_validation_hash="d" * 64,
    )
    prepared_incar = vasp.prepare_incar(
        snapshot=snapshot,
        method=method,
        protocol=protocol,
        recipe=recipe,
        system_context=context,
        prepared_poscar=prepared_poscar,
        prepared_kpoints=prepared_kpoints,
        potcar_spec=potcar_spec,
        project_lock=lock,
    )
    return (
        project,
        snapshot,
        fingerprint,
        calculation,
        recipe,
        context,
        prepared_poscar,
        prepared_incar,
        prepared_kpoints,
        potcar_spec,
        lock,
    )


def _materialize(tmp_path: Path, case: tuple[object, ...]) -> vasp.MaterializedInputSet:
    (
        project,
        snapshot,
        fingerprint,
        calculation,
        recipe,
        context,
        prepared_poscar,
        prepared_incar,
        prepared_kpoints,
        potcar_spec,
        lock,
    ) = case
    assert isinstance(project, domain.Project)
    assert isinstance(snapshot, domain.StructureSnapshot)
    assert isinstance(fingerprint, domain.MethodFingerprint)
    assert isinstance(calculation, domain.Calculation)
    assert isinstance(recipe, domain.RecipeIdentity)
    assert isinstance(context, vasp.VaspSystemContext)
    assert isinstance(prepared_poscar, vasp.PreparedPoscar)
    assert isinstance(prepared_incar, vasp.PreparedIncar)
    assert isinstance(prepared_kpoints, vasp.PreparedKPoints)
    assert isinstance(potcar_spec, vasp.PotcarSpec)
    assert isinstance(lock, vasp.ProjectNumericalLock)
    return vasp.materialize_calculation_inputs(
        project_root=tmp_path,
        calculation=calculation,
        snapshot=snapshot,
        fingerprint=fingerprint,
        recipe=recipe,
        system_context=context,
        prepared_poscar=prepared_poscar,
        prepared_incar=prepared_incar,
        prepared_kpoints=prepared_kpoints,
        potcar_spec=potcar_spec,
        project_lock=lock,
    )


def test_materializes_immutable_safe_inputs_manifest_and_project_entities(
    tmp_path: Path,
) -> None:
    case = _case()
    materialized = _materialize(tmp_path, case)
    project, snapshot, fingerprint, calculation, *_ = case
    assert isinstance(project, domain.Project)
    assert isinstance(snapshot, domain.StructureSnapshot)
    assert isinstance(fingerprint, domain.MethodFingerprint)
    assert isinstance(calculation, domain.Calculation)

    input_dir = tmp_path / materialized.input_directory
    assert {path.name for path in input_dir.iterdir()} == {
        "INCAR",
        "POSCAR",
        "KPOINTS",
        "POTCAR.spec",
        "atom-index-map.json",
        "input-manifest.json",
    }
    assert not (input_dir / "POTCAR").exists()
    assert len(materialized.artifacts) == 6
    assert all(
        artifact.producer == domain.CalculationProducerRef(calculation.id)
        for artifact in materialized.artifacts
    )
    assert all(
        artifact.availability is domain.ArtifactAvailability.LOCAL
        for artifact in materialized.artifacts
    )
    assert all(
        artifact.retrieval_policy is domain.RetrievalPolicy.ALWAYS
        for artifact in materialized.artifacts
    )

    manifest = json.loads((input_dir / "input-manifest.json").read_text(encoding="utf-8"))
    assert manifest["format"] == vasp.INPUT_MANIFEST_FORMAT
    assert manifest["calculation"]["id"] == str(calculation.id)
    assert manifest["structure"]["snapshot_id"] == str(snapshot.id)
    assert manifest["method_fingerprint"]["instance_hash"] == fingerprint.instance_hash
    assert manifest["recipe"]["recipe_hash"] == fingerprint.recipe.recipe_hash
    assert manifest["preparations"]["potcar_metadata_hash"] == case[9].metadata_hash
    assert manifest["validation"]["status"] == "passed"
    assert "POTCAR" not in {item["relative_path"].split("/")[-1] for item in manifest["files"]}

    bundle = ProjectBundle(
        project=project,
        structure_snapshots=(snapshot,),
        method_fingerprints=(fingerprint,),
        calculations=(calculation,),
        artifacts=materialized.artifacts,
        provenance_records=materialized.provenance_records,
        dependency_records=materialized.dependency_records,
    )
    bundle.validate()
    store = ProjectStore(tmp_path)
    store.save(bundle)
    reopened = store.open()
    assert reopened.artifacts == materialized.artifacts
    assert (input_dir / "input-manifest.json").is_file()


def test_atom_index_map_persists_uid_order_and_selective_dynamics(tmp_path: Path) -> None:
    case = list(_case())
    snapshot = case[1]
    fingerprint = case[2]
    assert isinstance(snapshot, domain.StructureSnapshot)
    assert isinstance(fingerprint, domain.MethodFingerprint)
    selective = vasp.UidSelectiveDynamics(
        default_flags=(False, False, False),
        overrides=(
            vasp.AtomSelectiveFlags(snapshot.sites[1].atom_uid, (True, True, True)),
        ),
    )
    prepared_poscar = vasp.prepare_poscar(snapshot, selective_dynamics=selective)
    case[6] = prepared_poscar
    case[7] = vasp.prepare_incar(
        snapshot=snapshot,
        method=fingerprint.method,
        protocol=fingerprint.protocol,
        recipe=case[4],
        system_context=case[5],
        prepared_poscar=prepared_poscar,
        prepared_kpoints=case[8],
        potcar_spec=case[9],
        project_lock=case[10],
    )
    materialized = _materialize(tmp_path, tuple(case))
    payload = json.loads(
        (tmp_path / materialized.input_directory / "atom-index-map.json").read_text(
            encoding="utf-8"
        )
    )
    entries = payload["entries"]
    assert [item["poscar_index"] for item in entries] == [0, 1, 2]
    assert [item["vasp_ordinal"] for item in entries] == [1, 2, 3]
    by_uid = {item["atom_uid"]: item for item in entries}
    assert by_uid[str(snapshot.sites[1].atom_uid)]["selective_dynamics"] == [
        True,
        True,
        True,
    ]


def test_kspacing_manifest_omits_kpoints_file(tmp_path: Path) -> None:
    policy = domain.KPointPolicy(domain.KPointPolicyKind.KSPACING, value=0.5)
    case = _case(kpoints=policy)
    materialized = _materialize(tmp_path, case)
    input_dir = tmp_path / materialized.input_directory

    assert not (input_dir / "KPOINTS").exists()
    assert len(materialized.artifacts) == 5
    payload = json.loads((input_dir / "input-manifest.json").read_text(encoding="utf-8"))
    assert payload["kpoints"]["uses_kpoints_file"] is False
    assert payload["kpoints"]["kspacing_inv_angstrom"] == 0.5
    assert "kpoints" not in {item["role"] for item in payload["files"]}


def test_existing_different_input_content_fails_closed(tmp_path: Path) -> None:
    case = _case()
    materialized = _materialize(tmp_path, case)
    incar = tmp_path / materialized.input_directory / "INCAR"
    incar.write_text("corrupted\n", encoding="utf-8")

    with pytest.raises(
        vasp.InputMaterializationError,
        match="immutable input already exists with different content",
    ):
        _materialize(tmp_path, case)


def test_materialization_rejects_calculation_fingerprint_mismatch(tmp_path: Path) -> None:
    case = list(_case())
    calculation = case[3]
    assert isinstance(calculation, domain.Calculation)
    case[3] = domain.Calculation(
        project_id=calculation.project_id,
        calculation_type=calculation.calculation_type,
        input_structure_snapshot_id=calculation.input_structure_snapshot_id,
        recipe_id="ECatVASP.VASP.WrongRecipe",
        method_fingerprint_id=calculation.method_fingerprint_id,
    )

    with pytest.raises(vasp.InputMaterializationError, match="recipe_id"):
        _materialize(tmp_path, tuple(case))

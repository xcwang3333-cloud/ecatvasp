from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import pytest

from ecatvasp import domain, structures, vasp


@dataclass(frozen=True)
class _AcceptanceCase:
    project_root: Path
    potcar_root: Path
    project: domain.Project
    final_snapshot: domain.StructureSnapshot
    active_site: domain.ActiveSite
    conformer: domain.StateConformer
    calculation: domain.Calculation
    fingerprint: domain.MethodFingerprint
    context: vasp.VaspSystemContext
    pipeline: vasp.CoreInputPipelineResult


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


def _build_acceptance_case(tmp_path: Path) -> _AcceptanceCase:
    project = domain.Project(name="Pb2 CO2RR v0.3 acceptance", slug="pb2-v03-acceptance")
    catalyst = domain.Catalyst(
        project_id=project.id,
        name="Pb2-NC",
        slug="pb2-nc",
        support_type="N-doped graphene",
    )

    graphene = structures.build_graphene(
        structures.GrapheneBuildSpec(nx=3, ny=3, vacuum_gap_angstrom=20.0)
    )
    vacancy_uid = graphene.sites[0].atom_uid
    first_carbon = graphene.sites[2].atom_uid
    second_carbon = graphene.sites[3].atom_uid
    mutation = structures.mutate_structure(
        graphene,
        vacancy_atom_uids=(vacancy_uid,),
        substitutions=(
            structures.DopantSubstitution(first_carbon, "N"),
            structures.DopantSubstitution(second_carbon, "N"),
        ),
        label="graphene-vacancy-N2",
    )
    replacement_map = dict(mutation.replacement_pairs)
    n_anchors = (replacement_map[first_carbon], replacement_map[second_carbon])

    metals = structures.build_multi_metal_site(
        mutation.snapshot,
        structures.MultiMetalSiteSpec(
            centers=(
                structures.MultiMetalCenterSpec(
                    "Pb",
                    n_anchors,
                    domain.SiteSide.TOP,
                    1.6,
                ),
                structures.MultiMetalCenterSpec(
                    "Pb",
                    n_anchors,
                    domain.SiteSide.BOTTOM,
                    1.6,
                ),
            ),
            metal_metal_topology_intent="opposite-side-pair",
            label="Pb2-opposite-side",
        ),
    )
    variant = domain.StructureVariant(
        catalyst_id=catalyst.id,
        name="Pb2 opposite-side",
        variant_type=domain.VariantType.SITE_TOPOLOGY,
        current_structure_snapshot_id=metals.snapshot.id,
        topology_tags=("dual-atom", "opposite-side"),
    )
    active_site = structures.active_site_from_multi_metal(variant=variant, result=metals)
    pb_top, pb_bottom = active_site.center_atom_uids

    adsorbate = structures.build_adsorbate(
        metals.snapshot,
        active_site,
        structures.AdsorbatePlacementSpec(
            template_key="COOH",
            target_center_atom_uids=(pb_top, pb_bottom),
            binding_mode=domain.BindingMode.MULTICENTER,
            height_angstrom=2.0,
            contacts=(
                structures.AdsorbateContactSpec("C", pb_top),
                structures.AdsorbateContactSpec("O_carbonyl", pb_bottom),
            ),
            placement_direction_cartesian=(0.0, 0.0, 1.0),
            orientation_vector_cartesian=(1.0, 0.0, 0.0),
        ),
    )
    state = structures.create_adsorption_state(
        variant,
        active_site,
        state_label="*COOH",
        adsorbates=("COOH",),
        reaction_role="CO2RR CO pathway",
    )
    conformer = structures.state_conformer_from_adsorbate_build(
        state,
        active_site,
        adsorbate,
        name="Pb2 multicenter COOH",
    )
    domain.validate_conformer_context(
        active_site=active_site,
        state=state,
        conformer=conformer,
        snapshot=adsorbate.snapshot,
    )

    potcar_root = tmp_path / "licensed-potcars"
    identities = (
        domain.PotcarIdentity(
            "C",
            "C",
            _write_potcar(potcar_root, symbol="C", zval=4.0, enmax_ev=400.0),
        ),
        domain.PotcarIdentity(
            "N",
            "N",
            _write_potcar(potcar_root, symbol="N", zval=5.0, enmax_ev=400.0),
        ),
        domain.PotcarIdentity(
            "Pb",
            "Pb",
            _write_potcar(potcar_root, symbol="Pb", zval=4.0, enmax_ev=300.0),
        ),
        domain.PotcarIdentity(
            "O",
            "O",
            _write_potcar(potcar_root, symbol="O", zval=6.0, enmax_ev=420.0),
        ),
        domain.PotcarIdentity(
            "H",
            "H",
            _write_potcar(potcar_root, symbol="H", zval=1.0, enmax_ev=350.0),
        ),
    )
    method = domain.MethodDefinition(
        xc_functional="PBE",
        potcar_family="PBE_54",
        potcars=identities,
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
        encut_ev=500.0,
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
    recipe = domain.RecipeIdentity(vasp.RECIPE_ADSORBATE_RELAX)
    fingerprint = domain.MethodFingerprint(method=method, protocol=protocol, recipe=recipe)
    calculation = domain.Calculation(
        project_id=project.id,
        calculation_type=domain.CalculationType.RELAX,
        input_structure_snapshot_id=adsorbate.snapshot.id,
        recipe_id=recipe.recipe_id,
        method_fingerprint_id=fingerprint.id,
    )

    prepared_poscar = vasp.prepare_poscar(adsorbate.snapshot)
    prepared_kpoints = vasp.prepare_kpoints(
        adsorbate.snapshot,
        policy=kpoints,
        system_context=context,
        centering=vasp.KPointCentering.GAMMA,
    )
    library = vasp.LocalPotcarLibrary("PBE_54", potcar_root)
    resolved = library.resolve(prepared_poscar=prepared_poscar, method=method)
    encut_hash = "a" * 64
    kpoint_hash = "b" * 64
    encut_evidence = vasp.EncCutValidationEvidence(
        core_method_hash=fingerprint.core_method_hash,
        potcar_spec_hash=resolved.spec.metadata_hash,
        tested_encuts_ev=(420.0, 500.0, 550.0),
        selected_encut_ev=500.0,
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
        encut_ev=500.0,
        encut_validation_hash=encut_hash,
        kpoints=kpoints,
        kpoints_validation_hash=kpoint_hash,
    )
    project_root = tmp_path / "project"
    pipeline = vasp.prepare_core_calculation_inputs(
        project_root=project_root,
        calculation=calculation,
        snapshot=adsorbate.snapshot,
        fingerprint=fingerprint,
        system_context=context,
        potcar_library=library,
        project_lock=lock,
        encut_evidence=encut_evidence,
        kpoint_evidence=kpoint_evidence,
    )
    return _AcceptanceCase(
        project_root=project_root,
        potcar_root=potcar_root,
        project=project,
        final_snapshot=adsorbate.snapshot,
        active_site=active_site,
        conformer=conformer,
        calculation=calculation,
        fingerprint=fingerprint,
        context=context,
        pipeline=pipeline,
    )


def _plan(
    case: _AcceptanceCase,
    *,
    settings: domain.ExecutionSettings | None = None,
) -> vasp.ExecutionPlan:
    return vasp.build_execution_plan(
        project_root=case.project_root,
        calculation=case.calculation,
        fingerprint=case.fingerprint,
        system_context=case.context,
        materialized=case.pipeline.materialized,
        resolved_potcars=case.pipeline.resolved_potcars,
        execution_settings=settings,
    )


def test_model_studio_pb2_cooh_reaches_portable_execution_plan(tmp_path: Path) -> None:
    case = _build_acceptance_case(tmp_path)
    settings = domain.ExecutionSettings(
        ncore=4,
        kpar=1,
        mpi_ranks=8,
        omp_threads=1,
        executable="vasp_std",
    )
    plan = _plan(case, settings=settings)

    assert case.active_site.nuclearity == 2
    assert case.conformer.binding_mode is domain.BindingMode.MULTICENTER
    bound_uids = {
        uid
        for edge in case.conformer.binding_edges
        for uid in (edge.adsorbate_atom_uid, edge.site_atom_uid)
    }
    assert all(case.final_snapshot.contains_atom(uid) for uid in bound_uids)
    assert all(case.pipeline.prepared_poscar.index_map.poscar_index(uid) >= 0 for uid in bound_uids)

    assert plan.calculation_id == case.calculation.id
    assert plan.input_manifest_sha256 == case.pipeline.materialized.manifest.sha256
    assert plan.preparation_hash == case.pipeline.materialized.manifest.preparation_hash
    assert plan.execution_settings_hash == settings.execution_hash
    assert plan.potcar_resolution.family == "PBE_54"
    assert tuple(item.element for item in plan.potcar_resolution.entries) == (
        "C",
        "N",
        "Pb",
        "O",
        "H",
    )
    assert {item.target_relative_path for item in plan.staging_inputs} == {
        "INCAR",
        "KPOINTS",
        "POSCAR",
        "POTCAR.spec",
        "atom-index-map.json",
        "input-manifest.json",
    }
    assert {
        item.role for item in plan.staging_inputs if item.kind is vasp.StagingInputKind.VASP_INPUT
    } == {"incar", "kpoints", "poscar"}
    outputs = {item.role: item for item in plan.expected_outputs}
    assert outputs["outcar"].required
    assert outputs["contcar"].required
    assert not outputs["oszicar"].required
    assert str(case.potcar_root) not in repr(plan)
    assert plan.potcar_resolution.target_relative_path == "POTCAR"


def test_execution_tuning_changes_plan_not_method_fingerprint(tmp_path: Path) -> None:
    case = _build_acceptance_case(tmp_path)
    instance_hash = case.fingerprint.instance_hash
    first = _plan(case, settings=domain.ExecutionSettings(ncore=4, executable="vasp_std"))
    second = _plan(case, settings=domain.ExecutionSettings(ncore=8, executable="vasp_std"))

    assert first.plan_hash != second.plan_hash
    assert first.execution_settings_hash != second.execution_settings_hash
    assert case.fingerprint.instance_hash == instance_hash


def test_execution_plan_rejects_scheduler_resource_selection(tmp_path: Path) -> None:
    case = _build_acceptance_case(tmp_path)

    with pytest.raises(vasp.ExecutionPlanError, match="defers scheduler resource fields"):
        _plan(case, settings=domain.ExecutionSettings(nodes=1))


def test_execution_plan_rejects_tampered_staging_file(tmp_path: Path) -> None:
    case = _build_acceptance_case(tmp_path)
    input_dir = case.project_root / case.pipeline.materialized.input_directory
    incar = input_dir / "INCAR"
    raw = incar.read_bytes()
    incar.write_bytes(b"X" + raw[1:])

    with pytest.raises(vasp.ExecutionPlanError, match="file hash changed"):
        _plan(case)


def test_execution_plan_rejects_potcar_drift_after_materialization(tmp_path: Path) -> None:
    case = _build_acceptance_case(tmp_path)
    potcar = case.pipeline.resolved_potcars.ordered_paths[0]
    potcar.write_bytes(potcar.read_bytes() + b"\n")

    with pytest.raises(vasp.ExecutionPlanError, match="licensed POTCAR changed"):
        _plan(case)

from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest

from ecatvasp import domain, vasp
from ecatvasp.domain.ids import new_project_id


def _snapshot(
    *,
    lattice: domain.Lattice | None = None,
) -> domain.StructureSnapshot:
    return domain.StructureSnapshot(
        lattice=lattice
        or domain.Lattice(
            vectors=((10.0, 0.0, 0.0), (0.0, 10.0, 0.0), (0.0, 0.0, 20.0))
        ),
        sites=(
            domain.StructureSite(domain.new_atom_uid(), "C", (0.0, 0.0, 0.25)),
            domain.StructureSite(domain.new_atom_uid(), "Pb", (0.25, 0.25, 0.55)),
            domain.StructureSite(domain.new_atom_uid(), "C", (0.5, 0.5, 0.25)),
            domain.StructureSite(domain.new_atom_uid(), "O", (0.25, 0.25, 0.65)),
        ),
        periodic=(True, True, False),
    )


def _method(
    *,
    spin: domain.SpinTreatment = domain.SpinTreatment.COLLINEAR,
    dispersion: str | None = "NONE",
    charge_e: float = 0.0,
    soc: bool = False,
    dft_u: tuple[domain.DftUSetting, ...] = (),
) -> domain.MethodDefinition:
    return domain.MethodDefinition(
        xc_functional="PBE",
        potcar_family="PBE_54",
        potcars=(
            domain.PotcarIdentity("C", "C", "a" * 64),
            domain.PotcarIdentity("Pb", "Pb_d", "b" * 64),
            domain.PotcarIdentity("O", "O", "c" * 64),
        ),
        dispersion_model=dispersion,
        spin_treatment=spin,
        soc=soc,
        charge_e=charge_e,
        dft_u=dft_u,
    )


def _potcar_spec(method: domain.MethodDefinition) -> vasp.PotcarSpec:
    entries = (
        vasp.PotcarSpecEntry("C", "C", "PBE_54", "PAW_PBE C", 4.0, 400.0, "a" * 64),
        vasp.PotcarSpecEntry(
            "Pb", "Pb_d", "PBE_54", "PAW_PBE Pb_d", 14.0, 300.0, "b" * 64
        ),
        vasp.PotcarSpecEntry("O", "O", "PBE_54", "PAW_PBE O", 6.0, 400.0, "c" * 64),
    )
    text = "C\nPb_d\nO\n"
    return vasp.PotcarSpec(
        core_method_hash=domain.canonical_sha256(method),
        entries=entries,
        text=text,
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def _slab() -> vasp.VaspSystemContext:
    return vasp.VaspSystemContext(
        vasp.VaspSystemKind.SLAB_2D,
        vacuum_axis=vasp.LatticeAxis.C,
    )


def _magmom(snapshot: domain.StructureSnapshot, *, vector: bool = False) -> vasp.UidMagmom:
    values = (
        (0.1, 0.0, 0.0) if vector else (0.1,),
        (1.5, 0.2, 0.0) if vector else (1.5,),
        (0.2, 0.0, 0.0) if vector else (0.2,),
        (0.3, 0.0, 0.0) if vector else (0.3,),
    )
    return vasp.UidMagmom(
        tuple(
            vasp.AtomMagmom(site.atom_uid, value)
            for site, value in zip(snapshot.sites, values, strict=True)
        )
    )


def _prepared(
    snapshot: domain.StructureSnapshot,
    *,
    policy: domain.KPointPolicy | None = None,
    context: vasp.VaspSystemContext | None = None,
) -> tuple[vasp.PreparedPoscar, vasp.PreparedKPoints, vasp.VaspSystemContext]:
    resolved_context = context or _slab()
    resolved_policy = policy or domain.KPointPolicy(
        domain.KPointPolicyKind.EXPLICIT_MESH,
        mesh=(3, 3, 1),
    )
    poscar = vasp.prepare_poscar(snapshot)
    kpoints = vasp.prepare_kpoints(
        snapshot,
        policy=resolved_policy,
        system_context=resolved_context,
        centering=(
            None
            if resolved_policy.kind is domain.KPointPolicyKind.GAMMA_ONLY
            else vasp.KPointCentering.GAMMA
        ),
    )
    return poscar, kpoints, resolved_context


def _protocol(
    *,
    kpoints: vasp.PreparedKPoints,
    magmom: vasp.UidMagmom | None,
    context: vasp.VaspSystemContext,
    dipole_policy: domain.DipolePolicy = domain.DipolePolicy.AUTO,
    extra: tuple[domain.ParameterEntry, ...] = (),
) -> domain.ProtocolDefinition:
    standard = vasp.ecat_standard_protocol_parameters(
        vacuum_axis=(
            context.vacuum_axis.value
            if context.kind is vasp.VaspSystemKind.SLAB_2D
            and dipole_policy is not domain.DipolePolicy.OFF
            else None
        )
    )
    extras = (*standard, kpoints.protocol_centering_parameter, *extra)
    initialization = (magmom.protocol_parameter,) if magmom is not None else ()
    return domain.ProtocolDefinition(
        encut_ev=450.0,
        kpoints=kpoints.policy,
        precision="Accurate",
        ediff_ev=1e-6,
        ediffg_ev_per_angstrom=-0.02,
        ismear=0,
        sigma_ev=0.05,
        dipole_policy=dipole_policy,
        lreal=False,
        initialization_parameters=initialization,
        extra_parameters=extras,
    )


def _lock(
    *,
    method: domain.MethodDefinition,
    protocol: domain.ProtocolDefinition,
    context: vasp.VaspSystemContext,
) -> vasp.ProjectNumericalLock:
    return vasp.ProjectNumericalLock(
        project_id=new_project_id(),
        system_kind=context.kind,
        core_method_hash=domain.canonical_sha256(method),
        encut_ev=protocol.encut_ev,
        encut_validation_hash="d" * 64,
        kpoints=protocol.kpoints,
        kpoints_validation_hash=(
            None if context.kind is vasp.VaspSystemKind.MOLECULE_0D else "e" * 64
        ),
    )


def _prepare_core(
    snapshot: domain.StructureSnapshot,
    *,
    method: domain.MethodDefinition | None = None,
    recipe: domain.RecipeIdentity | None = None,
    policy: domain.KPointPolicy | None = None,
    context: vasp.VaspSystemContext | None = None,
    dipole_policy: domain.DipolePolicy = domain.DipolePolicy.AUTO,
    magmom: vasp.UidMagmom | None = None,
    protocol_extra: tuple[domain.ParameterEntry, ...] = (),
) -> vasp.PreparedIncar:
    resolved_method = method or _method()
    poscar, kpoints, resolved_context = _prepared(
        snapshot,
        policy=policy,
        context=context,
    )
    resolved_magmom = magmom
    if (
        resolved_magmom is None
        and resolved_method.spin_treatment is not domain.SpinTreatment.UNPOLARIZED
    ):
        resolved_magmom = _magmom(snapshot)
    protocol = _protocol(
        kpoints=kpoints,
        magmom=resolved_magmom,
        context=resolved_context,
        dipole_policy=dipole_policy,
        extra=protocol_extra,
    )
    resolved_recipe = recipe or domain.RecipeIdentity(vasp.RECIPE_ADSORBATE_RELAX)
    lock = (
        None
        if resolved_recipe.recipe_id
        in {vasp.RECIPE_ENCUT_CONVERGENCE_POINT, vasp.RECIPE_KPOINT_CONVERGENCE_POINT}
        else _lock(method=resolved_method, protocol=protocol, context=resolved_context)
    )
    return vasp.prepare_incar(
        snapshot=snapshot,
        method=resolved_method,
        protocol=protocol,
        recipe=resolved_recipe,
        system_context=resolved_context,
        prepared_poscar=poscar,
        prepared_kpoints=kpoints,
        potcar_spec=_potcar_spec(resolved_method),
        project_lock=lock,
        magmom=resolved_magmom,
    )


def test_slab_relax_incar_is_deterministic_and_maps_magmom_by_uid() -> None:
    snapshot = _snapshot()
    first = _prepare_core(snapshot)
    second = _prepare_core(snapshot)

    assert first == second
    assert first.sha256 == second.sha256
    assert "GGA = PE\n" in first.text
    assert "PREC = Accurate\n" in first.text
    assert "ALGO = Normal\n" in first.text
    assert "LASPH = .TRUE.\n" in first.text
    assert "IBRION = 2\n" in first.text
    assert "ISIF = 2\n" in first.text
    assert "NSW = 200\n" in first.text
    assert "EDIFFG = -0.02\n" in first.text
    assert "LDIPOL = .TRUE.\n" in first.text
    assert "IDIPOL = 3\n" in first.text
    assert "ISPIN = 2\n" in first.text
    assert "MAGMOM = 0.1 0.2 1.5 0.3\n" in first.text
    assert "KSPACING" not in first.text


def test_kspacing_is_materialized_only_in_incar() -> None:
    snapshot = _snapshot()
    policy = domain.KPointPolicy(domain.KPointPolicyKind.KSPACING, value=0.5)
    prepared = _prepare_core(snapshot, policy=policy)

    assert "KSPACING = 0.5\n" in prepared.text
    assert "KGAMMA = .TRUE.\n" in prepared.text


def test_vdw_policy_must_be_explicit() -> None:
    with pytest.raises(vasp.IncarPreparationError, match="vdW policy is unresolved"):
        _prepare_core(_snapshot(), method=_method(dispersion=None))


def test_unpolarized_method_rejects_magmom_and_emits_ispin_one() -> None:
    snapshot = _snapshot()
    method = _method(spin=domain.SpinTreatment.UNPOLARIZED)
    prepared = _prepare_core(snapshot, method=method)
    assert "ISPIN = 1\n" in prepared.text
    assert "MAGMOM" not in prepared.text

    poscar, kpoints, context = _prepared(snapshot)
    magmom = _magmom(snapshot)
    protocol = _protocol(kpoints=kpoints, magmom=magmom, context=context)
    with pytest.raises(vasp.IncarPreparationError, match="UNPOLARIZED"):
        vasp.prepare_incar(
            snapshot=snapshot,
            method=method,
            protocol=protocol,
            recipe=domain.RecipeIdentity(vasp.RECIPE_ADSORBATE_RELAX),
            system_context=context,
            prepared_poscar=poscar,
            prepared_kpoints=kpoints,
            potcar_spec=_potcar_spec(method),
            project_lock=_lock(method=method, protocol=protocol, context=context),
            magmom=magmom,
        )


def test_collinear_magmom_requires_exact_uid_coverage_and_protocol_hash() -> None:
    snapshot = _snapshot()
    method = _method()
    poscar, kpoints, context = _prepared(snapshot)
    incomplete = vasp.UidMagmom(
        tuple(vasp.AtomMagmom(site.atom_uid, (0.0,)) for site in snapshot.sites[:-1])
    )
    protocol = _protocol(kpoints=kpoints, magmom=incomplete, context=context)

    with pytest.raises(vasp.IncarPreparationError, match="exactly cover"):
        vasp.prepare_incar(
            snapshot=snapshot,
            method=method,
            protocol=protocol,
            recipe=domain.RecipeIdentity(vasp.RECIPE_ADSORBATE_RELAX),
            system_context=context,
            prepared_poscar=poscar,
            prepared_kpoints=kpoints,
            potcar_spec=_potcar_spec(method),
            project_lock=_lock(method=method, protocol=protocol, context=context),
            magmom=incomplete,
        )

    correct = _magmom(snapshot)
    wrong_protocol = replace(protocol, initialization_parameters=())
    with pytest.raises(vasp.IncarPreparationError, match="ECATVASP_MAGMOM_UID_HASH"):
        vasp.prepare_incar(
            snapshot=snapshot,
            method=method,
            protocol=wrong_protocol,
            recipe=domain.RecipeIdentity(vasp.RECIPE_ADSORBATE_RELAX),
            system_context=context,
            prepared_poscar=poscar,
            prepared_kpoints=kpoints,
            potcar_spec=_potcar_spec(method),
            project_lock=_lock(method=method, protocol=wrong_protocol, context=context),
            magmom=correct,
        )


def test_noncollinear_soc_uses_vector_magmom_without_ispin_two() -> None:
    snapshot = _snapshot()
    method = _method(spin=domain.SpinTreatment.NONCOLLINEAR, soc=True)
    prepared = _prepare_core(snapshot, method=method, magmom=_magmom(snapshot, vector=True))

    assert "LNONCOLLINEAR = .TRUE.\n" in prepared.text
    assert "LSORBIT = .TRUE.\n" in prepared.text
    assert "ISPIN = 2" not in prepared.text
    assert "MAGMOM = 0.1 0 0 0.2 0 0 1.5 0.2 0 0.3 0 0\n" in prepared.text


def test_dft_u_arrays_follow_poscar_species_order() -> None:
    snapshot = _snapshot()
    method = _method(dft_u=(domain.DftUSetting("Pb", 2, 3.0, 0.0),))
    prepared = _prepare_core(snapshot, method=method)

    assert "LDAU = .TRUE.\n" in prepared.text
    assert "LDAUTYPE = 2\n" in prepared.text
    assert "LDAUL = -1 2 -1\n" in prepared.text
    assert "LDAUU = 0 3 0\n" in prepared.text
    assert "LDAUJ = 0 0 0\n" in prepared.text


def test_positive_charge_removes_electrons_using_potcar_zval() -> None:
    snapshot = _snapshot()
    prepared = _prepare_core(
        snapshot,
        method=_method(charge_e=1.0),
        dipole_policy=domain.DipolePolicy.OFF,
    )

    # Neutral valence count = 2*C(4) + Pb(14) + O(6) = 28.
    assert "NELECT = 27\n" in prepared.text
    assert "LDIPOL = .FALSE.\n" in prepared.text


def test_charged_ldipol_requires_cubic_supercell() -> None:
    with pytest.raises(vasp.IncarPreparationError, match="cubic supercell"):
        _prepare_core(_snapshot(), method=_method(charge_e=1.0))

    cubic = _snapshot(
        lattice=domain.Lattice(
            vectors=((10.0, 0.0, 0.0), (0.0, 10.0, 0.0), (0.0, 0.0, 10.0))
        )
    )
    prepared = _prepare_core(cubic, method=_method(charge_e=1.0))
    assert "NELECT = 27\n" in prepared.text
    assert "LDIPOL = .TRUE.\n" in prepared.text


def test_auto_dipole_uses_slab_vacuum_axis_and_checks_orthogonality() -> None:
    snapshot = _snapshot()
    prepared = _prepare_core(snapshot)
    assert "IDIPOL = 3\n" in prepared.text

    skewed = _snapshot(
        lattice=domain.Lattice(
            vectors=((10.0, 0.0, 0.0), (0.0, 10.0, 0.0), (1.0, 0.0, 20.0))
        )
    )
    with pytest.raises(vasp.IncarPreparationError, match="orthogonal"):
        _prepare_core(skewed)


def test_periodic_3d_requires_dipole_off() -> None:
    snapshot = _snapshot()
    context = vasp.VaspSystemContext(vasp.VaspSystemKind.PERIODIC_3D)
    policy = domain.KPointPolicy(
        domain.KPointPolicyKind.EXPLICIT_MESH,
        mesh=(3, 3, 3),
    )
    with pytest.raises(vasp.IncarPreparationError, match=r"DipolePolicy\.OFF"):
        _prepare_core(
            snapshot,
            context=context,
            policy=policy,
            recipe=domain.RecipeIdentity(vasp.RECIPE_GROUND_STATE_STATIC),
        )

    prepared = _prepare_core(
        snapshot,
        context=context,
        policy=policy,
        recipe=domain.RecipeIdentity(vasp.RECIPE_GROUND_STATE_STATIC),
        dipole_policy=domain.DipolePolicy.OFF,
    )
    assert "LDIPOL = .FALSE.\n" in prepared.text


def test_recipe_override_is_fingerprinted_and_applied() -> None:
    recipe = domain.RecipeIdentity(
        vasp.RECIPE_ADSORBATE_RELAX,
        parameters=(domain.ParameterEntry("NSW", 75),),
    )
    prepared = _prepare_core(_snapshot(), recipe=recipe)
    assert "NSW = 75\n" in prepared.text


def test_standard_requires_fingerprinted_algo_and_lasph() -> None:
    snapshot = _snapshot()
    method = _method()
    magmom = _magmom(snapshot)
    poscar, kpoints, context = _prepared(snapshot)
    protocol = domain.ProtocolDefinition(
        encut_ev=450.0,
        kpoints=kpoints.policy,
        precision="Accurate",
        ediff_ev=1e-6,
        ediffg_ev_per_angstrom=-0.02,
        dipole_policy=domain.DipolePolicy.AUTO,
        lreal=False,
        initialization_parameters=(magmom.protocol_parameter,),
        extra_parameters=(
            kpoints.protocol_centering_parameter,
            domain.ParameterEntry(vasp.ECATVASP_DIPOLE_AXIS, "c"),
        ),
    )
    with pytest.raises(vasp.IncarPreparationError, match="LASPH"):
        vasp.prepare_incar(
            snapshot=snapshot,
            method=method,
            protocol=protocol,
            recipe=domain.RecipeIdentity(vasp.RECIPE_ADSORBATE_RELAX),
            system_context=context,
            prepared_poscar=poscar,
            prepared_kpoints=kpoints,
            potcar_spec=_potcar_spec(method),
            project_lock=_lock(method=method, protocol=protocol, context=context),
            magmom=magmom,
        )


def test_lock_mismatch_and_deferred_recipes_fail_closed() -> None:
    snapshot = _snapshot()
    method = _method()
    magmom = _magmom(snapshot)
    poscar, kpoints, context = _prepared(snapshot)
    protocol = _protocol(kpoints=kpoints, magmom=magmom, context=context)
    wrong_lock = replace(
        _lock(method=method, protocol=protocol, context=context),
        encut_ev=500.0,
    )
    with pytest.raises(vasp.IncarPreparationError, match="ENCUT"):
        vasp.prepare_incar(
            snapshot=snapshot,
            method=method,
            protocol=protocol,
            recipe=domain.RecipeIdentity(vasp.RECIPE_ADSORBATE_RELAX),
            system_context=context,
            prepared_poscar=poscar,
            prepared_kpoints=kpoints,
            potcar_spec=_potcar_spec(method),
            project_lock=wrong_lock,
            magmom=magmom,
        )

    with pytest.raises(vasp.IncarPreparationError, match="Block 8/9"):
        vasp.prepare_incar(
            snapshot=snapshot,
            method=method,
            protocol=protocol,
            recipe=domain.RecipeIdentity(vasp.RECIPE_SELECTED_ATOM_FREQUENCY),
            system_context=context,
            prepared_poscar=poscar,
            prepared_kpoints=kpoints,
            potcar_spec=_potcar_spec(method),
            project_lock=_lock(method=method, protocol=protocol, context=context),
            magmom=magmom,
        )

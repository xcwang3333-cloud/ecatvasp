from __future__ import annotations

from dataclasses import replace

import pytest

from ecatvasp import domain, vasp


def _snapshot() -> domain.StructureSnapshot:
    return domain.StructureSnapshot(
        lattice=domain.Lattice(
            vectors=((10.0, 0.0, 0.0), (0.0, 10.0, 0.0), (0.0, 0.0, 20.0))
        ),
        sites=(domain.StructureSite(domain.new_atom_uid(), "C", (0.0, 0.0, 0.5)),),
        periodic=(True, True, False),
    )


def _slab() -> vasp.VaspSystemContext:
    return vasp.VaspSystemContext(
        vasp.VaspSystemKind.SLAB_2D,
        vacuum_axis=vasp.LatticeAxis.C,
    )


def test_explicit_mesh_is_deterministic_and_fingerprints_centering() -> None:
    snapshot = _snapshot()
    policy = domain.KPointPolicy(
        domain.KPointPolicyKind.EXPLICIT_MESH,
        mesh=(3, 3, 1),
    )

    first = vasp.prepare_kpoints(
        snapshot,
        policy=policy,
        system_context=_slab(),
        centering=vasp.KPointCentering.GAMMA,
    )
    second = vasp.prepare_kpoints(
        snapshot,
        policy=policy,
        system_context=_slab(),
        centering=vasp.KPointCentering.GAMMA,
    )

    assert first == second
    assert first.mesh == (3, 3, 1)
    assert first.uses_kpoints_file is True
    assert first.text == "ECatVASP explicit mesh\n0\nGamma\n3 3 1\n0 0 0\n"
    assert first.sha256 is not None
    assert first.identity_hash == second.identity_hash
    assert first.protocol_centering_parameter == domain.ParameterEntry(
        vasp.ECATVASP_KPOINT_CENTERING,
        "gamma",
    )


def test_non_gamma_only_policy_requires_explicit_centering() -> None:
    policy = domain.KPointPolicy(
        domain.KPointPolicyKind.EXPLICIT_MESH,
        mesh=(3, 3, 1),
    )

    with pytest.raises(vasp.KPointPreparationError, match="centering must be explicit"):
        vasp.prepare_kpoints(_snapshot(), policy=policy, system_context=_slab())


def test_slab_explicit_mesh_rejects_sampling_vacuum_axis() -> None:
    policy = domain.KPointPolicy(
        domain.KPointPolicyKind.EXPLICIT_MESH,
        mesh=(3, 3, 2),
    )

    with pytest.raises(vasp.KPointPreparationError, match="vacuum axis"):
        vasp.prepare_kpoints(
            _snapshot(),
            policy=policy,
            system_context=_slab(),
            centering=vasp.KPointCentering.GAMMA,
        )


def test_molecule_requires_canonical_gamma_only_policy() -> None:
    molecule = vasp.VaspSystemContext(vasp.VaspSystemKind.MOLECULE_0D)
    gamma_policy = domain.KPointPolicy(domain.KPointPolicyKind.GAMMA_ONLY)

    prepared = vasp.prepare_kpoints(
        _snapshot(),
        policy=gamma_policy,
        system_context=molecule,
    )
    assert prepared.mesh == (1, 1, 1)
    assert prepared.centering is vasp.KPointCentering.GAMMA
    assert prepared.text is not None and "Gamma\n1 1 1" in prepared.text

    explicit = domain.KPointPolicy(
        domain.KPointPolicyKind.EXPLICIT_MESH,
        mesh=(1, 1, 1),
    )
    with pytest.raises(vasp.KPointPreparationError, match="GAMMA_ONLY"):
        vasp.prepare_kpoints(
            _snapshot(),
            policy=explicit,
            system_context=molecule,
            centering=vasp.KPointCentering.GAMMA,
        )


def test_reciprocal_density_uses_kppvol_semantics_and_fixes_slab_vacuum_axis() -> None:
    policy = domain.KPointPolicy(
        domain.KPointPolicyKind.RECIPROCAL_DENSITY,
        value=64.0,
    )

    prepared = vasp.prepare_kpoints(
        _snapshot(),
        policy=policy,
        system_context=_slab(),
        centering=vasp.KPointCentering.GAMMA,
    )

    assert prepared.mesh == (2, 2, 1)
    assert prepared.text is not None
    assert "reciprocal density 64" in prepared.text
    assert "2 2 1" in prepared.text


def test_kspacing_uses_incar_handoff_and_never_materializes_kpoints() -> None:
    policy = domain.KPointPolicy(domain.KPointPolicyKind.KSPACING, value=0.5)

    prepared = vasp.prepare_kpoints(
        _snapshot(),
        policy=policy,
        system_context=_slab(),
        centering=vasp.KPointCentering.GAMMA,
    )

    assert prepared.mesh == (2, 2, 1)
    assert prepared.text is None
    assert prepared.sha256 is None
    assert prepared.uses_kpoints_file is False
    assert prepared.kspacing_inv_angstrom == 0.5
    assert prepared.kgamma is True
    vasp.validate_kpoints_file_presence(
        prepared=prepared,
        kpoints_file_present=False,
    )

    with pytest.raises(vasp.KPointPreparationError, match="conflicts"):
        vasp.validate_kpoints_file_presence(
            prepared=prepared,
            kpoints_file_present=True,
        )


def test_kspacing_fails_when_vasp_would_sample_slab_vacuum_axis() -> None:
    policy = domain.KPointPolicy(domain.KPointPolicyKind.KSPACING, value=0.2)

    with pytest.raises(vasp.KPointPreparationError, match="vacuum axis"):
        vasp.prepare_kpoints(
            _snapshot(),
            policy=policy,
            system_context=_slab(),
            centering=vasp.KPointCentering.GAMMA,
        )


def test_hexagonal_lattice_rejects_monkhorst_pack() -> None:
    snapshot = replace(
        _snapshot(),
        lattice=domain.Lattice(
            vectors=(
                (2.46, 0.0, 0.0),
                (1.23, 2.130422493, 0.0),
                (0.0, 0.0, 20.0),
            )
        ),
    )
    policy = domain.KPointPolicy(
        domain.KPointPolicyKind.EXPLICIT_MESH,
        mesh=(6, 6, 1),
    )

    with pytest.raises(vasp.KPointPreparationError, match="hexagonal"):
        vasp.prepare_kpoints(
            snapshot,
            policy=policy,
            system_context=_slab(),
            centering=vasp.KPointCentering.MONKHORST_PACK,
        )


def test_hexagonal_detection_is_independent_of_vacuum_axis_order() -> None:
    snapshot = replace(
        _snapshot(),
        lattice=domain.Lattice(
            vectors=(
                (0.0, 0.0, 20.0),
                (2.46, 0.0, 0.0),
                (1.23, 2.130422493, 0.0),
            )
        ),
    )
    context = vasp.VaspSystemContext(
        vasp.VaspSystemKind.SLAB_2D,
        vacuum_axis=vasp.LatticeAxis.A,
    )
    policy = domain.KPointPolicy(
        domain.KPointPolicyKind.EXPLICIT_MESH,
        mesh=(1, 6, 6),
    )

    with pytest.raises(vasp.KPointPreparationError, match="hexagonal"):
        vasp.prepare_kpoints(
            snapshot,
            policy=policy,
            system_context=context,
            centering=vasp.KPointCentering.MONKHORST_PACK,
        )


def test_protocol_contract_requires_namespaced_centering_identity() -> None:
    policy = domain.KPointPolicy(
        domain.KPointPolicyKind.EXPLICIT_MESH,
        mesh=(3, 3, 1),
    )
    gamma = vasp.prepare_kpoints(
        _snapshot(),
        policy=policy,
        system_context=_slab(),
        centering=vasp.KPointCentering.GAMMA,
    )
    monkhorst = vasp.prepare_kpoints(
        _snapshot(),
        policy=policy,
        system_context=_slab(),
        centering=vasp.KPointCentering.MONKHORST_PACK,
    )
    gamma_protocol = domain.ProtocolDefinition(
        encut_ev=500.0,
        kpoints=policy,
        extra_parameters=(gamma.protocol_centering_parameter,),
    )
    monkhorst_protocol = domain.ProtocolDefinition(
        encut_ev=500.0,
        kpoints=policy,
        extra_parameters=(monkhorst.protocol_centering_parameter,),
    )

    vasp.validate_protocol_kpoint_contract(protocol=gamma_protocol, prepared=gamma)
    assert domain.canonical_sha256(gamma_protocol) != domain.canonical_sha256(
        monkhorst_protocol
    )

    missing = domain.ProtocolDefinition(encut_ev=500.0, kpoints=policy)
    with pytest.raises(vasp.KPointPreparationError, match="exactly one"):
        vasp.validate_protocol_kpoint_contract(protocol=missing, prepared=gamma)

    with pytest.raises(vasp.KPointPreparationError, match="does not match"):
        vasp.validate_protocol_kpoint_contract(
            protocol=monkhorst_protocol,
            prepared=gamma,
        )


def test_kpoints_backed_policy_requires_materialized_file() -> None:
    prepared = vasp.prepare_kpoints(
        _snapshot(),
        policy=domain.KPointPolicy(domain.KPointPolicyKind.GAMMA_ONLY),
        system_context=_slab(),
    )

    with pytest.raises(vasp.KPointPreparationError, match="requires exactly one"):
        vasp.validate_kpoints_file_presence(
            prepared=prepared,
            kpoints_file_present=False,
        )

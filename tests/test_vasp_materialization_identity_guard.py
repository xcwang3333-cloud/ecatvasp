from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from ecatvasp import domain, vasp


def test_materialization_rejects_prepared_incar_from_different_protocol(tmp_path: Path) -> None:
    project = domain.Project(name="Block 6 identity", slug="block-6-identity")
    snapshot = domain.StructureSnapshot(
        lattice=domain.Lattice(
            vectors=((4.0, 0.0, 0.0), (0.0, 4.0, 0.0), (0.0, 0.0, 24.0))
        ),
        sites=(
            domain.StructureSite(domain.new_atom_uid(), "C", (0.0, 0.0, 0.45)),
            domain.StructureSite(domain.new_atom_uid(), "O", (0.5, 0.5, 0.55)),
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
    context = vasp.VaspSystemContext(
        vasp.VaspSystemKind.SLAB_2D,
        vacuum_axis=vasp.LatticeAxis.C,
    )
    policy = domain.KPointPolicy(domain.KPointPolicyKind.EXPLICIT_MESH, mesh=(3, 3, 1))
    prepared_poscar = vasp.prepare_poscar(snapshot)
    prepared_kpoints = vasp.prepare_kpoints(
        snapshot,
        policy=policy,
        system_context=context,
        centering=vasp.KPointCentering.GAMMA,
    )
    extras = (
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
        extra_parameters=extras,
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
            vasp.PotcarSpecEntry("C", "C", "PBE_54", "PAW_PBE C", 4.0, 400.0, "a" * 64),
            vasp.PotcarSpecEntry("O", "O", "PBE_54", "PAW_PBE O", 6.0, 400.0, "b" * 64),
        ),
        text=potcar_text,
        sha256=hashlib.sha256(potcar_text.encode()).hexdigest(),
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

    foreign_protocol = replace(protocol, sigma_ev=0.20)
    foreign_incar = vasp.prepare_incar(
        snapshot=snapshot,
        method=method,
        protocol=foreign_protocol,
        recipe=recipe,
        system_context=context,
        prepared_poscar=prepared_poscar,
        prepared_kpoints=prepared_kpoints,
        potcar_spec=potcar_spec,
        project_lock=lock,
    )

    with pytest.raises(
        vasp.InputMaterializationError,
        match="PreparedIncar does not match recompilation",
    ):
        vasp.materialize_calculation_inputs(
            project_root=tmp_path,
            calculation=calculation,
            snapshot=snapshot,
            fingerprint=fingerprint,
            recipe=recipe,
            system_context=context,
            prepared_poscar=prepared_poscar,
            prepared_incar=foreign_incar,
            prepared_kpoints=prepared_kpoints,
            potcar_spec=potcar_spec,
            project_lock=lock,
        )

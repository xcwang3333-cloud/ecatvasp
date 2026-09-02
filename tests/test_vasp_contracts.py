from dataclasses import replace

import pytest

from ecatvasp.domain import (
    Calculation,
    CalculationType,
    KPointPolicy,
    KPointPolicyKind,
)
from ecatvasp.domain.ids import (
    new_method_fingerprint_id,
    new_project_id,
    new_structure_snapshot_id,
)
from ecatvasp.vasp.contracts import (
    ECATVASP_ECAT_STANDARD,
    ECATVASP_ECAT_STANDARD_EDIFFG_EV_PER_ANGSTROM,
    LatticeAxis,
    ProjectNumericalLock,
    VaspSystemContext,
    VaspSystemKind,
)
from ecatvasp.vasp.recipes import (
    RECIPE_ADSORBATE_RELAX,
    RECIPE_ENCUT_CONVERGENCE_POINT,
    RECIPE_FULL_FREQUENCY,
    RECIPE_SELECTED_ATOM_FREQUENCY,
    VASP_RECIPE_REGISTRY,
    VaspRecipeContractError,
    get_vasp_recipe_spec,
    validate_calculation_recipe_contract,
)


def _lock(*, system_kind: VaspSystemKind = VaspSystemKind.SLAB_2D) -> ProjectNumericalLock:
    return ProjectNumericalLock(
        project_id=new_project_id(),
        system_kind=system_kind,
        core_method_hash="a" * 64,
        encut_ev=520.0,
        encut_validation_hash="b" * 64,
        kpoints=KPointPolicy(KPointPolicyKind.EXPLICIT_MESH, mesh=(3, 3, 1)),
        kpoints_validation_hash="c" * 64,
    )


def _calculation(*, recipe_id: str, calculation_type: CalculationType) -> Calculation:
    return Calculation(
        project_id=new_project_id(),
        calculation_type=calculation_type,
        input_structure_snapshot_id=new_structure_snapshot_id(),
        recipe_id=recipe_id,
        method_fingerprint_id=new_method_fingerprint_id(),
    )


def test_ecat_standard_keeps_frozen_force_threshold() -> None:
    assert ECATVASP_ECAT_STANDARD == "ECATVASP_ECAT_STANDARD"
    assert ECATVASP_ECAT_STANDARD_EDIFFG_EV_PER_ANGSTROM == -0.02


def test_slab_context_requires_explicit_vacuum_axis() -> None:
    with pytest.raises(ValueError, match="vacuum_axis"):
        VaspSystemContext(VaspSystemKind.SLAB_2D)

    slab = VaspSystemContext(VaspSystemKind.SLAB_2D, vacuum_axis=LatticeAxis.C)
    assert slab.vacuum_axis is LatticeAxis.C
    assert slab.vacuum_axis.index == 2

    with pytest.raises(ValueError, match="only valid"):
        VaspSystemContext(VaspSystemKind.MOLECULE_0D, vacuum_axis=LatticeAxis.C)


def test_project_lock_is_method_aware_and_fail_closed() -> None:
    lock = _lock()
    assert lock.standard_name == ECATVASP_ECAT_STANDARD
    assert len(lock.lock_hash) == 64

    with pytest.raises(ValueError, match="core_method_hash"):
        replace(lock, core_method_hash="bad")
    with pytest.raises(ValueError, match="encut_ev"):
        replace(lock, encut_ev=0.0)


def test_recipe_registry_distinguishes_selected_and_full_frequency() -> None:
    selected = get_vasp_recipe_spec(RECIPE_SELECTED_ATOM_FREQUENCY)
    full = get_vasp_recipe_spec(RECIPE_FULL_FREQUENCY)

    assert selected.calculation_type is CalculationType.FREQUENCY
    assert full.calculation_type is CalculationType.FREQUENCY
    assert selected.recipe_id != full.recipe_id
    assert selected.identity.recipe_id == RECIPE_SELECTED_ATOM_FREQUENCY
    assert len(VASP_RECIPE_REGISTRY) == 12


def test_production_recipe_requires_matching_project_lock() -> None:
    calculation = _calculation(
        recipe_id=RECIPE_ADSORBATE_RELAX,
        calculation_type=CalculationType.RELAX,
    )
    context = VaspSystemContext(VaspSystemKind.SLAB_2D, vacuum_axis=LatticeAxis.C)

    with pytest.raises(VaspRecipeContractError, match="requires a validated"):
        validate_calculation_recipe_contract(
            calculation=calculation,
            system_context=context,
            project_lock=None,
        )

    lock = replace(_lock(), project_id=calculation.project_id)
    spec = validate_calculation_recipe_contract(
        calculation=calculation,
        system_context=context,
        project_lock=lock,
    )
    assert spec.recipe_id == RECIPE_ADSORBATE_RELAX

    wrong_context = VaspSystemContext(VaspSystemKind.PERIODIC_3D)
    with pytest.raises(VaspRecipeContractError, match="incompatible"):
        validate_calculation_recipe_contract(
            calculation=calculation,
            system_context=wrong_context,
            project_lock=replace(lock, system_kind=VaspSystemKind.PERIODIC_3D),
        )


def test_convergence_recipe_is_allowed_before_project_lock() -> None:
    calculation = _calculation(
        recipe_id=RECIPE_ENCUT_CONVERGENCE_POINT,
        calculation_type=CalculationType.STATIC,
    )
    context = VaspSystemContext(VaspSystemKind.MOLECULE_0D)

    spec = validate_calculation_recipe_contract(
        calculation=calculation,
        system_context=context,
        project_lock=None,
    )
    assert spec.requires_project_lock is False


def test_recipe_calculation_type_mismatch_fails_closed() -> None:
    calculation = _calculation(
        recipe_id=RECIPE_ADSORBATE_RELAX,
        calculation_type=CalculationType.STATIC,
    )
    context = VaspSystemContext(VaspSystemKind.SLAB_2D, vacuum_axis=LatticeAxis.C)
    lock = replace(_lock(), project_id=calculation.project_id)

    with pytest.raises(VaspRecipeContractError, match="CalculationType"):
        validate_calculation_recipe_contract(
            calculation=calculation,
            system_context=context,
            project_lock=lock,
        )

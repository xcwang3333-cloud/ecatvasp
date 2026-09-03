"""Fail-closed identity guard for public VASP input materialization."""

from __future__ import annotations

from pathlib import Path

from ecatvasp.domain import Calculation, MethodFingerprint, SpinTreatment, StructureSnapshot
from ecatvasp.domain.method import RecipeIdentity
from ecatvasp.vasp.contracts import ProjectNumericalLock, VaspSystemContext
from ecatvasp.vasp.incar import AtomMagmom, PreparedIncar, UidMagmom, prepare_incar
from ecatvasp.vasp.kpoints import PreparedKPoints
from ecatvasp.vasp.materialization import (
    InputMaterializationError,
    MaterializedInputSet,
    materialize_calculation_inputs as _materialize_calculation_inputs,
)
from ecatvasp.vasp.poscar import PreparedPoscar
from ecatvasp.vasp.potcar import PotcarSpec


def materialize_calculation_inputs(
    *,
    project_root: Path | str,
    calculation: Calculation,
    snapshot: StructureSnapshot,
    fingerprint: MethodFingerprint,
    recipe: RecipeIdentity,
    system_context: VaspSystemContext,
    prepared_poscar: PreparedPoscar,
    prepared_incar: PreparedIncar,
    prepared_kpoints: PreparedKPoints,
    potcar_spec: PotcarSpec,
    project_lock: ProjectNumericalLock | None,
) -> MaterializedInputSet:
    """Materialize only after exact Method/Protocol/Recipe recompilation matches INCAR bytes."""

    magmom = _recover_uid_magmom(
        fingerprint=fingerprint,
        prepared_poscar=prepared_poscar,
        prepared_incar=prepared_incar,
    )
    try:
        expected_incar = prepare_incar(
            snapshot=snapshot,
            method=fingerprint.method,
            protocol=fingerprint.protocol,
            recipe=recipe,
            system_context=system_context,
            prepared_poscar=prepared_poscar,
            prepared_kpoints=prepared_kpoints,
            potcar_spec=potcar_spec,
            project_lock=project_lock,
            magmom=magmom,
        )
    except ValueError as error:
        raise InputMaterializationError(
            f"prepared inputs do not satisfy the exact fingerprint contract: {error}"
        ) from error

    if expected_incar != prepared_incar:
        raise InputMaterializationError(
            "PreparedIncar does not match recompilation from the exact MethodFingerprint"
        )

    return _materialize_calculation_inputs(
        project_root=project_root,
        calculation=calculation,
        snapshot=snapshot,
        fingerprint=fingerprint,
        recipe=recipe,
        system_context=system_context,
        prepared_poscar=prepared_poscar,
        prepared_incar=prepared_incar,
        prepared_kpoints=prepared_kpoints,
        potcar_spec=potcar_spec,
        project_lock=project_lock,
    )


def _recover_uid_magmom(
    *,
    fingerprint: MethodFingerprint,
    prepared_poscar: PreparedPoscar,
    prepared_incar: PreparedIncar,
) -> UidMagmom | None:
    treatment = fingerprint.method.spin_treatment
    if treatment is SpinTreatment.UNPOLARIZED:
        return None

    values = _magmom_values(prepared_incar)
    width = 1 if treatment is SpinTreatment.COLLINEAR else 3
    atom_uids = tuple(entry.atom_uid for entry in prepared_poscar.index_map.entries)
    if len(values) != len(atom_uids) * width:
        raise InputMaterializationError(
            "PreparedIncar MAGMOM length does not match the exact POSCAR atom mapping"
        )

    return UidMagmom(
        tuple(
            AtomMagmom(
                atom_uid,
                tuple(values[index * width : (index + 1) * width]),
            )
            for index, atom_uid in enumerate(atom_uids)
        )
    )


def _magmom_values(prepared_incar: PreparedIncar) -> tuple[float, ...]:
    matches = tuple(item for item in prepared_incar.parameters if item.name == "MAGMOM")
    if len(matches) != 1 or not isinstance(matches[0].value, str):
        raise InputMaterializationError(
            "spin-polarized PreparedIncar requires exactly one rendered MAGMOM string"
        )
    try:
        return tuple(float(item) for item in matches[0].value.split())
    except ValueError as error:
        raise InputMaterializationError("PreparedIncar MAGMOM contains non-numeric values") from error

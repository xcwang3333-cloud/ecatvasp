"""Fail-closed VASP contracts for DOS, charge-density, and LOBSTER prerequisites."""

from __future__ import annotations

import hashlib
from math import isfinite

from ecatvasp.domain.entities import StructureSnapshot
from ecatvasp.domain.method import (
    MethodDefinition,
    ParameterEntry,
    ProtocolDefinition,
    RecipeIdentity,
)
from ecatvasp.vasp.contracts import ProjectNumericalLock, VaspSystemContext
from ecatvasp.vasp.incar import (
    EffectiveIncarParameter,
    IncarSourceLayer,
    PreparedIncar,
    UidMagmom,
    prepare_incar,
)
from ecatvasp.vasp.kpoints import PreparedKPoints
from ecatvasp.vasp.poscar import PreparedPoscar
from ecatvasp.vasp.potcar import PotcarSpec
from ecatvasp.vasp.recipes import (
    RECIPE_CHARGE_DENSITY_STATIC,
    RECIPE_DOS_PREREQUISITE,
    RECIPE_GROUND_STATE_STATIC,
    RECIPE_LOBSTER_PREREQUISITE,
)

_ANALYSIS_PREREQUISITE_RECIPE_IDS = frozenset(
    {
        RECIPE_DOS_PREREQUISITE,
        RECIPE_CHARGE_DENSITY_STATIC,
        RECIPE_LOBSTER_PREREQUISITE,
    }
)


class AnalysisPrerequisitePreparationError(ValueError):
    """Raised when an analysis-prerequisite VASP input would require inference."""


def dos_recipe_parameters(*, nedos: int) -> tuple[ParameterEntry, ...]:
    """Return the explicit DOS grid control retained in Recipe identity."""

    if isinstance(nedos, bool) or not isinstance(nedos, int) or nedos < 2:
        raise AnalysisPrerequisitePreparationError("DOS NEDOS must be an integer >= 2")
    return (ParameterEntry("NEDOS", nedos),)


def lobster_recipe_parameters(*, nbands: int) -> tuple[ParameterEntry, ...]:
    """Return the explicit band count required for a LOBSTER prerequisite run."""

    if isinstance(nbands, bool) or not isinstance(nbands, int) or nbands < 1:
        raise AnalysisPrerequisitePreparationError("LOBSTER NBANDS must be a positive integer")
    return (ParameterEntry("NBANDS", nbands),)


def validate_analysis_prerequisite_recipe(recipe: RecipeIdentity) -> dict[str, int]:
    """Validate recipe-specific controls without relying on VASP defaults."""

    if recipe.recipe_id not in _ANALYSIS_PREREQUISITE_RECIPE_IDS:
        raise AnalysisPrerequisitePreparationError(
            "recipe is not a Block 9 analysis prerequisite recipe"
        )
    values = {item.name: item.value for item in recipe.parameters}
    if recipe.recipe_id == RECIPE_DOS_PREREQUISITE:
        if set(values) != {"NEDOS"}:
            raise AnalysisPrerequisitePreparationError(
                "DOSPrerequisite requires exactly one NEDOS Recipe parameter"
            )
        nedos = values["NEDOS"]
        if isinstance(nedos, bool) or not isinstance(nedos, int) or nedos < 2:
            raise AnalysisPrerequisitePreparationError("DOS NEDOS must be an integer >= 2")
        return {"NEDOS": nedos}
    if recipe.recipe_id == RECIPE_CHARGE_DENSITY_STATIC:
        if values:
            raise AnalysisPrerequisitePreparationError(
                "ChargeDensityStatic does not accept Recipe parameters in v0.3 Block 9"
            )
        return {}
    if set(values) != {"NBANDS"}:
        raise AnalysisPrerequisitePreparationError(
            "LobsterPrerequisite requires exactly one NBANDS Recipe parameter"
        )
    nbands = values["NBANDS"]
    if isinstance(nbands, bool) or not isinstance(nbands, int) or nbands < 1:
        raise AnalysisPrerequisitePreparationError("LOBSTER NBANDS must be a positive integer")
    return {"NBANDS": nbands}


def prepare_analysis_prerequisite_incar(
    *,
    snapshot: StructureSnapshot,
    method: MethodDefinition,
    protocol: ProtocolDefinition,
    recipe: RecipeIdentity,
    system_context: VaspSystemContext,
    prepared_poscar: PreparedPoscar,
    prepared_kpoints: PreparedKPoints,
    potcar_spec: PotcarSpec,
    project_lock: ProjectNumericalLock,
    magmom: UidMagmom | None = None,
) -> PreparedIncar:
    """Compile a deterministic static prerequisite INCAR from the exact fingerprint layers."""

    recipe_values = validate_analysis_prerequisite_recipe(recipe)
    if prepared_poscar.selective_flags is not None:
        raise AnalysisPrerequisitePreparationError(
            "analysis prerequisite static calculations must not use Selective Dynamics"
        )
    if recipe.recipe_id == RECIPE_LOBSTER_PREREQUISITE and protocol.isym != 0:
        raise AnalysisPrerequisitePreparationError(
            "ECAT_STANDARD LobsterPrerequisite requires fingerprinted ISYM=0"
        )

    base = prepare_incar(
        snapshot=snapshot,
        method=method,
        protocol=protocol,
        recipe=RecipeIdentity(RECIPE_GROUND_STATE_STATIC),
        system_context=system_context,
        prepared_poscar=prepared_poscar,
        prepared_kpoints=prepared_kpoints,
        potcar_spec=potcar_spec,
        project_lock=project_lock,
        magmom=magmom,
    )
    replaced = {"IBRION", "LCHARG", "LWAVE", "NSW"}
    parameters = {item.name: item for item in base.parameters if item.name not in replaced}
    common: dict[str, str | int | float | bool] = {
        "IBRION": -1,
        "NSW": 0,
    }
    if recipe.recipe_id == RECIPE_DOS_PREREQUISITE:
        common.update(
            {
                "LCHARG": False,
                "LWAVE": False,
                "LORBIT": 11,
                "NEDOS": recipe_values["NEDOS"],
            }
        )
    elif recipe.recipe_id == RECIPE_CHARGE_DENSITY_STATIC:
        common.update(
            {
                "LAECHG": False,
                "LCHARG": True,
                "LWAVE": False,
            }
        )
    else:
        common.update(
            {
                "LCHARG": False,
                "LWAVE": True,
                "NBANDS": recipe_values["NBANDS"],
            }
        )

    for name, value in common.items():
        if isinstance(value, float) and not isfinite(value):
            raise AnalysisPrerequisitePreparationError(
                f"analysis prerequisite INCAR value {name} must be finite"
            )
        parameters[name] = EffectiveIncarParameter(name, value, IncarSourceLayer.RECIPE)
    ordered = tuple(parameters[name] for name in sorted(parameters))
    text = "".join(f"{item.name} = {_format_value(item.value)}\n" for item in ordered)
    return PreparedIncar(
        structure_snapshot_id=prepared_poscar.structure_snapshot_id,
        recipe_id=recipe.recipe_id,
        parameters=ordered,
        text=text,
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def _format_value(value: str | int | float | bool) -> str:
    if isinstance(value, bool):
        return ".TRUE." if value else ".FALSE."
    if isinstance(value, float):
        if abs(value) < 5e-16:
            value = 0.0
        return f"{value:.12g}"
    return str(value)

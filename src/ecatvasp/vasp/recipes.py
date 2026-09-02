"""Canonical v0.3 VASP recipe identities and context validation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from ecatvasp.domain.calculation import Calculation, CalculationType
from ecatvasp.domain.method import RecipeIdentity
from ecatvasp.vasp.contracts import ProjectNumericalLock, VaspSystemContext, VaspSystemKind

RECIPE_SLAB_RELAX = "ECatVASP.VASP.SlabRelax"
RECIPE_ADSORBATE_RELAX = "ECatVASP.VASP.AdsorbateRelax"
RECIPE_GAS_RELAX = "ECatVASP.VASP.GasRelax"
RECIPE_GROUND_STATE_STATIC = "ECatVASP.VASP.GroundStateStatic"
RECIPE_SELECTED_ATOM_FREQUENCY = "ECatVASP.VASP.SelectedAtomFrequency"
RECIPE_FULL_FREQUENCY = "ECatVASP.VASP.FullFrequency"
RECIPE_GAS_FREQUENCY = "ECatVASP.VASP.GasFrequency"
RECIPE_DOS_PREREQUISITE = "ECatVASP.VASP.DOSPrerequisite"
RECIPE_CHARGE_DENSITY_STATIC = "ECatVASP.VASP.ChargeDensityStatic"
RECIPE_LOBSTER_PREREQUISITE = "ECatVASP.VASP.LobsterPrerequisite"
RECIPE_ENCUT_CONVERGENCE_POINT = "ECatVASP.VASP.ENCUTConvergencePoint"
RECIPE_KPOINT_CONVERGENCE_POINT = "ECatVASP.VASP.KPointConvergencePoint"


class VaspRecipeContractError(ValueError):
    """Raised when a Calculation cannot satisfy its declared VASP recipe contract."""


@dataclass(frozen=True, slots=True)
class VaspRecipeSpec:
    """Stable recipe identity mapped to a scientific CalculationType and context."""

    recipe_id: str
    calculation_type: CalculationType
    allowed_system_kinds: tuple[VaspSystemKind, ...]
    requires_project_lock: bool = True
    version: str = "1"

    def __post_init__(self) -> None:
        if not self.recipe_id.strip():
            raise ValueError("recipe_id must not be blank")
        if not self.version.strip():
            raise ValueError("version must not be blank")
        if not self.allowed_system_kinds:
            raise ValueError("allowed_system_kinds must not be empty")
        if len(self.allowed_system_kinds) != len(set(self.allowed_system_kinds)):
            raise ValueError("allowed_system_kinds must be unique")

    @property
    def identity(self) -> RecipeIdentity:
        """Return the parameter-free stable RecipeIdentity for this contract."""

        return RecipeIdentity(recipe_id=self.recipe_id, version=self.version)


_ALL_SYSTEMS = (
    VaspSystemKind.SLAB_2D,
    VaspSystemKind.MOLECULE_0D,
    VaspSystemKind.PERIODIC_3D,
)
_SOLID_SYSTEMS = (VaspSystemKind.SLAB_2D, VaspSystemKind.PERIODIC_3D)

VASP_RECIPE_SPECS: tuple[VaspRecipeSpec, ...] = (
    VaspRecipeSpec(RECIPE_SLAB_RELAX, CalculationType.RELAX, (VaspSystemKind.SLAB_2D,)),
    VaspRecipeSpec(
        RECIPE_ADSORBATE_RELAX,
        CalculationType.RELAX,
        (VaspSystemKind.SLAB_2D,),
    ),
    VaspRecipeSpec(
        RECIPE_GAS_RELAX,
        CalculationType.GAS_RELAX,
        (VaspSystemKind.MOLECULE_0D,),
    ),
    VaspRecipeSpec(RECIPE_GROUND_STATE_STATIC, CalculationType.STATIC, _ALL_SYSTEMS),
    VaspRecipeSpec(
        RECIPE_SELECTED_ATOM_FREQUENCY,
        CalculationType.FREQUENCY,
        (VaspSystemKind.SLAB_2D,),
    ),
    VaspRecipeSpec(RECIPE_FULL_FREQUENCY, CalculationType.FREQUENCY, _SOLID_SYSTEMS),
    VaspRecipeSpec(
        RECIPE_GAS_FREQUENCY,
        CalculationType.GAS_FREQUENCY,
        (VaspSystemKind.MOLECULE_0D,),
    ),
    VaspRecipeSpec(RECIPE_DOS_PREREQUISITE, CalculationType.DOS_STATIC, _SOLID_SYSTEMS),
    VaspRecipeSpec(
        RECIPE_CHARGE_DENSITY_STATIC,
        CalculationType.CHARGE_STATIC,
        _ALL_SYSTEMS,
    ),
    VaspRecipeSpec(
        RECIPE_LOBSTER_PREREQUISITE,
        CalculationType.LOBSTER_PREREQUISITE,
        _SOLID_SYSTEMS,
    ),
    VaspRecipeSpec(
        RECIPE_ENCUT_CONVERGENCE_POINT,
        CalculationType.STATIC,
        _ALL_SYSTEMS,
        requires_project_lock=False,
    ),
    VaspRecipeSpec(
        RECIPE_KPOINT_CONVERGENCE_POINT,
        CalculationType.STATIC,
        _SOLID_SYSTEMS,
        requires_project_lock=False,
    ),
)

VASP_RECIPE_REGISTRY: Mapping[str, VaspRecipeSpec] = MappingProxyType(
    {spec.recipe_id: spec for spec in VASP_RECIPE_SPECS}
)

if len(VASP_RECIPE_REGISTRY) != len(VASP_RECIPE_SPECS):
    raise RuntimeError("VASP recipe IDs must be unique")


def get_vasp_recipe_spec(recipe_id: str) -> VaspRecipeSpec:
    """Resolve a canonical v0.3 recipe or fail closed for unknown identities."""

    try:
        return VASP_RECIPE_REGISTRY[recipe_id]
    except KeyError as error:
        raise VaspRecipeContractError(f"unknown VASP recipe: {recipe_id}") from error


def validate_calculation_recipe_contract(
    *,
    calculation: Calculation,
    system_context: VaspSystemContext,
    project_lock: ProjectNumericalLock | None,
) -> VaspRecipeSpec:
    """Validate task, system context, and project lock without generating inputs."""

    spec = get_vasp_recipe_spec(calculation.recipe_id)
    if calculation.calculation_type is not spec.calculation_type:
        raise VaspRecipeContractError(
            "CalculationType does not match the canonical VASP recipe contract"
        )
    if system_context.kind not in spec.allowed_system_kinds:
        raise VaspRecipeContractError(
            f"recipe {spec.recipe_id} is incompatible with {system_context.kind.value}"
        )
    if spec.requires_project_lock and project_lock is None:
        raise VaspRecipeContractError(
            f"recipe {spec.recipe_id} requires a validated project numerical lock"
        )
    if project_lock is not None:
        if project_lock.project_id != calculation.project_id:
            raise VaspRecipeContractError("project numerical lock belongs to another Project")
        if project_lock.system_kind is not system_context.kind:
            raise VaspRecipeContractError(
                "project numerical lock system kind does not match the calculation context"
            )
    return spec

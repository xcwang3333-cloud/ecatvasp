"""Deterministic scientific INCAR preparation for the ECAT_STANDARD core recipes."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import StrEnum
from math import isfinite

from ecatvasp.domain.entities import StructureSnapshot
from ecatvasp.domain.ids import AtomUid, StructureSnapshotId
from ecatvasp.domain.method import (
    MethodDefinition,
    ParameterEntry,
    ProtocolDefinition,
    RecipeIdentity,
    SpinTreatment,
    canonical_sha256,
)
from ecatvasp.vasp.contracts import (
    ECATVASP_ECAT_STANDARD,
    ECATVASP_ECAT_STANDARD_EDIFFG_EV_PER_ANGSTROM,
    ProjectNumericalLock,
    VaspSystemContext,
    VaspSystemKind,
)
from ecatvasp.vasp.kpoints import PreparedKPoints, validate_protocol_kpoint_contract
from ecatvasp.vasp.poscar import PreparedPoscar
from ecatvasp.vasp.potcar import PotcarSpec
from ecatvasp.vasp.recipes import (
    RECIPE_ADSORBATE_RELAX,
    RECIPE_CHARGE_DENSITY_STATIC,
    RECIPE_DOS_PREREQUISITE,
    RECIPE_ENCUT_CONVERGENCE_POINT,
    RECIPE_FULL_FREQUENCY,
    RECIPE_GAS_FREQUENCY,
    RECIPE_GAS_RELAX,
    RECIPE_GROUND_STATE_STATIC,
    RECIPE_KPOINT_CONVERGENCE_POINT,
    RECIPE_LOBSTER_PREREQUISITE,
    RECIPE_SELECTED_ATOM_FREQUENCY,
    RECIPE_SLAB_RELAX,
    get_vasp_recipe_spec,
)

ECATVASP_MAGMOM_UID_HASH = "ECATVASP_MAGMOM_UID_HASH"
ECATVASP_DIPOLE_AXIS = "ECATVASP_DIPOLE_AXIS"
ECAT_STANDARD_ALGO = "Normal"


class IncarPreparationError(ValueError):
    """Raised when a scientific INCAR cannot be prepared without guessing."""


class IncarSourceLayer(StrEnum):
    """Scientific source layer for one effective INCAR parameter."""

    METHOD = "method"
    PROTOCOL = "protocol"
    RECIPE = "recipe"
    CONTEXT = "context"


IncarValue = str | int | float | bool


@dataclass(frozen=True, slots=True)
class EffectiveIncarParameter:
    """One deterministic effective INCAR parameter with its scientific source layer."""

    name: str
    value: IncarValue
    source: IncarSourceLayer

    def __post_init__(self) -> None:
        if not self.name.strip() or self.name != self.name.upper():
            raise ValueError("INCAR parameter names must be non-blank uppercase strings")
        if isinstance(self.value, float) and not isfinite(self.value):
            raise ValueError("INCAR float values must be finite")


@dataclass(frozen=True, slots=True)
class AtomMagmom:
    """UID-addressed collinear or noncollinear magnetic initialization."""

    atom_uid: AtomUid
    components: tuple[float, ...]

    def __post_init__(self) -> None:
        if len(self.components) not in (1, 3):
            raise ValueError("MAGMOM components must contain one or three values")
        if any(not isfinite(value) for value in self.components):
            raise ValueError("MAGMOM components must be finite")


@dataclass(frozen=True, slots=True)
class UidMagmom:
    """Permanent-identity magnetic initialization independent of POSCAR ordering."""

    entries: tuple[AtomMagmom, ...]
    mapping_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if not self.entries:
            raise ValueError("UidMagmom requires at least one atom entry")
        ordered = tuple(sorted(self.entries, key=lambda item: str(item.atom_uid)))
        atom_uids = tuple(item.atom_uid for item in ordered)
        if len(atom_uids) != len(set(atom_uids)):
            raise ValueError("UidMagmom atom_uids must be unique")
        object.__setattr__(self, "entries", ordered)
        object.__setattr__(self, "mapping_hash", canonical_sha256(ordered))

    @property
    def protocol_parameter(self) -> ParameterEntry:
        """Return the Protocol initialization parameter binding this UID map."""

        return ParameterEntry(ECATVASP_MAGMOM_UID_HASH, self.mapping_hash)


@dataclass(frozen=True, slots=True)
class PreparedIncar:
    """Immutable deterministic scientific INCAR preparation result."""

    structure_snapshot_id: StructureSnapshotId
    recipe_id: str
    parameters: tuple[EffectiveIncarParameter, ...]
    text: str
    sha256: str
    identity_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if not self.parameters:
            raise ValueError("PreparedIncar requires at least one parameter")
        names = tuple(item.name for item in self.parameters)
        if names != tuple(sorted(names)):
            raise ValueError("PreparedIncar parameters must be sorted by name")
        if len(names) != len(set(names)):
            raise ValueError("PreparedIncar parameter names must be unique")
        expected_text = "".join(
            f"{item.name} = {_format_incar_value(item.value)}\n" for item in self.parameters
        )
        if self.text != expected_text:
            raise ValueError("PreparedIncar text does not match effective parameters")
        expected_sha = hashlib.sha256(self.text.encode("utf-8")).hexdigest()
        if self.sha256 != expected_sha:
            raise ValueError("INCAR sha256 does not match text content")
        object.__setattr__(
            self,
            "identity_hash",
            canonical_sha256(
                {
                    "structure_snapshot_id": self.structure_snapshot_id,
                    "recipe_id": self.recipe_id,
                    "parameters": self.parameters,
                    "sha256": self.sha256,
                }
            ),
        )


_CORE_RECIPE_DEFAULTS: dict[str, dict[str, IncarValue]] = {
    RECIPE_SLAB_RELAX: {
        "IBRION": 2,
        "ISIF": 2,
        "LCHARG": False,
        "LWAVE": False,
        "NSW": 200,
    },
    RECIPE_ADSORBATE_RELAX: {
        "IBRION": 2,
        "ISIF": 2,
        "LCHARG": False,
        "LWAVE": False,
        "NSW": 200,
    },
    RECIPE_GAS_RELAX: {
        "IBRION": 2,
        "ISIF": 2,
        "LCHARG": False,
        "LWAVE": False,
        "NSW": 200,
    },
    RECIPE_GROUND_STATE_STATIC: {
        "IBRION": -1,
        "LCHARG": True,
        "LWAVE": False,
        "NSW": 0,
    },
    RECIPE_ENCUT_CONVERGENCE_POINT: {
        "IBRION": -1,
        "LCHARG": False,
        "LWAVE": False,
        "NSW": 0,
    },
    RECIPE_KPOINT_CONVERGENCE_POINT: {
        "IBRION": -1,
        "LCHARG": False,
        "LWAVE": False,
        "NSW": 0,
    },
}

_DEFERRED_RECIPE_IDS = {
    RECIPE_SELECTED_ATOM_FREQUENCY,
    RECIPE_FULL_FREQUENCY,
    RECIPE_GAS_FREQUENCY,
    RECIPE_DOS_PREREQUISITE,
    RECIPE_CHARGE_DENSITY_STATIC,
    RECIPE_LOBSTER_PREREQUISITE,
}

_RECIPE_OVERRIDE_KEYS = frozenset({"IBRION", "ISIF", "LCHARG", "LWAVE", "NSW"})
_PROTOCOL_RAW_KEYS = frozenset({"ALGO", "DIPOL", "IDIPOL", "LASPH"})
_INTERNAL_PROTOCOL_KEYS = frozenset({"ECATVASP_KPOINT_CENTERING", ECATVASP_DIPOLE_AXIS})


def ecat_standard_protocol_parameters(
    *, vacuum_axis: str | None = None
) -> tuple[ParameterEntry, ...]:
    """Return fingerprinted ECAT_STANDARD Protocol extras owned by Block 5."""

    items = [ParameterEntry("ALGO", ECAT_STANDARD_ALGO), ParameterEntry("LASPH", True)]
    if vacuum_axis is not None:
        items.append(ParameterEntry(ECATVASP_DIPOLE_AXIS, vacuum_axis))
    return tuple(sorted(items, key=lambda item: item.name))


def prepare_incar(
    *,
    snapshot: StructureSnapshot,
    method: MethodDefinition,
    protocol: ProtocolDefinition,
    recipe: RecipeIdentity,
    system_context: VaspSystemContext,
    prepared_poscar: PreparedPoscar,
    prepared_kpoints: PreparedKPoints,
    potcar_spec: PotcarSpec,
    project_lock: ProjectNumericalLock | None,
    magmom: UidMagmom | None = None,
) -> PreparedIncar:
    """Compile scientific Method/Protocol/Recipe identity into deterministic INCAR text."""

    spec = get_vasp_recipe_spec(recipe.recipe_id)
    if recipe.version != spec.version:
        raise IncarPreparationError("RecipeIdentity version does not match the canonical recipe")
    if system_context.kind not in spec.allowed_system_kinds:
        raise IncarPreparationError("recipe is incompatible with the supplied VASP system context")
    if recipe.recipe_id in _DEFERRED_RECIPE_IDS:
        raise IncarPreparationError(
            "recipe-specific INCAR semantics are deferred to v0.3 Block 8/9"
        )
    if recipe.recipe_id not in _CORE_RECIPE_DEFAULTS:
        raise IncarPreparationError("recipe has no Block 5 INCAR contract")

    _validate_shared_inputs(
        snapshot=snapshot,
        method=method,
        protocol=protocol,
        recipe=recipe,
        system_context=system_context,
        prepared_poscar=prepared_poscar,
        prepared_kpoints=prepared_kpoints,
        potcar_spec=potcar_spec,
        project_lock=project_lock,
        requires_project_lock=spec.requires_project_lock,
    )

    parameters: dict[str, EffectiveIncarParameter] = {}
    _add_method_parameters(
        parameters,
        method=method,
        prepared_poscar=prepared_poscar,
        potcar_spec=potcar_spec,
    )
    _add_protocol_parameters(
        parameters,
        snapshot=snapshot,
        protocol=protocol,
        system_context=system_context,
        prepared_kpoints=prepared_kpoints,
        method=method,
    )
    _add_spin_parameters(
        parameters,
        method=method,
        protocol=protocol,
        prepared_poscar=prepared_poscar,
        magmom=magmom,
    )
    _add_recipe_parameters(parameters, recipe=recipe)
    _validate_ecat_standard(
        parameters=parameters,
        protocol=protocol,
        recipe=recipe,
        system_context=system_context,
        project_lock=project_lock,
    )

    ordered = tuple(parameters[name] for name in sorted(parameters))
    text = "".join(f"{item.name} = {_format_incar_value(item.value)}\n" for item in ordered)
    return PreparedIncar(
        structure_snapshot_id=prepared_poscar.structure_snapshot_id,
        recipe_id=recipe.recipe_id,
        parameters=ordered,
        text=text,
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def _validate_shared_inputs(
    *,
    snapshot: StructureSnapshot,
    method: MethodDefinition,
    protocol: ProtocolDefinition,
    recipe: RecipeIdentity,
    system_context: VaspSystemContext,
    prepared_poscar: PreparedPoscar,
    prepared_kpoints: PreparedKPoints,
    potcar_spec: PotcarSpec,
    project_lock: ProjectNumericalLock | None,
    requires_project_lock: bool,
) -> None:
    if snapshot.id != prepared_poscar.structure_snapshot_id:
        raise IncarPreparationError("StructureSnapshot does not match PreparedPoscar")
    if prepared_poscar.structure_snapshot_id != prepared_kpoints.structure_snapshot_id:
        raise IncarPreparationError("POSCAR and k-point preparations target different snapshots")
    if prepared_kpoints.system_context != system_context:
        raise IncarPreparationError("prepared k-point context does not match INCAR context")
    if potcar_spec.species_order != prepared_poscar.species_order:
        raise IncarPreparationError("POTCAR spec species order does not match PreparedPoscar")
    core_method_hash = canonical_sha256(method)
    if potcar_spec.core_method_hash != core_method_hash:
        raise IncarPreparationError("POTCAR spec core method does not match MethodDefinition")
    validate_protocol_kpoint_contract(protocol=protocol, prepared=prepared_kpoints)

    if requires_project_lock and project_lock is None:
        raise IncarPreparationError("production recipe requires a validated project numerical lock")
    if project_lock is not None:
        if project_lock.core_method_hash != core_method_hash:
            raise IncarPreparationError("project lock core method does not match MethodDefinition")
        if project_lock.system_kind is not system_context.kind:
            raise IncarPreparationError("project lock system kind does not match INCAR context")
        if project_lock.encut_ev != protocol.encut_ev:
            raise IncarPreparationError("Protocol ENCUT does not match the project numerical lock")
        if project_lock.kpoints != protocol.kpoints:
            raise IncarPreparationError("Protocol k-point policy does not match the project lock")

    if method.engine.lower() != "vasp":
        raise IncarPreparationError(
            "Block 5 INCAR preparation requires MethodDefinition.engine=vasp"
        )
    if method.extra_parameters:
        raise IncarPreparationError(
            "Block 5 does not silently pass through Method extra_parameters; "
            "use structured Method fields"
        )
    unknown_recipe = tuple(
        item.name for item in recipe.parameters if item.name not in _RECIPE_OVERRIDE_KEYS
    )
    if unknown_recipe:
        raise IncarPreparationError(
            f"unsupported Block 5 Recipe parameters: {', '.join(sorted(unknown_recipe))}"
        )


def _add_method_parameters(
    parameters: dict[str, EffectiveIncarParameter],
    *,
    method: MethodDefinition,
    prepared_poscar: PreparedPoscar,
    potcar_spec: PotcarSpec,
) -> None:
    xc_tags = _xc_parameters(method.xc_functional)
    for name, value in xc_tags.items():
        _put(parameters, name, value, IncarSourceLayer.METHOD)

    dispersion = method.dispersion_model
    if dispersion is None:
        raise IncarPreparationError(
            "vdW policy is unresolved; set dispersion_model explicitly to NONE or IVDW=<n>"
        )
    normalized_dispersion = dispersion.strip().upper()
    if normalized_dispersion == "NONE":
        pass
    elif normalized_dispersion in {"IVDW=11", "IVDW=12"}:
        _put(parameters, "IVDW", int(normalized_dispersion.split("=")[1]), IncarSourceLayer.METHOD)
    else:
        raise IncarPreparationError(
            "unsupported dispersion_model; Block 5 accepts NONE, IVDW=11, or IVDW=12"
        )

    if method.dft_u:
        _put(parameters, "LDAU", True, IncarSourceLayer.METHOD)
        _put(parameters, "LDAUTYPE", 2, IncarSourceLayer.METHOD)
        settings = {item.element: item for item in method.dft_u}
        missing = tuple(
            element for element in settings if element not in prepared_poscar.species_order
        )
        if missing:
            raise IncarPreparationError("DFT+U element is absent from PreparedPoscar species")
        ldau_l = []
        ldau_u = []
        ldau_j = []
        for element in prepared_poscar.species_order:
            setting = settings.get(element)
            ldau_l.append(str(setting.orbital_l if setting is not None else -1))
            ldau_u.append(_format_float(setting.u_ev if setting is not None else 0.0))
            ldau_j.append(_format_float(setting.j_ev if setting is not None else 0.0))
        _put(parameters, "LDAUL", " ".join(ldau_l), IncarSourceLayer.METHOD)
        _put(parameters, "LDAUU", " ".join(ldau_u), IncarSourceLayer.METHOD)
        _put(parameters, "LDAUJ", " ".join(ldau_j), IncarSourceLayer.METHOD)

    solvation = method.solvation_model
    if solvation is not None and solvation.strip().upper() != "NONE":
        if solvation.strip().upper() != "VASPSOL":
            raise IncarPreparationError("unsupported solvation_model; Block 5 supports VASPsol")
        _put(parameters, "LSOL", True, IncarSourceLayer.METHOD)

    if method.charge_e != 0.0:
        neutral_nelect = _neutral_nelect(prepared_poscar=prepared_poscar, potcar_spec=potcar_spec)
        nelect = neutral_nelect - method.charge_e
        if nelect <= 0:
            raise IncarPreparationError("resolved NELECT must remain positive")
        _put(parameters, "NELECT", nelect, IncarSourceLayer.METHOD)


def _add_protocol_parameters(
    parameters: dict[str, EffectiveIncarParameter],
    *,
    snapshot: StructureSnapshot,
    protocol: ProtocolDefinition,
    system_context: VaspSystemContext,
    prepared_kpoints: PreparedKPoints,
    method: MethodDefinition,
) -> None:
    _put(parameters, "ENCUT", protocol.encut_ev, IncarSourceLayer.PROTOCOL)
    _put(parameters, "PREC", protocol.precision, IncarSourceLayer.PROTOCOL)
    _put(parameters, "EDIFF", protocol.ediff_ev, IncarSourceLayer.PROTOCOL)
    _put(parameters, "ISMEAR", protocol.ismear, IncarSourceLayer.PROTOCOL)
    _put(parameters, "SIGMA", protocol.sigma_ev, IncarSourceLayer.PROTOCOL)
    _put(parameters, "LREAL", protocol.lreal, IncarSourceLayer.PROTOCOL)
    if protocol.isym is not None:
        _put(parameters, "ISYM", protocol.isym, IncarSourceLayer.PROTOCOL)

    if prepared_kpoints.kspacing_inv_angstrom is not None:
        assert prepared_kpoints.kgamma is not None
        _put(
            parameters,
            "KSPACING",
            prepared_kpoints.kspacing_inv_angstrom,
            IncarSourceLayer.PROTOCOL,
        )
        _put(parameters, "KGAMMA", prepared_kpoints.kgamma, IncarSourceLayer.PROTOCOL)

    extras = {item.name: item.value for item in protocol.extra_parameters}
    unknown = tuple(
        name
        for name in extras
        if name not in _PROTOCOL_RAW_KEYS and name not in _INTERNAL_PROTOCOL_KEYS
    )
    if unknown:
        raise IncarPreparationError(
            f"unsupported Block 5 Protocol extra parameters: {', '.join(sorted(unknown))}"
        )
    for name in ("ALGO", "LASPH"):
        if name in extras:
            value = extras[name]
            if not isinstance(value, (str, bool)):
                raise IncarPreparationError(f"Protocol {name} has an invalid scalar type")
            _put(parameters, name, value, IncarSourceLayer.PROTOCOL)

    idipol = _resolve_dipole(
        parameters, protocol=protocol, system_context=system_context, extras=extras
    )
    if method.electric_field_ev_per_angstrom is not None:
        if system_context.kind is not VaspSystemKind.SLAB_2D or idipol not in (1, 2, 3):
            raise IncarPreparationError(
                "EFIELD currently requires a slab context with a resolved IDIPOL axis"
            )
        _put(
            parameters,
            "EFIELD",
            method.electric_field_ev_per_angstrom,
            IncarSourceLayer.METHOD,
        )

    if idipol is not None:
        _validate_dipole_cell_geometry(
            snapshot=snapshot, system_context=system_context, idipol=idipol
        )
        if method.charge_e != 0.0:
            _validate_charged_ldipol_cell(snapshot)


def _add_spin_parameters(
    parameters: dict[str, EffectiveIncarParameter],
    *,
    method: MethodDefinition,
    protocol: ProtocolDefinition,
    prepared_poscar: PreparedPoscar,
    magmom: UidMagmom | None,
) -> None:
    init_matches = tuple(
        item for item in protocol.initialization_parameters if item.name == ECATVASP_MAGMOM_UID_HASH
    )
    unknown_init = tuple(
        item.name
        for item in protocol.initialization_parameters
        if item.name != ECATVASP_MAGMOM_UID_HASH
    )
    if unknown_init:
        raise IncarPreparationError(
            f"unsupported Protocol initialization parameters: {', '.join(sorted(unknown_init))}"
        )

    if method.spin_treatment is SpinTreatment.UNPOLARIZED:
        if magmom is not None or init_matches:
            raise IncarPreparationError("UNPOLARIZED method must not carry MAGMOM initialization")
        _put(parameters, "ISPIN", 1, IncarSourceLayer.METHOD)
        return

    if magmom is None:
        raise IncarPreparationError("spin-polarized methods require explicit UID-addressed MAGMOM")
    if len(init_matches) != 1 or init_matches[0].value != magmom.mapping_hash:
        raise IncarPreparationError(
            "Protocol initialization must contain the exact ECATVASP_MAGMOM_UID_HASH"
        )

    by_uid = {item.atom_uid: item for item in magmom.entries}
    poscar_uids = tuple(item.atom_uid for item in prepared_poscar.index_map.entries)
    if set(by_uid) != set(poscar_uids):
        raise IncarPreparationError("MAGMOM UID mapping must exactly cover PreparedPoscar atoms")

    if method.spin_treatment is SpinTreatment.COLLINEAR:
        if method.soc:
            raise IncarPreparationError("COLLINEAR method cannot enable SOC")
        if any(len(by_uid[uid].components) != 1 for uid in poscar_uids):
            raise IncarPreparationError("COLLINEAR MAGMOM requires one component per atom")
        _put(parameters, "ISPIN", 2, IncarSourceLayer.METHOD)
        magmom_text = " ".join(_format_float(by_uid[uid].components[0]) for uid in poscar_uids)
    else:
        if any(len(by_uid[uid].components) != 3 for uid in poscar_uids):
            raise IncarPreparationError("NONCOLLINEAR MAGMOM requires three components per atom")
        _put(parameters, "LNONCOLLINEAR", True, IncarSourceLayer.METHOD)
        if method.soc:
            _put(parameters, "LSORBIT", True, IncarSourceLayer.METHOD)
        magmom_text = " ".join(
            _format_float(component)
            for uid in poscar_uids
            for component in by_uid[uid].components
        )
    _put(parameters, "MAGMOM", magmom_text, IncarSourceLayer.PROTOCOL)


def _add_recipe_parameters(
    parameters: dict[str, EffectiveIncarParameter],
    *,
    recipe: RecipeIdentity,
) -> None:
    values = dict(_CORE_RECIPE_DEFAULTS[recipe.recipe_id])
    values.update({item.name: item.value for item in recipe.parameters})
    for name, value in values.items():
        _validate_recipe_value(name=name, value=value)
        assert isinstance(value, (str, int, float, bool))
        _put(parameters, name, value, IncarSourceLayer.RECIPE)


def _validate_recipe_value(*, name: str, value: object) -> None:
    if name in {"LCHARG", "LWAVE"}:
        if not isinstance(value, bool):
            raise IncarPreparationError(f"Recipe parameter {name} must be boolean")
        return
    if name in {"IBRION", "ISIF", "NSW"}:
        if isinstance(value, bool) or not isinstance(value, int):
            raise IncarPreparationError(f"Recipe parameter {name} must be an integer")
        if name == "NSW" and value < 0:
            raise IncarPreparationError("Recipe parameter NSW must be non-negative")
        return
    raise IncarPreparationError(f"unsupported Block 5 Recipe parameter: {name}")


def _validate_ecat_standard(
    *,
    parameters: dict[str, EffectiveIncarParameter],
    protocol: ProtocolDefinition,
    recipe: RecipeIdentity,
    system_context: VaspSystemContext,
    project_lock: ProjectNumericalLock | None,
) -> None:
    standard_name = (
        project_lock.standard_name
        if project_lock is not None
        else ECATVASP_ECAT_STANDARD
    )
    if standard_name != ECATVASP_ECAT_STANDARD:
        raise IncarPreparationError("Block 5 currently supports only ECATVASP_ECAT_STANDARD")
    if protocol.precision != "Accurate":
        raise IncarPreparationError("ECAT_STANDARD requires PREC=Accurate")
    if protocol.lreal is not False:
        raise IncarPreparationError("ECAT_STANDARD requires LREAL=False")
    if parameters.get("LASPH") is None or parameters["LASPH"].value is not True:
        raise IncarPreparationError("ECAT_STANDARD requires fingerprinted LASPH=True")
    if parameters.get("ALGO") is None or parameters["ALGO"].value != ECAT_STANDARD_ALGO:
        raise IncarPreparationError("ECAT_STANDARD requires fingerprinted ALGO=Normal")

    if recipe.recipe_id in {RECIPE_SLAB_RELAX, RECIPE_ADSORBATE_RELAX, RECIPE_GAS_RELAX}:
        if protocol.ediffg_ev_per_angstrom != ECATVASP_ECAT_STANDARD_EDIFFG_EV_PER_ANGSTROM:
            raise IncarPreparationError(
                "ECAT_STANDARD relaxations require EDIFFG=-0.02 eV/Angstrom"
            )
        _put(
            parameters,
            "EDIFFG",
            protocol.ediffg_ev_per_angstrom,
            IncarSourceLayer.PROTOCOL,
        )

    if system_context.kind is VaspSystemKind.SLAB_2D and protocol.dipole_policy.value != "off":
        assert system_context.vacuum_axis is not None
        axis_matches = tuple(
            item
            for item in protocol.extra_parameters
            if item.name == ECATVASP_DIPOLE_AXIS
        )
        if len(axis_matches) != 1 or axis_matches[0].value != system_context.vacuum_axis.value:
            raise IncarPreparationError(
                "slab dipole correction requires fingerprinted ECATVASP_DIPOLE_AXIS"
            )


def _resolve_dipole(
    parameters: dict[str, EffectiveIncarParameter],
    *,
    protocol: ProtocolDefinition,
    system_context: VaspSystemContext,
    extras: dict[str, object],
) -> int | None:
    policy = protocol.dipole_policy.value
    if policy == "off":
        if ECATVASP_DIPOLE_AXIS in extras:
            raise IncarPreparationError(
                "DipolePolicy.OFF conflicts with ECATVASP_DIPOLE_AXIS"
            )
        if any(name in extras for name in ("IDIPOL", "DIPOL")):
            raise IncarPreparationError("DipolePolicy.OFF conflicts with IDIPOL/DIPOL extras")
        _put(parameters, "LDIPOL", False, IncarSourceLayer.PROTOCOL)
        return None

    if system_context.kind is VaspSystemKind.PERIODIC_3D:
        raise IncarPreparationError("periodic 3D systems must use DipolePolicy.OFF")

    _put(parameters, "LDIPOL", True, IncarSourceLayer.PROTOCOL)
    if policy == "auto":
        if any(name in extras for name in ("IDIPOL", "DIPOL")):
            raise IncarPreparationError("DipolePolicy.AUTO must not carry explicit IDIPOL/DIPOL")
        if system_context.kind is VaspSystemKind.SLAB_2D:
            assert system_context.vacuum_axis is not None
            idipol = system_context.vacuum_axis.axis_index + 1
        else:
            idipol = 4
        _put(parameters, "IDIPOL", idipol, IncarSourceLayer.CONTEXT)
        return idipol

    if policy != "explicit":
        raise IncarPreparationError("unknown dipole policy")
    idipol_value = extras.get("IDIPOL")
    dipol_value = extras.get("DIPOL")
    if (
        isinstance(idipol_value, bool)
        or not isinstance(idipol_value, int)
        or idipol_value not in (1, 2, 3, 4)
    ):
        raise IncarPreparationError("DipolePolicy.EXPLICIT requires integer IDIPOL=1..4")
    if not isinstance(dipol_value, str):
        raise IncarPreparationError(
            "DipolePolicy.EXPLICIT requires DIPOL as three direct coordinates"
        )
    _validate_dipol_text(dipol_value)
    if system_context.kind is VaspSystemKind.SLAB_2D:
        assert system_context.vacuum_axis is not None
        if idipol_value != system_context.vacuum_axis.axis_index + 1:
            raise IncarPreparationError("slab explicit IDIPOL must match the declared vacuum axis")
    _put(parameters, "IDIPOL", idipol_value, IncarSourceLayer.PROTOCOL)
    _put(parameters, "DIPOL", dipol_value, IncarSourceLayer.PROTOCOL)
    return idipol_value


def _validate_dipole_cell_geometry(
    *,
    snapshot: StructureSnapshot,
    system_context: VaspSystemContext,
    idipol: int,
) -> None:
    vectors = snapshot.lattice.vectors
    if idipol in (1, 2, 3):
        axis = idipol - 1
        for other in range(3):
            if other == axis:
                continue
            if not _orthogonal(vectors[axis], vectors[other]):
                raise IncarPreparationError(
                    "LDIPOL correction axis must be orthogonal to the other lattice vectors"
                )
        return
    if idipol == 4 and system_context.kind is VaspSystemKind.MOLECULE_0D:
        pairs = ((0, 1), (0, 2), (1, 2))
        if any(not _orthogonal(vectors[first], vectors[second]) for first, second in pairs):
            raise IncarPreparationError(
                "molecule IDIPOL=4 requires an orthogonal simulation cell"
            )


def _validate_charged_ldipol_cell(snapshot: StructureSnapshot) -> None:
    """Enforce VASP's current cubic-cell restriction for charged LDIPOL calculations."""

    vectors = snapshot.lattice.vectors
    pairs = ((0, 1), (0, 2), (1, 2))
    if any(not _orthogonal(vectors[first], vectors[second]) for first, second in pairs):
        raise IncarPreparationError("charged LDIPOL requires a cubic supercell in current VASP")
    lengths = tuple(sum(value * value for value in vector) ** 0.5 for vector in vectors)
    scale = max(lengths)
    if scale <= 0 or max(lengths) - min(lengths) > 1e-10 * scale:
        raise IncarPreparationError("charged LDIPOL requires a cubic supercell in current VASP")


def _orthogonal(
    first: tuple[float, float, float],
    second: tuple[float, float, float],
) -> bool:
    dot = sum(a * b for a, b in zip(first, second, strict=True))
    first_norm = sum(value * value for value in first) ** 0.5
    second_norm = sum(value * value for value in second) ** 0.5
    if first_norm <= 0 or second_norm <= 0:
        return False
    return abs(dot) <= 1e-10 * first_norm * second_norm


def _xc_parameters(xc_functional: str) -> dict[str, IncarValue]:
    normalized = xc_functional.strip().upper().replace("-", "")
    if normalized == "PBE":
        return {"GGA": "PE"}
    if normalized == "RPBE":
        return {"GGA": "RP"}
    if normalized == "PBESOL":
        return {"GGA": "PS"}
    if normalized == "SCAN":
        return {"METAGGA": "SCAN"}
    if normalized == "R2SCAN":
        return {"METAGGA": "R2SCAN"}
    raise IncarPreparationError(f"unsupported xc_functional for Block 5: {xc_functional}")


def _neutral_nelect(*, prepared_poscar: PreparedPoscar, potcar_spec: PotcarSpec) -> float:
    zvals = {entry.element: entry.zval for entry in potcar_spec.entries}
    return sum(
        count * zvals[element]
        for element, count in zip(
            prepared_poscar.species_order,
            prepared_poscar.species_counts,
            strict=True,
        )
    )


def _validate_dipol_text(value: str) -> None:
    parts = value.split()
    if len(parts) != 3:
        raise IncarPreparationError("DIPOL must contain exactly three direct coordinates")
    try:
        coordinates = tuple(float(item) for item in parts)
    except ValueError as exc:
        raise IncarPreparationError("DIPOL coordinates must be numeric") from exc
    if any(not isfinite(item) for item in coordinates):
        raise IncarPreparationError("DIPOL coordinates must be finite")


def _put(
    parameters: dict[str, EffectiveIncarParameter],
    name: str,
    value: IncarValue,
    source: IncarSourceLayer,
) -> None:
    if name in parameters:
        raise IncarPreparationError(f"INCAR parameter {name} has multiple scientific sources")
    parameters[name] = EffectiveIncarParameter(name=name, value=value, source=source)


def _format_incar_value(value: IncarValue) -> str:
    if isinstance(value, bool):
        return ".TRUE." if value else ".FALSE."
    if isinstance(value, float):
        return _format_float(value)
    return str(value)


def _format_float(value: float) -> str:
    if abs(value) < 5e-16:
        value = 0.0
    return f"{value:.12g}"

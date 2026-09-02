"""Scientific method, protocol, recipe, execution, and fingerprint identity."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, is_dataclass
from enum import StrEnum
from hashlib import sha256
from math import isfinite
from uuid import UUID

from ecatvasp.domain.ids import MethodFingerprintId, new_method_fingerprint_id

ScalarParameter = str | int | float | bool | None

_METHOD_PARAMETER_NAMES = frozenset(
    {
        "GGA",
        "METAGGA",
        "LHFCALC",
        "AEXX",
        "HFSCREEN",
        "IVDW",
        "LDAU",
        "LDAUTYPE",
        "LDAUL",
        "LDAUU",
        "LDAUJ",
        "ISPIN",
        "LSORBIT",
        "LNONCOLLINEAR",
        "LSOL",
        "NELECT",
        "EFIELD",
    }
)
_PROTOCOL_PARAMETER_NAMES = frozenset(
    {
        "ENCUT",
        "PREC",
        "EDIFF",
        "EDIFFG",
        "ISMEAR",
        "SIGMA",
        "KSPACING",
        "KGAMMA",
        "MAGMOM",
        "IDIPOL",
        "LDIPOL",
        "DIPOL",
        "LREAL",
        "ISYM",
        "ALGO",
    }
)
_RECIPE_PARAMETER_NAMES = frozenset(
    {
        "IBRION",
        "NSW",
        "ISIF",
        "LAECHG",
        "LCHARG",
        "LWAVE",
        "LORBIT",
        "NEDOS",
        "NFREE",
        "POTIM",
    }
)
_EXECUTION_PARAMETER_NAMES = frozenset({"NCORE", "KPAR", "NPAR"})


def _require_text(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank")


def _require_positive(value: float, field_name: str) -> None:
    if not isfinite(value) or value <= 0:
        raise ValueError(f"{field_name} must be finite and positive")


def _validate_sha256(value: str, field_name: str) -> str:
    normalized = value.lower()
    valid_hex = all(character in "0123456789abcdef" for character in normalized)
    if len(normalized) != 64 or not valid_hex:
        raise ValueError(f"{field_name} must be a 64-character hexadecimal SHA-256 digest")
    return normalized


def _normalize(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: _normalize(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Mapping):
        normalized: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("canonical mappings require string keys")
            normalized[key] = _normalize(item)
        return normalized
    if isinstance(value, (tuple, list)):
        return [_normalize(item) for item in value]
    if isinstance(value, set):
        raise TypeError("sets are not canonical; use an explicitly ordered tuple")
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError("canonical serialization does not allow non-finite floats")
        return 0.0 if value == 0.0 else value
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise TypeError(f"unsupported canonical value: {type(value).__name__}")


def canonical_json(value: object) -> str:
    """Serialize a scientific identity payload deterministically."""

    return json.dumps(
        _normalize(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def canonical_sha256(value: object) -> str:
    """Return the SHA-256 digest of ``canonical_json(value)``."""

    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ParameterEntry:
    """One immutable named scalar used for forward-compatible identity metadata."""

    name: str
    value: ScalarParameter

    def __post_init__(self) -> None:
        _require_text(self.name, "name")
        if isinstance(self.value, float) and not isfinite(self.value):
            raise ValueError("ParameterEntry float values must be finite")


@dataclass(frozen=True, slots=True)
class PotcarIdentity:
    """Licensed POTCAR identity without storing or redistributing its contents."""

    element: str
    symbol: str
    sha256: str

    def __post_init__(self) -> None:
        _require_text(self.element, "element")
        _require_text(self.symbol, "symbol")
        object.__setattr__(self, "sha256", _validate_sha256(self.sha256, "sha256"))


@dataclass(frozen=True, slots=True)
class DftUSetting:
    """Element-specific DFT+U identity included in the physical method hash."""

    element: str
    orbital_l: int
    u_ev: float
    j_ev: float = 0.0

    def __post_init__(self) -> None:
        _require_text(self.element, "element")
        if self.orbital_l < -1:
            raise ValueError("orbital_l must be -1 or a non-negative angular momentum index")
        if not isfinite(self.u_ev) or not isfinite(self.j_ev):
            raise ValueError("DFT+U values must be finite")


class SpinTreatment(StrEnum):
    """Electronic spin model belonging to Method identity."""

    UNPOLARIZED = "unpolarized"
    COLLINEAR = "collinear"
    NONCOLLINEAR = "noncollinear"


@dataclass(frozen=True, slots=True)
class MethodDefinition:
    """Physical-model choices that define core scientific comparability."""

    xc_functional: str
    potcar_family: str
    potcars: tuple[PotcarIdentity, ...]
    engine: str = "vasp"
    engine_version: str | None = None
    dispersion_model: str | None = None
    dft_u: tuple[DftUSetting, ...] = ()
    spin_treatment: SpinTreatment = SpinTreatment.COLLINEAR
    soc: bool = False
    solvation_model: str | None = None
    charge_e: float = 0.0
    electric_field_ev_per_angstrom: float | None = None
    extra_parameters: tuple[ParameterEntry, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.xc_functional, "xc_functional")
        _require_text(self.potcar_family, "potcar_family")
        _require_text(self.engine, "engine")
        if not self.potcars:
            raise ValueError("MethodDefinition requires at least one POTCAR identity")
        if self.engine_version is not None:
            _require_text(self.engine_version, "engine_version")
        if self.dispersion_model is not None:
            _require_text(self.dispersion_model, "dispersion_model")
        if self.solvation_model is not None:
            _require_text(self.solvation_model, "solvation_model")
        if not isfinite(self.charge_e):
            raise ValueError("charge_e must be finite")
        electric_field = self.electric_field_ev_per_angstrom
        if electric_field is not None and not isfinite(electric_field):
            raise ValueError("electric_field_ev_per_angstrom must be finite")
        if self.soc and self.spin_treatment is not SpinTreatment.NONCOLLINEAR:
            raise ValueError("SOC requires NONCOLLINEAR spin treatment")

        potcars = tuple(sorted(self.potcars, key=lambda item: (item.element, item.symbol)))
        if len({item.element for item in potcars}) != len(potcars):
            raise ValueError("POTCAR identities must have unique elements")
        dft_u = tuple(sorted(self.dft_u, key=lambda item: item.element))
        if len({item.element for item in dft_u}) != len(dft_u):
            raise ValueError("DFT+U settings must have unique elements")
        extras = _sorted_unique_parameters(self.extra_parameters, "extra Method parameters")
        _reject_cross_layer_parameters(
            extras,
            _PROTOCOL_PARAMETER_NAMES | _RECIPE_PARAMETER_NAMES | _EXECUTION_PARAMETER_NAMES,
            "Method",
        )

        object.__setattr__(self, "potcars", potcars)
        object.__setattr__(self, "dft_u", dft_u)
        object.__setattr__(self, "extra_parameters", extras)


class KPointPolicyKind(StrEnum):
    """Identity-level description of how a k-point grid is specified."""

    EXPLICIT_MESH = "explicit_mesh"
    KSPACING = "kspacing"
    RECIPROCAL_DENSITY = "reciprocal_density"
    GAMMA_ONLY = "gamma_only"


@dataclass(frozen=True, slots=True)
class KPointPolicy:
    """Immutable k-point protocol descriptor; generation occurs in later blocks."""

    kind: KPointPolicyKind
    mesh: tuple[int, int, int] | None = None
    value: float | None = None

    def __post_init__(self) -> None:
        if self.kind is KPointPolicyKind.EXPLICIT_MESH:
            if self.mesh is None or any(component < 1 for component in self.mesh):
                raise ValueError("EXPLICIT_MESH requires three positive mesh components")
            if self.value is not None:
                raise ValueError("EXPLICIT_MESH does not accept value")
            return
        if self.kind is KPointPolicyKind.GAMMA_ONLY:
            if self.mesh is not None or self.value is not None:
                raise ValueError("GAMMA_ONLY does not accept mesh or value")
            return
        if self.mesh is not None:
            raise ValueError(f"{self.kind.value} does not accept an explicit mesh")
        if self.value is None:
            raise ValueError(f"{self.kind.value} requires a positive value")
        _require_positive(self.value, "k-point policy value")


class DipolePolicy(StrEnum):
    """Slab/molecule dipole-correction strategy in Protocol identity."""

    OFF = "off"
    AUTO = "auto"
    EXPLICIT = "explicit"


@dataclass(frozen=True, slots=True)
class ProtocolDefinition:
    """Numerical strategy whose changes create a protocol revision."""

    encut_ev: float
    kpoints: KPointPolicy
    precision: str = "Accurate"
    ediff_ev: float = 1e-5
    ediffg_ev_per_angstrom: float | None = -0.02
    ismear: int = 0
    sigma_ev: float = 0.05
    dipole_policy: DipolePolicy = DipolePolicy.AUTO
    lreal: str | bool = False
    isym: int | None = None
    initialization_parameters: tuple[ParameterEntry, ...] = ()
    extra_parameters: tuple[ParameterEntry, ...] = ()

    def __post_init__(self) -> None:
        _require_positive(self.encut_ev, "encut_ev")
        _require_text(self.precision, "precision")
        _require_positive(self.ediff_ev, "ediff_ev")
        ediffg = self.ediffg_ev_per_angstrom
        if ediffg is not None and (not isfinite(ediffg) or ediffg == 0):
            raise ValueError("ediffg_ev_per_angstrom must be finite and non-zero")
        _require_positive(self.sigma_ev, "sigma_ev")
        if isinstance(self.lreal, str):
            _require_text(self.lreal, "lreal")

        initialization = _sorted_unique_parameters(
            self.initialization_parameters,
            "initialization parameters",
        )
        extras = _sorted_unique_parameters(self.extra_parameters, "extra Protocol parameters")
        _reject_cross_layer_parameters(
            extras,
            _METHOD_PARAMETER_NAMES | _RECIPE_PARAMETER_NAMES | _EXECUTION_PARAMETER_NAMES,
            "Protocol",
        )
        object.__setattr__(self, "initialization_parameters", initialization)
        object.__setattr__(self, "extra_parameters", extras)


@dataclass(frozen=True, slots=True)
class RecipeIdentity:
    """Stable WXC recipe API identity independent from implementation backend."""

    recipe_id: str
    version: str = "1"
    parameters: tuple[ParameterEntry, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.recipe_id, "recipe_id")
        _require_text(self.version, "version")
        parameters = _sorted_unique_parameters(self.parameters, "Recipe parameters")
        _reject_cross_layer_parameters(
            parameters,
            _METHOD_PARAMETER_NAMES | _PROTOCOL_PARAMETER_NAMES | _EXECUTION_PARAMETER_NAMES,
            "Recipe",
        )
        object.__setattr__(self, "parameters", parameters)

    @property
    def recipe_hash(self) -> str:
        return canonical_sha256(self)


@dataclass(frozen=True, slots=True)
class ExecutionSettings:
    """Execution-only tuning deliberately excluded from scientific fingerprints."""

    ncore: int | None = None
    kpar: int | None = None
    nodes: int | None = None
    cores: int | None = None
    memory_mb: int | None = None
    walltime_seconds: int | None = None
    partition: str | None = None
    mpi_ranks: int | None = None
    omp_threads: int | None = None
    executable: str = "vasp_std"
    extra_parameters: tuple[ParameterEntry, ...] = ()

    def __post_init__(self) -> None:
        for field_name in (
            "ncore",
            "kpar",
            "nodes",
            "cores",
            "memory_mb",
            "walltime_seconds",
            "mpi_ranks",
            "omp_threads",
        ):
            value = getattr(self, field_name)
            if value is not None and value < 1:
                raise ValueError(f"{field_name} must be positive when specified")
        if self.partition is not None:
            _require_text(self.partition, "partition")
        _require_text(self.executable, "executable")
        extras = _sorted_unique_parameters(self.extra_parameters, "extra Execution parameters")
        _reject_cross_layer_parameters(
            extras,
            _METHOD_PARAMETER_NAMES | _PROTOCOL_PARAMETER_NAMES | _RECIPE_PARAMETER_NAMES,
            "Execution",
        )
        object.__setattr__(self, "extra_parameters", extras)

    @property
    def execution_hash(self) -> str:
        return canonical_sha256(self)


@dataclass(frozen=True, slots=True)
class ScientificInputDigest:
    """Named digest of a structure/artifact input participating in instance identity."""

    label: str
    sha256: str

    def __post_init__(self) -> None:
        _require_text(self.label, "label")
        object.__setattr__(self, "sha256", _validate_sha256(self.sha256, "sha256"))


@dataclass(frozen=True, slots=True)
class MethodFingerprint:
    """Three-level scientific fingerprint; execution tuning is intentionally excluded."""

    method: MethodDefinition
    protocol: ProtocolDefinition
    recipe: RecipeIdentity
    input_digests: tuple[ScientificInputDigest, ...] = ()
    id: MethodFingerprintId = field(default_factory=new_method_fingerprint_id)
    core_method_hash: str = field(init=False)
    protocol_hash: str = field(init=False)
    instance_hash: str = field(init=False)

    def __post_init__(self) -> None:
        inputs = tuple(sorted(self.input_digests, key=lambda item: item.label))
        if len({item.label for item in inputs}) != len(inputs):
            raise ValueError("input_digests must have unique labels")
        object.__setattr__(self, "input_digests", inputs)

        core_method_hash = canonical_sha256(self.method)
        protocol_hash = canonical_sha256(self.protocol)
        instance_hash = canonical_sha256(
            {
                "core_method_hash": core_method_hash,
                "protocol_hash": protocol_hash,
                "recipe_hash": self.recipe.recipe_hash,
                "input_digests": inputs,
            }
        )
        object.__setattr__(self, "core_method_hash", core_method_hash)
        object.__setattr__(self, "protocol_hash", protocol_hash)
        object.__setattr__(self, "instance_hash", instance_hash)


class FingerprintCompatibility(StrEnum):
    """Increasingly broad levels of scientific compatibility."""

    IDENTICAL_INSTANCE = "identical_instance"
    SAME_PROTOCOL = "same_protocol"
    CORE_METHOD_COMPATIBLE = "core_method_compatible"
    INCOMPATIBLE = "incompatible"


def compare_fingerprints(
    left: MethodFingerprint,
    right: MethodFingerprint,
) -> FingerprintCompatibility:
    """Compare fingerprints without applying reaction- or reference-specific policy."""

    if left.core_method_hash != right.core_method_hash:
        return FingerprintCompatibility.INCOMPATIBLE
    if left.protocol_hash != right.protocol_hash:
        return FingerprintCompatibility.CORE_METHOD_COMPATIBLE
    if left.instance_hash != right.instance_hash:
        return FingerprintCompatibility.SAME_PROTOCOL
    return FingerprintCompatibility.IDENTICAL_INSTANCE


def _reject_cross_layer_parameters(
    parameters: tuple[ParameterEntry, ...],
    disallowed_names: frozenset[str],
    layer_name: str,
) -> None:
    leaked = sorted(
        parameter.name for parameter in parameters if parameter.name.upper() in disallowed_names
    )
    if leaked:
        names = ", ".join(leaked)
        raise ValueError(f"{layer_name} parameters contain cross-layer keys: {names}")


def _sorted_unique_parameters(
    parameters: tuple[ParameterEntry, ...],
    field_name: str,
) -> tuple[ParameterEntry, ...]:
    ordered = tuple(sorted(parameters, key=lambda item: item.name))
    if len({item.name for item in ordered}) != len(ordered):
        raise ValueError(f"{field_name} must have unique names")
    return ordered

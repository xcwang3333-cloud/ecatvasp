"""Strict Thermochemistry & Reaction Workspace request contracts for desktop IPC v2."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, TypeAlias, TypeGuard, cast
from uuid import UUID

from ecatvasp.desktop.protocol import DesktopIPCError
from ecatvasp.desktop.protocol_v2_common import (
    DESKTOP_IPC_V2_CONTRACT_VERSION,
    DesktopV2Operation,
)

_BASE = frozenset({"protocol_version", "request_id", "operation", "project_root"})
_SUBJECT_KINDS = frozenset({"surface", "adsorbate"})
_ENERGY_KINDS = frozenset(
    {"energy_sigma0_ev", "energy_without_entropy_ev", "free_energy_toten_ev"}
)
_HARMONIC_ENTROPY_POLICIES = frozenset({"neglected"})
_GAS_ENTROPY_POLICIES = frozenset({"neglected", "spin_degeneracy"})
_IMAGINARY_POLICIES = frozenset({"reject_any", "exclude_explicit"})
_LOW_FREQUENCY_POLICIES = frozenset({"reject_below_cutoff", "exclude_explicit"})
_EXCLUSION_REASONS = frozenset(
    {"imaginary", "low_frequency", "constrained", "translational", "rotational"}
)
_GAS_SPECIES = frozenset({"H2", "H2O", "O2", "CO", "CO2"})
_GAS_STANDARD_STATES = frozenset({"ideal_gas_1_bar", "ideal_gas_1_atm"})
_GAS_GEOMETRIES = frozenset({"monatomic", "linear", "nonlinear"})


@dataclass(frozen=True, slots=True)
class DesktopV2ModeExclusion:
    mode_index: int
    reason: str
    note: str | None


@dataclass(frozen=True, slots=True)
class DesktopV2GasAtomicMass:
    atom_uid: str
    mass_amu: float
    isotopologue_label: str | None


@dataclass(frozen=True, slots=True)
class DesktopV2ThermochemistryCatalogRequest:
    request_id: str
    project_root: str
    protocol_version: str = DESKTOP_IPC_V2_CONTRACT_VERSION
    operation: DesktopV2Operation = DesktopV2Operation.THERMOCHEMISTRY_CATALOG


@dataclass(frozen=True, slots=True)
class DesktopV2MaterializeHarmonicThermochemistryRequest:
    request_id: str
    project_root: str
    calculation_id: str
    subject_kind: str
    temperature_k: float
    electronic_energy_kind: str
    electronic_entropy_policy: str
    frequency_cutoff_cm_inverse: float
    imaginary_mode_policy: str
    low_frequency_policy: str
    exclusions: tuple[DesktopV2ModeExclusion, ...]
    protocol_version: str = DESKTOP_IPC_V2_CONTRACT_VERSION
    operation: DesktopV2Operation = DesktopV2Operation.MATERIALIZE_HARMONIC_THERMOCHEMISTRY


@dataclass(frozen=True, slots=True)
class DesktopV2MaterializeGasReferenceRequest:
    request_id: str
    project_root: str
    calculation_id: str
    species: str
    temperature_k: float
    pressure_pa: float
    standard_state: str
    electronic_energy_kind: str
    electronic_entropy_policy: str
    geometry_kind: str
    symmetry_number: int
    spin_multiplicity: int
    atomic_masses: tuple[DesktopV2GasAtomicMass, ...]
    frequency_cutoff_cm_inverse: float
    imaginary_mode_policy: str
    low_frequency_policy: str
    exclusions: tuple[DesktopV2ModeExclusion, ...]
    protocol_version: str = DESKTOP_IPC_V2_CONTRACT_VERSION
    operation: DesktopV2Operation = DesktopV2Operation.MATERIALIZE_GAS_REFERENCE


@dataclass(frozen=True, slots=True)
class DesktopV2ThermochemistryViewRequest:
    request_id: str
    project_root: str
    analysis_id: str
    protocol_version: str = DESKTOP_IPC_V2_CONTRACT_VERSION
    operation: DesktopV2Operation = DesktopV2Operation.THERMOCHEMISTRY_VIEW


DesktopV2ThermochemistryRequest: TypeAlias = (
    DesktopV2ThermochemistryCatalogRequest
    | DesktopV2MaterializeHarmonicThermochemistryRequest
    | DesktopV2MaterializeGasReferenceRequest
    | DesktopV2ThermochemistryViewRequest
)


def is_desktop_v2_thermochemistry_request(
    value: object,
) -> TypeGuard[DesktopV2ThermochemistryRequest]:
    return isinstance(
        value,
        (
            DesktopV2ThermochemistryCatalogRequest,
            DesktopV2MaterializeHarmonicThermochemistryRequest,
            DesktopV2MaterializeGasReferenceRequest,
            DesktopV2ThermochemistryViewRequest,
        ),
    )


def decode_desktop_v2_thermochemistry_request(
    raw: dict[str, Any],
    *,
    operation: DesktopV2Operation,
    request_id: str,
) -> DesktopV2ThermochemistryRequest:
    project_root = _required_string(raw, "project_root")
    if operation is DesktopV2Operation.THERMOCHEMISTRY_CATALOG:
        _reject_unknown(raw, _BASE, operation)
        return DesktopV2ThermochemistryCatalogRequest(
            request_id=request_id,
            project_root=project_root,
        )
    if operation is DesktopV2Operation.MATERIALIZE_HARMONIC_THERMOCHEMISTRY:
        allowed = _BASE | {
            "calculation_id",
            "subject_kind",
            "temperature_k",
            "electronic_energy_kind",
            "electronic_entropy_policy",
            "frequency_cutoff_cm_inverse",
            "imaginary_mode_policy",
            "low_frequency_policy",
            "exclusions",
        }
        _reject_unknown(raw, allowed, operation)
        return DesktopV2MaterializeHarmonicThermochemistryRequest(
            request_id=request_id,
            project_root=project_root,
            calculation_id=_required_uuid(raw, "calculation_id"),
            subject_kind=_choice(raw, "subject_kind", _SUBJECT_KINDS),
            temperature_k=_positive_number(raw, "temperature_k"),
            electronic_energy_kind=_choice(raw, "electronic_energy_kind", _ENERGY_KINDS),
            electronic_entropy_policy=_choice(
                raw,
                "electronic_entropy_policy",
                _HARMONIC_ENTROPY_POLICIES,
            ),
            frequency_cutoff_cm_inverse=_positive_number(
                raw,
                "frequency_cutoff_cm_inverse",
            ),
            imaginary_mode_policy=_choice(
                raw,
                "imaginary_mode_policy",
                _IMAGINARY_POLICIES,
            ),
            low_frequency_policy=_choice(
                raw,
                "low_frequency_policy",
                _LOW_FREQUENCY_POLICIES,
            ),
            exclusions=_mode_exclusions(raw),
        )
    if operation is DesktopV2Operation.MATERIALIZE_GAS_REFERENCE:
        allowed = _BASE | {
            "calculation_id",
            "species",
            "temperature_k",
            "pressure_pa",
            "standard_state",
            "electronic_energy_kind",
            "electronic_entropy_policy",
            "geometry_kind",
            "symmetry_number",
            "spin_multiplicity",
            "atomic_masses",
            "frequency_cutoff_cm_inverse",
            "imaginary_mode_policy",
            "low_frequency_policy",
            "exclusions",
        }
        _reject_unknown(raw, allowed, operation)
        return DesktopV2MaterializeGasReferenceRequest(
            request_id=request_id,
            project_root=project_root,
            calculation_id=_required_uuid(raw, "calculation_id"),
            species=_choice(raw, "species", _GAS_SPECIES),
            temperature_k=_positive_number(raw, "temperature_k"),
            pressure_pa=_positive_number(raw, "pressure_pa"),
            standard_state=_choice(raw, "standard_state", _GAS_STANDARD_STATES),
            electronic_energy_kind=_choice(raw, "electronic_energy_kind", _ENERGY_KINDS),
            electronic_entropy_policy=_choice(
                raw,
                "electronic_entropy_policy",
                _GAS_ENTROPY_POLICIES,
            ),
            geometry_kind=_choice(raw, "geometry_kind", _GAS_GEOMETRIES),
            symmetry_number=_positive_integer(raw, "symmetry_number"),
            spin_multiplicity=_positive_integer(raw, "spin_multiplicity"),
            atomic_masses=_gas_atomic_masses(raw),
            frequency_cutoff_cm_inverse=_positive_number(
                raw,
                "frequency_cutoff_cm_inverse",
            ),
            imaginary_mode_policy=_choice(
                raw,
                "imaginary_mode_policy",
                _IMAGINARY_POLICIES,
            ),
            low_frequency_policy=_choice(
                raw,
                "low_frequency_policy",
                _LOW_FREQUENCY_POLICIES,
            ),
            exclusions=_mode_exclusions(raw),
        )
    if operation is DesktopV2Operation.THERMOCHEMISTRY_VIEW:
        _reject_unknown(raw, _BASE | {"analysis_id"}, operation)
        return DesktopV2ThermochemistryViewRequest(
            request_id=request_id,
            project_root=project_root,
            analysis_id=_required_uuid(raw, "analysis_id"),
        )
    raise DesktopIPCError("operation is not a Thermochemistry & Reaction Workspace request")


def _mode_exclusions(raw: dict[str, Any]) -> tuple[DesktopV2ModeExclusion, ...]:
    value = raw.get("exclusions", [])
    if not isinstance(value, list):
        raise DesktopIPCError("exclusions must be an array")
    result: list[DesktopV2ModeExclusion] = []
    seen: set[int] = set()
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise DesktopIPCError(f"exclusions[{index}] must be an object")
        typed = cast(dict[str, Any], item)
        unknown = set(typed) - {"mode_index", "reason", "note"}
        if unknown:
            raise DesktopIPCError(
                f"exclusions[{index}] contains unknown fields: {sorted(unknown)!r}"
            )
        mode_index = _positive_integer(typed, "mode_index")
        if mode_index in seen:
            raise DesktopIPCError("exclusions mode_index values must be unique")
        seen.add(mode_index)
        result.append(
            DesktopV2ModeExclusion(
                mode_index=mode_index,
                reason=_choice(typed, "reason", _EXCLUSION_REASONS),
                note=_optional_string(typed, "note"),
            )
        )
    return tuple(result)


def _gas_atomic_masses(raw: dict[str, Any]) -> tuple[DesktopV2GasAtomicMass, ...]:
    value = raw.get("atomic_masses")
    if not isinstance(value, list) or not value:
        raise DesktopIPCError("atomic_masses must be a non-empty array")
    result: list[DesktopV2GasAtomicMass] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise DesktopIPCError(f"atomic_masses[{index}] must be an object")
        typed = cast(dict[str, Any], item)
        unknown = set(typed) - {"atom_uid", "mass_amu", "isotopologue_label"}
        if unknown:
            raise DesktopIPCError(
                f"atomic_masses[{index}] contains unknown fields: {sorted(unknown)!r}"
            )
        atom_uid = _required_uuid(typed, "atom_uid")
        if atom_uid in seen:
            raise DesktopIPCError("atomic_masses atom_uid values must be unique")
        seen.add(atom_uid)
        result.append(
            DesktopV2GasAtomicMass(
                atom_uid=atom_uid,
                mass_amu=_positive_number(typed, "mass_amu"),
                isotopologue_label=_optional_string(typed, "isotopologue_label"),
            )
        )
    return tuple(result)


def _required_string(raw: dict[str, Any], field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value.strip():
        raise DesktopIPCError(f"{field} must be a non-blank string")
    return value


def _optional_string(raw: dict[str, Any], field: str) -> str | None:
    value = raw.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise DesktopIPCError(f"{field} must be null or a non-blank string")
    return value


def _required_uuid(raw: dict[str, Any], field: str) -> str:
    value = _required_string(raw, field)
    try:
        UUID(value)
    except ValueError as error:
        raise DesktopIPCError(f"{field} must be a UUID") from error
    return value


def _choice(raw: dict[str, Any], field: str, choices: frozenset[str]) -> str:
    value = _required_string(raw, field)
    if value not in choices:
        raise DesktopIPCError(f"{field} has an unsupported value")
    return value


def _positive_number(raw: dict[str, Any], field: str) -> float:
    value = raw.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DesktopIPCError(f"{field} must be a finite positive number")
    converted = float(value)
    if not isfinite(converted) or converted <= 0.0:
        raise DesktopIPCError(f"{field} must be a finite positive number")
    return converted


def _positive_integer(raw: dict[str, Any], field: str) -> int:
    value = raw.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise DesktopIPCError(f"{field} must be a positive integer")
    return value


def _reject_unknown(
    raw: dict[str, Any],
    allowed: set[str] | frozenset[str],
    operation: DesktopV2Operation,
) -> None:
    unknown = set(raw) - set(allowed)
    if unknown:
        raise DesktopIPCError(
            f"{operation.value} request contains unknown fields: {sorted(unknown)!r}"
        )


__all__ = [
    "DesktopV2GasAtomicMass",
    "DesktopV2MaterializeGasReferenceRequest",
    "DesktopV2MaterializeHarmonicThermochemistryRequest",
    "DesktopV2ModeExclusion",
    "DesktopV2ThermochemistryCatalogRequest",
    "DesktopV2ThermochemistryRequest",
    "DesktopV2ThermochemistryViewRequest",
    "decode_desktop_v2_thermochemistry_request",
    "is_desktop_v2_thermochemistry_request",
]

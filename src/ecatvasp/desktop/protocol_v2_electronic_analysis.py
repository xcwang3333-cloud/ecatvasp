"""Strict Electronic Analysis Workspace request contracts for desktop IPC v2."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, TypeAlias, TypeGuard
from uuid import UUID

from ecatvasp.desktop.protocol import DesktopIPCError
from ecatvasp.desktop.protocol_v2_common import (
    DESKTOP_IPC_V2_CONTRACT_VERSION,
    DesktopV2Operation,
)

_BASE = frozenset({"protocol_version", "request_id", "operation", "project_root"})
_KINDS = frozenset({"band", "p_band", "d_band"})
_SCOPES = frozenset({"system", "atom", "element"})
_SPINS = frozenset({"total", "up", "down", "sum"})
_ENERGY_REFERENCES = frozenset({"vasp_native", "fermi_relative"})


@dataclass(frozen=True, slots=True)
class DesktopV2ElectronicAnalysisCatalogRequest:
    request_id: str
    project_root: str
    protocol_version: str = DESKTOP_IPC_V2_CONTRACT_VERSION
    operation: DesktopV2Operation = DesktopV2Operation.ELECTRONIC_ANALYSIS_CATALOG


@dataclass(frozen=True, slots=True)
class DesktopV2MaterializeDosAnalysisRequest:
    request_id: str
    project_root: str
    calculation_id: str
    protocol_version: str = DESKTOP_IPC_V2_CONTRACT_VERSION
    operation: DesktopV2Operation = DesktopV2Operation.MATERIALIZE_DOS_ANALYSIS


@dataclass(frozen=True, slots=True)
class DesktopV2ElectronicAnalysisViewRequest:
    request_id: str
    project_root: str
    analysis_id: str
    protocol_version: str = DESKTOP_IPC_V2_CONTRACT_VERSION
    operation: DesktopV2Operation = DesktopV2Operation.ELECTRONIC_ANALYSIS_VIEW


@dataclass(frozen=True, slots=True)
class DesktopV2MaterializeBandCenterRequest:
    request_id: str
    project_root: str
    source_analysis_id: str
    kind: str
    scope: str
    spin: str
    atom_uid: str | None
    element: str | None
    energy_reference: str
    window_lower_ev: float
    window_upper_ev: float
    protocol_version: str = DESKTOP_IPC_V2_CONTRACT_VERSION
    operation: DesktopV2Operation = DesktopV2Operation.MATERIALIZE_BAND_CENTER


DesktopV2ElectronicAnalysisRequest: TypeAlias = (
    DesktopV2ElectronicAnalysisCatalogRequest
    | DesktopV2MaterializeDosAnalysisRequest
    | DesktopV2ElectronicAnalysisViewRequest
    | DesktopV2MaterializeBandCenterRequest
)


def is_desktop_v2_electronic_analysis_request(
    value: object,
) -> TypeGuard[DesktopV2ElectronicAnalysisRequest]:
    return isinstance(
        value,
        (
            DesktopV2ElectronicAnalysisCatalogRequest,
            DesktopV2MaterializeDosAnalysisRequest,
            DesktopV2ElectronicAnalysisViewRequest,
            DesktopV2MaterializeBandCenterRequest,
        ),
    )


def decode_desktop_v2_electronic_analysis_request(
    raw: dict[str, Any],
    *,
    operation: DesktopV2Operation,
    request_id: str,
) -> DesktopV2ElectronicAnalysisRequest:
    project_root = _required_string(raw, "project_root")
    if operation is DesktopV2Operation.ELECTRONIC_ANALYSIS_CATALOG:
        _reject_unknown(raw, _BASE, operation)
        return DesktopV2ElectronicAnalysisCatalogRequest(
            request_id=request_id,
            project_root=project_root,
        )
    if operation is DesktopV2Operation.MATERIALIZE_DOS_ANALYSIS:
        _reject_unknown(raw, _BASE | {"calculation_id"}, operation)
        return DesktopV2MaterializeDosAnalysisRequest(
            request_id=request_id,
            project_root=project_root,
            calculation_id=_required_uuid(raw, "calculation_id"),
        )
    if operation is DesktopV2Operation.ELECTRONIC_ANALYSIS_VIEW:
        _reject_unknown(raw, _BASE | {"analysis_id"}, operation)
        return DesktopV2ElectronicAnalysisViewRequest(
            request_id=request_id,
            project_root=project_root,
            analysis_id=_required_uuid(raw, "analysis_id"),
        )
    if operation is DesktopV2Operation.MATERIALIZE_BAND_CENTER:
        allowed = _BASE | {
            "source_analysis_id",
            "kind",
            "scope",
            "spin",
            "atom_uid",
            "element",
            "energy_reference",
            "window_lower_ev",
            "window_upper_ev",
        }
        _reject_unknown(raw, allowed, operation)
        scope = _choice(raw, "scope", _SCOPES)
        atom_uid = _optional_uuid(raw, "atom_uid")
        element = _optional_string(raw, "element")
        _validate_selector(scope=scope, atom_uid=atom_uid, element=element)
        lower = _number(raw, "window_lower_ev")
        upper = _number(raw, "window_upper_ev")
        if upper <= lower:
            raise DesktopIPCError("band-center integration window must have positive width")
        return DesktopV2MaterializeBandCenterRequest(
            request_id=request_id,
            project_root=project_root,
            source_analysis_id=_required_uuid(raw, "source_analysis_id"),
            kind=_choice(raw, "kind", _KINDS),
            scope=scope,
            spin=_choice(raw, "spin", _SPINS),
            atom_uid=atom_uid,
            element=element,
            energy_reference=_choice(raw, "energy_reference", _ENERGY_REFERENCES),
            window_lower_ev=lower,
            window_upper_ev=upper,
        )
    raise DesktopIPCError("operation is not an Electronic Analysis Workspace request")


def _validate_selector(*, scope: str, atom_uid: str | None, element: str | None) -> None:
    if scope == "system":
        if atom_uid is not None or element is not None:
            raise DesktopIPCError("system band-center selector forbids atom_uid and element")
        return
    if scope == "atom":
        if atom_uid is None or element is None:
            raise DesktopIPCError("atom band-center selector requires atom_uid and element")
        return
    if scope == "element" and (atom_uid is not None or element is None):
        raise DesktopIPCError("element band-center selector requires element and forbids atom_uid")


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


def _optional_uuid(raw: dict[str, Any], field: str) -> str | None:
    value = raw.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise DesktopIPCError(f"{field} must be null or a UUID string")
    try:
        UUID(value)
    except ValueError as error:
        raise DesktopIPCError(f"{field} must be null or a UUID") from error
    return value


def _choice(raw: dict[str, Any], field: str, choices: frozenset[str]) -> str:
    value = _required_string(raw, field)
    if value not in choices:
        raise DesktopIPCError(f"{field} has an unsupported value")
    return value


def _number(raw: dict[str, Any], field: str) -> float:
    value = raw.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DesktopIPCError(f"{field} must be a finite number")
    converted = float(value)
    if not isfinite(converted):
        raise DesktopIPCError(f"{field} must be a finite number")
    return converted


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
    "DesktopV2ElectronicAnalysisCatalogRequest",
    "DesktopV2ElectronicAnalysisRequest",
    "DesktopV2ElectronicAnalysisViewRequest",
    "DesktopV2MaterializeBandCenterRequest",
    "DesktopV2MaterializeDosAnalysisRequest",
    "decode_desktop_v2_electronic_analysis_request",
    "is_desktop_v2_electronic_analysis_request",
]

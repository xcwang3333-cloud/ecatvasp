"""Strict Result Center request contracts for desktop IPC v2."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeAlias, TypeGuard
from uuid import UUID

from ecatvasp.desktop.protocol import DesktopIPCError
from ecatvasp.desktop.protocol_v2_common import (
    DESKTOP_IPC_V2_CONTRACT_VERSION,
    DesktopV2Operation,
)

_BASE = frozenset({"protocol_version", "request_id", "operation", "project_root"})


@dataclass(frozen=True, slots=True)
class DesktopV2ResultCatalogRequest:
    request_id: str
    project_root: str
    protocol_version: str = DESKTOP_IPC_V2_CONTRACT_VERSION
    operation: DesktopV2Operation = DesktopV2Operation.RESULT_CATALOG


@dataclass(frozen=True, slots=True)
class DesktopV2AnalyzeResultRequest:
    request_id: str
    project_root: str
    calculation_id: str
    protocol_version: str = DESKTOP_IPC_V2_CONTRACT_VERSION
    operation: DesktopV2Operation = DesktopV2Operation.ANALYZE_RESULT


@dataclass(frozen=True, slots=True)
class DesktopV2PromoteResultStructureRequest:
    request_id: str
    project_root: str
    calculation_id: str
    label: str | None
    protocol_version: str = DESKTOP_IPC_V2_CONTRACT_VERSION
    operation: DesktopV2Operation = DesktopV2Operation.PROMOTE_RESULT_STRUCTURE


DesktopV2ResultCenterRequest: TypeAlias = (
    DesktopV2ResultCatalogRequest
    | DesktopV2AnalyzeResultRequest
    | DesktopV2PromoteResultStructureRequest
)


def is_desktop_v2_result_center_request(
    value: object,
) -> TypeGuard[DesktopV2ResultCenterRequest]:
    return isinstance(
        value,
        (
            DesktopV2ResultCatalogRequest,
            DesktopV2AnalyzeResultRequest,
            DesktopV2PromoteResultStructureRequest,
        ),
    )


def decode_desktop_v2_result_center_request(
    raw: dict[str, Any],
    *,
    operation: DesktopV2Operation,
    request_id: str,
) -> DesktopV2ResultCenterRequest:
    project_root = _required_string(raw, "project_root")
    if operation is DesktopV2Operation.RESULT_CATALOG:
        _reject_unknown(raw, _BASE, operation)
        return DesktopV2ResultCatalogRequest(
            request_id=request_id,
            project_root=project_root,
        )
    if operation is DesktopV2Operation.ANALYZE_RESULT:
        _reject_unknown(raw, _BASE | {"calculation_id"}, operation)
        return DesktopV2AnalyzeResultRequest(
            request_id=request_id,
            project_root=project_root,
            calculation_id=_required_uuid(raw, "calculation_id"),
        )
    if operation is DesktopV2Operation.PROMOTE_RESULT_STRUCTURE:
        _reject_unknown(raw, _BASE | {"calculation_id", "label"}, operation)
        label = raw.get("label")
        if label is not None and (not isinstance(label, str) or not label.strip()):
            raise DesktopIPCError("label must be a non-blank string when supplied")
        return DesktopV2PromoteResultStructureRequest(
            request_id=request_id,
            project_root=project_root,
            calculation_id=_required_uuid(raw, "calculation_id"),
            label=label,
        )
    raise DesktopIPCError("operation is not a Result Center request")


def _required_string(raw: dict[str, Any], field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value.strip():
        raise DesktopIPCError(f"{field} must be a non-blank string")
    return value


def _required_uuid(raw: dict[str, Any], field: str) -> str:
    value = _required_string(raw, field)
    try:
        UUID(value)
    except ValueError as error:
        raise DesktopIPCError(f"{field} must be a UUID") from error
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
    "DesktopV2AnalyzeResultRequest",
    "DesktopV2PromoteResultStructureRequest",
    "DesktopV2ResultCatalogRequest",
    "DesktopV2ResultCenterRequest",
    "decode_desktop_v2_result_center_request",
    "is_desktop_v2_result_center_request",
]

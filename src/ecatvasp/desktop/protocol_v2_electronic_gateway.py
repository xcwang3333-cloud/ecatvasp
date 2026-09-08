"""Block 6 extension gateway for the frozen desktop IPC v2 family.

The wire protocol remains ``ecatvasp-desktop-ipc-v2``.  Existing v2 requests are
forwarded unchanged to ``DesktopBackendV2``; only the four explicit Electronic
Analysis Workspace operations are decoded and handled here.
"""

from __future__ import annotations

import json
from typing import Any, TypeAlias, TypeGuard

from ecatvasp.desktop.electronic_analysis import (
    electronic_analysis_catalog_action,
    electronic_analysis_view_action,
    materialize_band_center_action,
    materialize_dos_analysis_action,
)
from ecatvasp.desktop.protocol import DesktopError, DesktopIPCError
from ecatvasp.desktop.protocol_v2 import (
    DesktopBackendV2,
    DesktopV2Request,
    DesktopV2Response,
    decode_desktop_v2_request,
    encode_desktop_v2_response,
    is_desktop_v2_request,
)
from ecatvasp.desktop.protocol_v2_common import (
    DESKTOP_IPC_V2_CONTRACT_VERSION,
    DesktopV2Operation,
)
from ecatvasp.desktop.protocol_v2_electronic_analysis import (
    DesktopV2ElectronicAnalysisCatalogRequest,
    DesktopV2ElectronicAnalysisRequest,
    DesktopV2ElectronicAnalysisViewRequest,
    DesktopV2MaterializeBandCenterRequest,
    DesktopV2MaterializeDosAnalysisRequest,
    decode_desktop_v2_electronic_analysis_request,
    is_desktop_v2_electronic_analysis_request,
)
from ecatvasp.storage import (
    MigrationPathError,
    ProjectIntegrityError,
    ProjectStorageError,
    StorageCodecError,
    UnsupportedSchemaVersionError,
)

_ELECTRONIC_OPERATIONS = frozenset(
    {
        DesktopV2Operation.ELECTRONIC_ANALYSIS_CATALOG,
        DesktopV2Operation.MATERIALIZE_DOS_ANALYSIS,
        DesktopV2Operation.ELECTRONIC_ANALYSIS_VIEW,
        DesktopV2Operation.MATERIALIZE_BAND_CENTER,
    }
)
_PROJECT_READ_ERRORS = (
    MigrationPathError,
    ProjectIntegrityError,
    ProjectStorageError,
    StorageCodecError,
    UnsupportedSchemaVersionError,
)

DesktopV2Block6Request: TypeAlias = DesktopV2Request | DesktopV2ElectronicAnalysisRequest


def is_desktop_v2_block6_request(value: object) -> TypeGuard[DesktopV2Block6Request]:
    return is_desktop_v2_request(value) or is_desktop_v2_electronic_analysis_request(value)


def decode_desktop_v2_block6_request(line: str) -> DesktopV2Block6Request:
    try:
        raw: Any = json.loads(line)
    except json.JSONDecodeError as error:
        raise DesktopIPCError("desktop request is not valid JSON") from error
    if not isinstance(raw, dict):
        raise DesktopIPCError("desktop request must be a JSON object")
    protocol_version = _required_string(raw, "protocol_version")
    request_id = _required_string(raw, "request_id")
    operation_text = _required_string(raw, "operation")
    if protocol_version != DESKTOP_IPC_V2_CONTRACT_VERSION:
        raise DesktopIPCError("unsupported desktop IPC contract version")
    try:
        operation = DesktopV2Operation(operation_text)
    except ValueError as error:
        raise DesktopIPCError("unsupported desktop operation") from error
    if operation in _ELECTRONIC_OPERATIONS:
        return decode_desktop_v2_electronic_analysis_request(
            raw,
            operation=operation,
            request_id=request_id,
        )
    return decode_desktop_v2_request(line)


class DesktopBackendV2Block6:
    """Compatibility-preserving v2 backend with explicit Block 6 operations."""

    def __init__(self) -> None:
        self._base = DesktopBackendV2()

    def handle(self, request: DesktopV2Block6Request) -> DesktopV2Response:
        if not is_desktop_v2_electronic_analysis_request(request):
            return self._base.handle(request)
        try:
            if isinstance(request, DesktopV2ElectronicAnalysisCatalogRequest):
                payload = electronic_analysis_catalog_action(request.project_root)
            elif isinstance(request, DesktopV2MaterializeDosAnalysisRequest):
                payload = materialize_dos_analysis_action(
                    project_root=request.project_root,
                    calculation_id=request.calculation_id,
                )
            elif isinstance(request, DesktopV2ElectronicAnalysisViewRequest):
                payload = electronic_analysis_view_action(
                    project_root=request.project_root,
                    analysis_id=request.analysis_id,
                )
            elif isinstance(request, DesktopV2MaterializeBandCenterRequest):
                payload = materialize_band_center_action(
                    project_root=request.project_root,
                    source_analysis_id=request.source_analysis_id,
                    kind=request.kind,
                    scope=request.scope,
                    spin=request.spin,
                    atom_uid=request.atom_uid,
                    element=request.element,
                    energy_reference=request.energy_reference,
                    window_lower_ev=request.window_lower_ev,
                    window_upper_ev=request.window_upper_ev,
                )
            else:  # pragma: no cover - guarded by request TypeGuard
                raise DesktopIPCError("unsupported Electronic Analysis Workspace request")
        except _PROJECT_READ_ERRORS as error:
            return _failure(request, code="project_unavailable", message=str(error))
        except (OSError, ValueError) as error:
            return _failure(request, code="application_rejected", message=str(error))
        return DesktopV2Response(
            request_id=request.request_id,
            operation=request.operation,
            ok=True,
            payload=payload,
        )


def _failure(
    request: DesktopV2ElectronicAnalysisRequest,
    *,
    code: str,
    message: str,
) -> DesktopV2Response:
    return DesktopV2Response(
        request_id=request.request_id,
        operation=request.operation,
        ok=False,
        error=DesktopError(code=code, message=message),
    )


def _required_string(raw: dict[str, Any], field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value.strip():
        raise DesktopIPCError(f"{field} must be a non-blank string")
    return value


__all__ = [
    "DesktopBackendV2Block6",
    "DesktopV2Block6Request",
    "decode_desktop_v2_block6_request",
    "encode_desktop_v2_response",
    "is_desktop_v2_block6_request",
]

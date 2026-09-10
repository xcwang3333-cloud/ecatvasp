"""Block 8 gateway exposing transient real-site preflight through desktop IPC v2."""

from __future__ import annotations

import json
from typing import Any, TypeAlias, TypeGuard, cast

from ecatvasp.desktop.protocol import DesktopError, DesktopIPCError
from ecatvasp.desktop.protocol_v2 import DesktopV2Response
from ecatvasp.desktop.protocol_v2_common import (
    DESKTOP_IPC_V2_CONTRACT_VERSION,
    DesktopV2Operation,
)
from ecatvasp.desktop.protocol_v2_site_preflight import (
    DesktopV2SitePreflightRequest,
    decode_desktop_v2_site_preflight_request,
    is_desktop_v2_site_preflight_request,
)
from ecatvasp.desktop.protocol_v2_thermochemistry_gateway import (
    DesktopBackendV2Block7,
    DesktopV2Block7Request,
    decode_desktop_v2_block7_request,
    encode_desktop_v2_response,
    is_desktop_v2_block7_request,
)
from ecatvasp.desktop.site_preflight import site_preflight_action

DesktopV2Block8Request: TypeAlias = DesktopV2Block7Request | DesktopV2SitePreflightRequest


def is_desktop_v2_block8_request(value: object) -> TypeGuard[DesktopV2Block8Request]:
    return is_desktop_v2_block7_request(value) or is_desktop_v2_site_preflight_request(value)


def decode_desktop_v2_block8_request(line: str) -> DesktopV2Block8Request:
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
    if operation is DesktopV2Operation.SITE_PREFLIGHT:
        return decode_desktop_v2_site_preflight_request(
            cast(dict[str, Any], raw),
            operation=operation,
            request_id=request_id,
        )
    return decode_desktop_v2_block7_request(line)


class DesktopBackendV2Block8:
    """Compatibility-preserving v2 backend with typed real-site preflight access."""

    def __init__(self) -> None:
        self._base = DesktopBackendV2Block7()

    def handle(self, request: DesktopV2Block8Request) -> DesktopV2Response:
        if is_desktop_v2_site_preflight_request(request):
            try:
                payload = site_preflight_action(request.profile)
            except (OSError, ValueError) as error:
                return DesktopV2Response(
                    request_id=request.request_id,
                    operation=request.operation,
                    ok=False,
                    error=DesktopError(code="application_rejected", message=str(error)),
                )
            return DesktopV2Response(
                request_id=request.request_id,
                operation=request.operation,
                ok=True,
                payload=payload,
            )
        return self._base.handle(cast(DesktopV2Block7Request, request))


def _required_string(raw: dict[str, Any], field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value.strip():
        raise DesktopIPCError(f"{field} must be a non-blank string")
    return value


__all__ = [
    "DesktopBackendV2Block8",
    "DesktopV2Block8Request",
    "decode_desktop_v2_block8_request",
    "encode_desktop_v2_response",
    "is_desktop_v2_block8_request",
]

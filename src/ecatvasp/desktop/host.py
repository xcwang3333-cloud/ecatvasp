"""Local stdio sidecar host for versioned desktop IPC contracts."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Protocol, TextIO, TypeAlias

from ecatvasp.desktop.protocol import (
    DESKTOP_IPC_CONTRACT_VERSION,
    DesktopBackend,
    DesktopError,
    DesktopIPCError,
    DesktopRequest,
    DesktopResponse,
    decode_desktop_request,
    encode_desktop_response,
)
from ecatvasp.desktop.protocol_v2 import (
    DESKTOP_IPC_V2_CONTRACT_VERSION,
    DesktopBackendV2,
    DesktopV2Request,
    DesktopV2Response,
    decode_desktop_v2_request,
    encode_desktop_v2_response,
    is_desktop_v2_request,
)

DESKTOP_HOST_CONTRACT_VERSION = "ecatvasp-desktop-host-v1"
HOST_EXIT_OK = 0
HOST_EXIT_BACKEND_FAILURE = 1
_HOST_ERROR_FRAME_TYPE = "host_error"

DesktopAnyRequest: TypeAlias = DesktopRequest | DesktopV2Request
DesktopAnyResponse: TypeAlias = DesktopResponse | DesktopV2Response


class _DesktopRequestHandler(Protocol):
    def handle(self, request: DesktopAnyRequest) -> DesktopAnyResponse:
        """Handle one already-decoded versioned desktop request."""
        ...


class _VersionedDesktopBackend:
    """Route exact v1 requests to the frozen adapter and v2 requests to the v1.1 adapter."""

    def __init__(self) -> None:
        self._v1 = DesktopBackend()
        self._v2 = DesktopBackendV2()

    def handle(self, request: DesktopAnyRequest) -> DesktopAnyResponse:
        if is_desktop_v2_request(request):
            return self._v2.handle(request)
        if not isinstance(request, DesktopRequest):
            raise DesktopIPCError("desktop request family is unsupported")
        return self._v1.handle(request)


@dataclass(frozen=True, slots=True)
class DesktopHostErrorFrame:
    """Uncorrelated framing error emitted when a request cannot be decoded safely."""

    error: DesktopError
    host_protocol_version: str = DESKTOP_HOST_CONTRACT_VERSION
    frame_type: str = _HOST_ERROR_FRAME_TYPE

    def __post_init__(self) -> None:
        if self.host_protocol_version != DESKTOP_HOST_CONTRACT_VERSION:
            raise DesktopIPCError("unsupported desktop host contract version")
        if self.frame_type != _HOST_ERROR_FRAME_TYPE:
            raise DesktopIPCError("unsupported desktop host frame type")

    def to_dict(self) -> dict[str, object]:
        return {
            "host_protocol_version": self.host_protocol_version,
            "frame_type": self.frame_type,
            "ok": False,
            "error": self.error.to_dict(),
        }


def encode_desktop_host_error(error: DesktopIPCError) -> str:
    """Render one deterministic host-level error for an undecodable input line."""

    frame = DesktopHostErrorFrame(
        error=DesktopError(code="invalid_request", message=str(error)),
    )
    return (
        json.dumps(
            frame.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        + "\n"
    )


def decode_versioned_desktop_request(line: str) -> DesktopAnyRequest:
    """Select the strict decoder from the explicit wire protocol version."""

    try:
        raw = json.loads(line)
    except json.JSONDecodeError as error:
        raise DesktopIPCError("desktop request is not valid JSON") from error
    if not isinstance(raw, dict):
        raise DesktopIPCError("desktop request must be a JSON object")
    protocol_version = raw.get("protocol_version")
    if not isinstance(protocol_version, str):
        raise DesktopIPCError("protocol_version must be a string")
    if protocol_version == DESKTOP_IPC_CONTRACT_VERSION:
        return decode_desktop_request(line)
    if protocol_version == DESKTOP_IPC_V2_CONTRACT_VERSION:
        return decode_desktop_v2_request(line)
    raise DesktopIPCError("unsupported desktop IPC contract version")


def encode_versioned_desktop_response(response: DesktopAnyResponse) -> str:
    """Encode one response using the same protocol family as its decoded request."""

    if isinstance(response, DesktopV2Response):
        return encode_desktop_v2_response(response)
    return encode_desktop_response(response)


def run_stdio_host(
    stdin: TextIO,
    stdout: TextIO,
    stderr: TextIO,
    *,
    backend: _DesktopRequestHandler | None = None,
) -> int:
    """Serve v1/v2 NDJSON requests until EOF without retaining project/session authority."""

    selected_backend: _DesktopRequestHandler = (
        _VersionedDesktopBackend() if backend is None else backend
    )
    for raw_line in stdin:
        line = raw_line.rstrip("\r\n")
        if not line.strip():
            _write_protocol_frame(
                stdout,
                encode_desktop_host_error(
                    DesktopIPCError("desktop host input line must not be blank")
                ),
            )
            continue

        try:
            request = decode_versioned_desktop_request(line)
        except DesktopIPCError as error:
            _write_protocol_frame(stdout, encode_desktop_host_error(error))
            continue

        try:
            response = selected_backend.handle(request)
        except Exception as error:
            _write_fatal_backend_error(stderr, error)
            return HOST_EXIT_BACKEND_FAILURE

        _write_protocol_frame(stdout, encode_versioned_desktop_response(response))

    return HOST_EXIT_OK


def main() -> int:
    """Run the production local-sidecar stdio host until the controlling pipe reaches EOF."""

    return run_stdio_host(sys.stdin, sys.stdout, sys.stderr)


def _write_protocol_frame(stdout: TextIO, frame: str) -> None:
    stdout.write(frame)
    stdout.flush()


def _write_fatal_backend_error(stderr: TextIO, error: Exception) -> None:
    stderr.write(
        "ecatvasp-desktop-backend: fatal backend failure: "
        f"{type(error).__name__}\n"
    )
    stderr.flush()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

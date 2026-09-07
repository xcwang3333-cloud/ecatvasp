"""Local stdio sidecar host for the versioned desktop IPC contract."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Protocol, TextIO

from ecatvasp.desktop.protocol import (
    DesktopBackend,
    DesktopError,
    DesktopIPCError,
    DesktopRequest,
    DesktopResponse,
    decode_desktop_request,
    encode_desktop_response,
)

DESKTOP_HOST_CONTRACT_VERSION = "ecatvasp-desktop-host-v1"
HOST_EXIT_OK = 0
HOST_EXIT_BACKEND_FAILURE = 1
_HOST_ERROR_FRAME_TYPE = "host_error"


class _DesktopRequestHandler(Protocol):
    def handle(self, request: DesktopRequest) -> DesktopResponse:
        """Handle one already-decoded desktop request."""


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


def run_stdio_host(
    stdin: TextIO,
    stdout: TextIO,
    stderr: TextIO,
    *,
    backend: _DesktopRequestHandler | None = None,
) -> int:
    """Serve NDJSON requests until EOF without retaining project/session authority."""

    selected_backend: _DesktopRequestHandler = DesktopBackend() if backend is None else backend
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
            request = decode_desktop_request(line)
        except DesktopIPCError as error:
            _write_protocol_frame(stdout, encode_desktop_host_error(error))
            continue

        try:
            response = selected_backend.handle(request)
        except Exception as error:
            _write_fatal_backend_error(stderr, error)
            return HOST_EXIT_BACKEND_FAILURE

        _write_protocol_frame(stdout, encode_desktop_response(response))

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

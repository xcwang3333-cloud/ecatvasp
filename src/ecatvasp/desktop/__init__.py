"""Desktop application boundary for ECatVASP."""

from ecatvasp.desktop.host import (
    DESKTOP_HOST_CONTRACT_VERSION,
    HOST_EXIT_BACKEND_FAILURE,
    HOST_EXIT_OK,
    DesktopHostErrorFrame,
    encode_desktop_host_error,
    run_stdio_host,
)
from ecatvasp.desktop.protocol import (
    DESKTOP_IPC_CONTRACT_VERSION,
    DesktopBackend,
    DesktopError,
    DesktopIPCError,
    DesktopOperation,
    DesktopRequest,
    DesktopResponse,
    decode_desktop_request,
    encode_desktop_response,
)

__all__ = [
    "DESKTOP_HOST_CONTRACT_VERSION",
    "DESKTOP_IPC_CONTRACT_VERSION",
    "HOST_EXIT_BACKEND_FAILURE",
    "HOST_EXIT_OK",
    "DesktopBackend",
    "DesktopError",
    "DesktopHostErrorFrame",
    "DesktopIPCError",
    "DesktopOperation",
    "DesktopRequest",
    "DesktopResponse",
    "decode_desktop_request",
    "encode_desktop_host_error",
    "encode_desktop_response",
    "run_stdio_host",
]

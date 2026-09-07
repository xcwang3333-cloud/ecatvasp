"""Desktop application boundary for ECatVASP."""

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
    "DESKTOP_IPC_CONTRACT_VERSION",
    "DesktopBackend",
    "DesktopError",
    "DesktopIPCError",
    "DesktopOperation",
    "DesktopRequest",
    "DesktopResponse",
    "decode_desktop_request",
    "encode_desktop_response",
]

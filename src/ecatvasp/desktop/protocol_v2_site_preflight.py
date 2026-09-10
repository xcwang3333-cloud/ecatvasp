"""Strict real-site preflight request contract for desktop IPC v2."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeGuard

from ecatvasp.desktop.protocol import DesktopIPCError
from ecatvasp.desktop.protocol_v2_common import (
    DESKTOP_IPC_V2_CONTRACT_VERSION,
    DesktopV2Operation,
)

_BASE = frozenset({"protocol_version", "request_id", "operation", "profile"})
_PROFILE_FIELDS = frozenset(
    {
        "site_id",
        "name",
        "host_alias",
        "remote_root",
        "potcar_resolver_id",
        "potcar_family",
        "potcar_root",
        "scheduler_type",
        "ssh_mode",
        "vasp_executable",
        "mpi_launcher",
        "module_loads",
        "bader_executable",
        "lobster_executable",
    }
)
_REQUIRED_PROFILE_FIELDS = (
    "site_id",
    "name",
    "host_alias",
    "remote_root",
    "potcar_resolver_id",
    "potcar_family",
    "potcar_root",
)


@dataclass(frozen=True, slots=True)
class DesktopV2SitePreflightRequest:
    request_id: str
    profile: dict[str, Any]
    protocol_version: str = DESKTOP_IPC_V2_CONTRACT_VERSION
    operation: DesktopV2Operation = DesktopV2Operation.SITE_PREFLIGHT


def is_desktop_v2_site_preflight_request(value: object) -> TypeGuard[DesktopV2SitePreflightRequest]:
    return isinstance(value, DesktopV2SitePreflightRequest)


def decode_desktop_v2_site_preflight_request(
    raw: dict[str, Any],
    *,
    operation: DesktopV2Operation,
    request_id: str,
) -> DesktopV2SitePreflightRequest:
    if operation is not DesktopV2Operation.SITE_PREFLIGHT:
        raise DesktopIPCError("operation is not a Site Preflight request")
    _reject_unknown(raw, _BASE, prefix="request")
    profile = raw.get("profile")
    if not isinstance(profile, dict):
        raise DesktopIPCError("profile must be an object")
    _reject_unknown(profile, _PROFILE_FIELDS, prefix="profile")
    for field in _REQUIRED_PROFILE_FIELDS:
        _required_string(profile, field)
    _optional_enum(profile, "scheduler_type", {"slurm"})
    _optional_enum(profile, "ssh_mode", {"system_openssh"})
    for field in ("vasp_executable", "mpi_launcher", "bader_executable", "lobster_executable"):
        _optional_string(profile, field)
    modules = profile.get("module_loads", [])
    if not isinstance(modules, list) or any(
        not isinstance(item, str) or not item.strip() for item in modules
    ):
        raise DesktopIPCError("profile.module_loads must be a string list")
    if len(modules) != len(set(modules)):
        raise DesktopIPCError("profile.module_loads must not contain duplicates")
    return DesktopV2SitePreflightRequest(request_id=request_id, profile=profile)


def _required_string(raw: dict[str, Any], field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value.strip():
        raise DesktopIPCError(f"profile.{field} must be a non-blank string")
    return value


def _optional_string(raw: dict[str, Any], field: str) -> None:
    value = raw.get(field)
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise DesktopIPCError(f"profile.{field} must be a non-blank string when supplied")


def _optional_enum(raw: dict[str, Any], field: str, allowed: set[str]) -> None:
    value = raw.get(field)
    if value is not None and value not in allowed:
        raise DesktopIPCError(f"profile.{field} is unsupported")


def _reject_unknown(raw: dict[str, Any], allowed: frozenset[str], *, prefix: str) -> None:
    extras = sorted(set(raw) - set(allowed))
    if extras:
        raise DesktopIPCError(f"{prefix} contains unsupported fields: {', '.join(extras)}")


__all__ = [
    "DesktopV2SitePreflightRequest",
    "decode_desktop_v2_site_preflight_request",
    "is_desktop_v2_site_preflight_request",
]

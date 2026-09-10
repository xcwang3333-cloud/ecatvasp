from __future__ import annotations

import json

import pytest

import ecatvasp.desktop.protocol_v2_site_preflight_gateway as gateway
from ecatvasp.api.site_preflight import SitePreflightApplicationService
from ecatvasp.desktop.host import decode_versioned_desktop_request
from ecatvasp.desktop.protocol import DesktopIPCError
from ecatvasp.desktop.protocol_v2_common import DESKTOP_IPC_V2_CONTRACT_VERSION
from ecatvasp.desktop.protocol_v2_site_preflight import DesktopV2SitePreflightRequest
from ecatvasp.desktop.site_preflight import site_preflight_action
from ecatvasp.execution.preflight import (
    PreflightCheck,
    PreflightReport,
    PreflightStatus,
)
from ecatvasp.execution.site_profile import SiteProfile


def _profile() -> dict[str, object]:
    return {
        "site_id": "site-a",
        "name": "Institutional cluster",
        "host_alias": "cluster",
        "remote_root": "/scratch/ecatvasp",
        "potcar_resolver_id": "site-potcars",
        "potcar_family": "PBE_54",
        "potcar_root": "/opt/potcars/pbe54",
        "scheduler_type": "slurm",
        "ssh_mode": "system_openssh",
        "vasp_executable": "vasp_std",
        "mpi_launcher": "srun",
        "module_loads": ["vasp/6.4"],
        "bader_executable": "bader",
    }


def _wire(profile: dict[str, object] | None = None) -> str:
    return json.dumps(
        {
            "protocol_version": DESKTOP_IPC_V2_CONTRACT_VERSION,
            "request_id": "site-preflight-1",
            "operation": "site_preflight",
            "profile": _profile() if profile is None else profile,
        }
    )


def test_block8_decodes_site_preflight_through_host() -> None:
    request = decode_versioned_desktop_request(_wire())
    assert isinstance(request, DesktopV2SitePreflightRequest)
    assert request.profile["site_id"] == "site-a"
    assert request.profile["module_loads"] == ["vasp/6.4"]


def test_site_preflight_decoder_rejects_credential_like_unknown_fields() -> None:
    profile = _profile()
    profile["password"] = "secret"
    with pytest.raises(DesktopIPCError, match="profile contains unsupported fields: password"):
        decode_versioned_desktop_request(_wire(profile))


class _StubSitePreflightApplicationService(SitePreflightApplicationService):
    def __init__(self) -> None:
        pass

    def run(self, site_profile: SiteProfile) -> PreflightReport:
        assert site_profile.site_id == "site-a"
        return PreflightReport(
            site_id=site_profile.site_id,
            profile_hash="a" * 64,
            status=PreflightStatus.READY,
            checks=(
                PreflightCheck(
                    check_name="profile_validation",
                    status=PreflightStatus.READY,
                    reason_code=None,
                    message="profile accepted",
                    evidence=("sanitized=true",),
                ),
            ),
            timestamp="2026-09-11T00:00:00+00:00",
        )


def test_desktop_action_returns_transient_sanitized_report() -> None:
    payload = site_preflight_action(
        _profile(),
        service=_StubSitePreflightApplicationService(),
    )
    assert payload == {
        "site_id": "site-a",
        "profile_hash": "a" * 64,
        "status": "READY",
        "timestamp": "2026-09-11T00:00:00+00:00",
        "transient": True,
        "checks": [
            {
                "check_name": "profile_validation",
                "status": "READY",
                "reason_code": None,
                "message": "profile accepted",
                "evidence": ["sanitized=true"],
            }
        ],
    }
    assert "host_alias" not in payload
    assert "remote_root" not in payload
    assert "potcar_root" not in payload


def test_block8_backend_routes_site_preflight_without_project_state(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = {
        "site_id": "site-a",
        "profile_hash": "b" * 64,
        "status": "READY",
        "timestamp": "2026-09-11T00:00:00+00:00",
        "transient": True,
        "checks": [],
    }
    monkeypatch.setattr(gateway, "site_preflight_action", lambda profile: expected)
    request = gateway.decode_desktop_v2_block8_request(_wire())
    response = gateway.DesktopBackendV2Block8().handle(request)
    assert response.ok is True
    assert response.payload == expected
    assert response.operation.value == "site_preflight"

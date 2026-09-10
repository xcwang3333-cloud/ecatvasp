from ecatvasp.execution.preflight import PreflightService, PreflightStatus
from ecatvasp.execution.site_profile import SiteProfile


def test_site_profile_serialization_and_defaults() -> None:
    profile = SiteProfile(site_id="local", name="Local", hostname="localhost")
    assert profile.to_dict()["ssh_mode"].value == "system_openssh"
    assert profile.validate() == ["remote_root"]


def test_preflight_ready_profile() -> None:
    profile = SiteProfile(
        site_id="cluster",
        name="Cluster",
        hostname="hpc.example",
        remote_root="/scratch/ecatvasp",
    )
    report = PreflightService().run(profile)
    assert report.status is PreflightStatus.READY


def test_preflight_invalid_profile() -> None:
    report = PreflightService().run(
        SiteProfile(site_id="", name="", hostname="", remote_root="")
    )
    assert report.status is PreflightStatus.BLOCKED

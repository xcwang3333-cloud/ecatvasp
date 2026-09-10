from __future__ import annotations

from dataclasses import replace

from ecatvasp.domain import SchedulerType
from ecatvasp.execution.preflight import PreflightService, PreflightStatus
from ecatvasp.execution.site_profile import SiteProfile
from ecatvasp.execution.targets import TransportKind


def _profile() -> SiteProfile:
    return SiteProfile(
        site_id="cluster",
        name="Cluster",
        host_alias="hpc.example",
        remote_root="/scratch/ecatvasp",
        potcar_resolver_id="vasp-pbe",
        potcar_family="PBE_54",
        potcar_root="/apps/vasp/potpaw_PBE.54",
        scheduler_type=SchedulerType.SLURM,
        vasp_executable="vasp_std",
        mpi_launcher="srun",
        module_loads=("vasp/6.4",),
        bader_executable="bader",
        lobster_executable="lobster",
    )


def test_site_profile_resolves_existing_execution_authorities() -> None:
    profile = _profile()

    target = profile.to_execution_target()
    potcars = profile.to_remote_potcar_library()

    assert profile.validate() == []
    assert target.transport is TransportKind.SSH
    assert target.host_alias == profile.host_alias
    assert target.remote_work_root == profile.remote_root
    assert target.scheduler is SchedulerType.SLURM
    assert target.vasp_executable == "vasp_std"
    assert target.launcher == "srun"
    assert target.module_loads == ("vasp/6.4",)
    assert potcars.resolver_id == "vasp-pbe"
    assert potcars.family == "PBE_54"
    assert potcars.root == "/apps/vasp/potpaw_PBE.54"


def test_site_profile_hash_changes_with_operational_configuration() -> None:
    profile = _profile()

    assert profile.profile_hash == replace(profile, name="Display only").profile_hash
    assert profile.profile_hash != replace(profile, vasp_executable="vasp_gam").profile_hash
    assert profile.profile_hash != replace(profile, remote_root="/scratch/ecatvasp-2").profile_hash


def test_preflight_ready_profile_shape() -> None:
    report = PreflightService().run(_profile())

    assert report.status is PreflightStatus.READY


def test_preflight_invalid_profile() -> None:
    profile = replace(_profile(), host_alias="", remote_root="", potcar_root="")

    report = PreflightService().run(profile)

    assert report.status is PreflightStatus.BLOCKED
    assert set(profile.validate()) == {"host_alias", "remote_root", "potcar_root"}

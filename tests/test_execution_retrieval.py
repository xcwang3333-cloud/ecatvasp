from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ecatvasp.domain import (
    ArtifactAvailability,
    ArtifactType,
    Calculation,
    CalculationType,
    ExecutionAttemptStatus,
    ExecutionSettings,
    Project,
    RemoteJob,
    RetrievalPolicy,
    SchedulerState,
    SchedulerType,
    new_method_fingerprint_id,
    new_structure_snapshot_id,
)
from ecatvasp.domain.ids import new_artifact_id
from ecatvasp.execution import (
    CommandResult,
    CommandSpec,
    ExecutionTargetProfile,
    RetrievalError,
    SshSecurityPolicy,
    TargetRelativePath,
    TransportKind,
    create_execution_attempt,
    remote_absolute_path,
    retrieve_remote_outputs,
)
from ecatvasp.vasp.contracts import VaspSystemContext, VaspSystemKind
from ecatvasp.vasp.execution_plan import (
    ExecutionPlan,
    ExpectedOutput,
    PotcarResolutionEntry,
    PotcarResolutionRequest,
    VaspRuntimeConstraints,
)


class _FakeRetrievalTransport:
    transport_kind = TransportKind.SSH

    def __init__(self, *, corrupt_download_name: str | None = None) -> None:
        self.files: dict[str, bytes] = {}
        self.commands: list[tuple[str, ...]] = []
        self.downloads: list[str] = []
        self.corrupt_download_name = corrupt_download_name

    def ensure_directory(
        self,
        *,
        target: ExecutionTargetProfile,
        path: TargetRelativePath,
    ) -> None:
        _ = (target, path)

    def upload(
        self,
        *,
        target: ExecutionTargetProfile,
        local_path: Path,
        destination: TargetRelativePath,
    ) -> None:
        self.files[remote_absolute_path(target, destination)] = local_path.read_bytes()

    def download(
        self,
        *,
        target: ExecutionTargetProfile,
        source: TargetRelativePath,
        local_path: Path,
    ) -> None:
        absolute = remote_absolute_path(target, source)
        body = self.files[absolute]
        if self.corrupt_download_name is not None and source.value.endswith(
            self.corrupt_download_name
        ):
            body += b"corrupt"
        local_path.write_bytes(body)
        self.downloads.append(source.value)

    def run(
        self,
        *,
        target: ExecutionTargetProfile,
        command: CommandSpec,
    ) -> CommandResult:
        self.commands.append(command.argv)
        action = command.argv[0]
        path = command.argv[-1]
        if action == "test":
            return CommandResult(0 if path in self.files else 1)
        if action == "sha256sum":
            body = self.files.get(path)
            if body is None:
                return CommandResult(1, stderr="missing")
            digest = hashlib.sha256(body).hexdigest()
            return CommandResult(0, stdout=f"{digest}  {path}\n")
        if action == "stat":
            body = self.files.get(path)
            if body is None:
                return CommandResult(1, stderr="missing")
            return CommandResult(0, stdout=f"{len(body)}\n")
        if action == "rm":
            if path not in self.files:
                return CommandResult(1, stderr="missing")
            del self.files[path]
            return CommandResult(0)
        return CommandResult(127, stderr="unsupported")


def _target() -> ExecutionTargetProfile:
    return ExecutionTargetProfile(
        target_id="primary-hpc",
        transport=TransportKind.SSH,
        scheduler=SchedulerType.SLURM,
        host_alias="cluster-a",
        remote_work_root="/scratch/ecatvasp",
        potcar_resolver_id="pbe54-remote",
        vasp_executable="vasp_std",
        launcher="srun",
        ssh_security=SshSecurityPolicy(),
    )


def _plan_and_attempt() -> tuple[ExecutionPlan, object, RemoteJob]:
    project = Project(name="Retrieval", slug="retrieval")
    calculation = Calculation(
        project_id=project.id,
        calculation_type=CalculationType.STATIC,
        input_structure_snapshot_id=new_structure_snapshot_id(),
        recipe_id="ECatVASP.VASP.GroundStateStatic",
        method_fingerprint_id=new_method_fingerprint_id(),
    )
    outputs = (
        ExpectedOutput(
            "discardable",
            ArtifactType.STDOUT,
            "scratch.tmp",
            RetrievalPolicy.DISCARDABLE,
            False,
        ),
        ExpectedOutput(
            "on_demand",
            ArtifactType.WAVECAR,
            "WAVECAR",
            RetrievalPolicy.ON_DEMAND,
            True,
        ),
        ExpectedOutput(
            "optional",
            ArtifactType.OSZICAR,
            "OSZICAR",
            RetrievalPolicy.ALWAYS,
            False,
        ),
        ExpectedOutput(
            "outcar",
            ArtifactType.OUTCAR,
            "OUTCAR",
            RetrievalPolicy.ALWAYS,
            True,
        ),
        ExpectedOutput(
            "remote_only",
            ArtifactType.VASPRUN_XML,
            "vasprun.xml",
            RetrievalPolicy.REMOTE_ONLY,
            False,
        ),
    )
    plan = ExecutionPlan(
        calculation_id=calculation.id,
        recipe_id=calculation.recipe_id,
        system_context=VaspSystemContext(VaspSystemKind.PERIODIC_3D),
        input_manifest_artifact_id=new_artifact_id(),
        input_manifest_sha256="a" * 64,
        preparation_hash="b" * 64,
        staging_inputs=(),
        potcar_resolution=PotcarResolutionRequest(
            family="PBE_54",
            core_method_hash="c" * 64,
            metadata_hash="d" * 64,
            entries=(PotcarResolutionEntry("Pb", "Pb_d", "e" * 64),),
        ),
        expected_outputs=outputs,
        runtime_constraints=VaspRuntimeConstraints(),
        execution_settings=ExecutionSettings(),
    )
    attempt = replace(
        create_execution_attempt(plan=plan, calculation=calculation),
        status=ExecutionAttemptStatus.EXITED,
    )
    remote_job = RemoteJob(
        execution_attempt_id=attempt.id,
        scheduler=SchedulerType.SLURM,
        scheduler_job_id="12345",
        remote_directory=f"execution/{attempt.id}",
        state=SchedulerState.COMPLETED,
    )
    return plan, attempt, remote_job


def _seed_outputs(
    *,
    transport: _FakeRetrievalTransport,
    target: ExecutionTargetProfile,
    remote_job: RemoteJob,
    include_optional: bool = False,
    include_on_demand: bool = True,
) -> None:
    bodies = {
        "OUTCAR": b"outcar-body\n",
        "WAVECAR": b"wavecar-body",
        "vasprun.xml": b"<modeling/>\n",
        "scratch.tmp": b"temporary-output\n",
    }
    if include_optional:
        bodies["OSZICAR"] = b"1 F= -.1\n"
    if not include_on_demand:
        del bodies["WAVECAR"]
    for name, body in bodies.items():
        relative = TargetRelativePath(f"{remote_job.remote_directory}/{name}")
        transport.files[remote_absolute_path(target, relative)] = body


def _prepare_root(tmp_path: Path, attempt_id: object) -> None:
    (tmp_path / "artifacts" / "execution" / str(attempt_id)).mkdir(parents=True)


def test_default_retrieval_preserves_policy_and_remote_retention(tmp_path: Path) -> None:
    plan, attempt, remote_job = _plan_and_attempt()
    target = _target()
    transport = _FakeRetrievalTransport()
    _prepare_root(tmp_path, attempt.id)
    _seed_outputs(transport=transport, target=target, remote_job=remote_job)

    result = retrieve_remote_outputs(
        project_root=tmp_path,
        plan=plan,
        attempt=attempt,
        remote_job=remote_job,
        target=target,
        transport=transport,
        retrieved_at=datetime(2026, 9, 3, 7, 0, tzinfo=UTC),
    )

    by_type = {item.artifact_type: item for item in result.output_artifacts}
    assert result.attempt.status is ExecutionAttemptStatus.RETRIEVING
    assert by_type[ArtifactType.OUTCAR].availability is ArtifactAvailability.BOTH
    assert by_type[ArtifactType.WAVECAR].availability is ArtifactAvailability.REMOTE
    assert by_type[ArtifactType.VASPRUN_XML].availability is ArtifactAvailability.REMOTE
    assert by_type[ArtifactType.STDOUT].availability is ArtifactAvailability.REMOTE
    assert by_type[ArtifactType.OSZICAR].availability is ArtifactAvailability.MISSING
    assert (tmp_path / by_type[ArtifactType.OUTCAR].local_path).read_bytes() == b"outcar-body\n"
    assert not any(item.endswith("WAVECAR") for item in transport.downloads)
    manifest_text = (tmp_path / result.manifest_artifact.local_path).read_text()
    assert result.manifest.retrieval_hash
    assert "/scratch/ecatvasp" not in manifest_text
    assert "cluster-a" not in manifest_text
    assert '"retrieval_policy":"remote_only"' in manifest_text


def test_on_demand_retrieval_can_release_remote_only_after_local_verification(
    tmp_path: Path,
) -> None:
    plan, attempt, remote_job = _plan_and_attempt()
    target = _target()
    transport = _FakeRetrievalTransport()
    _prepare_root(tmp_path, attempt.id)
    _seed_outputs(transport=transport, target=target, remote_job=remote_job)

    result = retrieve_remote_outputs(
        project_root=tmp_path,
        plan=plan,
        attempt=attempt,
        remote_job=remote_job,
        target=target,
        transport=transport,
        requested_roles=("on_demand",),
        release_remote_roles=("on_demand",),
    )

    wavecar = next(
        item for item in result.output_artifacts if item.artifact_type is ArtifactType.WAVECAR
    )
    assert wavecar.availability is ArtifactAvailability.LOCAL
    assert wavecar.remote_path is None
    assert wavecar.local_path is not None
    assert (tmp_path / wavecar.local_path).read_bytes() == b"wavecar-body"
    remote = remote_absolute_path(
        target,
        TargetRelativePath(f"{remote_job.remote_directory}/WAVECAR"),
    )
    assert remote not in transport.files


def test_discardable_output_can_be_explicitly_removed_without_local_copy(tmp_path: Path) -> None:
    plan, attempt, remote_job = _plan_and_attempt()
    target = _target()
    transport = _FakeRetrievalTransport()
    _prepare_root(tmp_path, attempt.id)
    _seed_outputs(transport=transport, target=target, remote_job=remote_job)

    result = retrieve_remote_outputs(
        project_root=tmp_path,
        plan=plan,
        attempt=attempt,
        remote_job=remote_job,
        target=target,
        transport=transport,
        discard_remote_roles=("discardable",),
    )

    scratch = next(
        item for item in result.output_artifacts if item.artifact_type is ArtifactType.STDOUT
    )
    assert scratch.availability is ArtifactAvailability.MISSING
    assert scratch.local_path is None
    assert scratch.remote_path is None
    assert scratch.sha256 is not None
    record = next(item for item in result.manifest.files if item.role == "discardable")
    assert record.remote_present
    assert not record.remote_retained
    assert record.remote_sha256 == scratch.sha256


def test_remote_only_policy_rejects_local_request_and_release_before_transport(tmp_path: Path) -> None:
    plan, attempt, remote_job = _plan_and_attempt()
    target = _target()
    transport = _FakeRetrievalTransport()
    _prepare_root(tmp_path, attempt.id)
    _seed_outputs(transport=transport, target=target, remote_job=remote_job)

    with pytest.raises(RetrievalError, match="REMOTE_ONLY"):
        retrieve_remote_outputs(
            project_root=tmp_path,
            plan=plan,
            attempt=attempt,
            remote_job=remote_job,
            target=target,
            transport=transport,
            requested_roles=("remote_only",),
        )
    assert not transport.commands


def test_required_missing_output_fails_for_completed_attempt_but_is_recorded_for_failed_run(
    tmp_path: Path,
) -> None:
    plan, attempt, remote_job = _plan_and_attempt()
    target = _target()
    transport = _FakeRetrievalTransport()
    _prepare_root(tmp_path, attempt.id)
    _seed_outputs(
        transport=transport,
        target=target,
        remote_job=remote_job,
        include_on_demand=False,
    )

    with pytest.raises(RetrievalError, match="required remote output is missing: on_demand"):
        retrieve_remote_outputs(
            project_root=tmp_path,
            plan=plan,
            attempt=attempt,
            remote_job=remote_job,
            target=target,
            transport=transport,
        )

    failed_attempt = replace(attempt, status=ExecutionAttemptStatus.FAILED)
    failed_job = replace(remote_job, state=SchedulerState.FAILED)
    result = retrieve_remote_outputs(
        project_root=tmp_path,
        plan=plan,
        attempt=failed_attempt,
        remote_job=failed_job,
        target=target,
        transport=transport,
        retrieved_at=datetime(2026, 9, 3, 7, 1, tzinfo=UTC),
    )
    assert result.attempt.status is ExecutionAttemptStatus.FAILED
    wavecar = next(
        item for item in result.output_artifacts if item.artifact_type is ArtifactType.WAVECAR
    )
    assert wavecar.availability is ArtifactAvailability.MISSING


def test_download_checksum_mismatch_fails_closed_without_manifest(tmp_path: Path) -> None:
    plan, attempt, remote_job = _plan_and_attempt()
    target = _target()
    transport = _FakeRetrievalTransport(corrupt_download_name="OUTCAR")
    _prepare_root(tmp_path, attempt.id)
    _seed_outputs(transport=transport, target=target, remote_job=remote_job)

    with pytest.raises(RetrievalError, match="size does not match|SHA-256 does not match"):
        retrieve_remote_outputs(
            project_root=tmp_path,
            plan=plan,
            attempt=attempt,
            remote_job=remote_job,
            target=target,
            transport=transport,
        )

    output = tmp_path / "artifacts" / "execution" / str(attempt.id) / "outputs" / "OUTCAR"
    assert not output.exists()
    provenance = tmp_path / "artifacts" / "execution" / str(attempt.id)
    assert not list(provenance.glob("retrieval-*.json"))


def test_nonterminal_scheduler_state_is_not_retrievable(tmp_path: Path) -> None:
    plan, attempt, remote_job = _plan_and_attempt()
    target = _target()
    transport = _FakeRetrievalTransport()
    _prepare_root(tmp_path, attempt.id)

    with pytest.raises(RetrievalError, match="terminal scheduler state"):
        retrieve_remote_outputs(
            project_root=tmp_path,
            plan=plan,
            attempt=attempt,
            remote_job=replace(remote_job, state=SchedulerState.RUNNING),
            target=target,
            transport=transport,
        )
    assert not transport.commands

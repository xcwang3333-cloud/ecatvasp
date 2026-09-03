from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ecatvasp.domain import (
    ArtifactAvailability,
    ArtifactType,
    Calculation,
    CalculationType,
    ExecutionSettings,
    Project,
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
    RemoteStageManifest,
    RemoteStagePackage,
    SchedulerAdapter,
    SlurmAdapter,
    SlurmSubmissionError,
    SshSecurityPolicy,
    TargetRelativePath,
    TransportKind,
    create_execution_attempt,
    remote_absolute_path,
    render_slurm_job_script,
    resolve_scheduler_resources,
    submit_remote_slurm,
)
from ecatvasp.vasp.contracts import VaspSystemContext, VaspSystemKind
from ecatvasp.vasp.execution_plan import (
    ExecutionPlan,
    PotcarResolutionEntry,
    PotcarResolutionRequest,
    VaspRuntimeConstraints,
)


class _FakeSlurmTransport:
    transport_kind = TransportKind.SSH

    def __init__(
        self,
        *,
        corrupt_job_script: bool = False,
        sbatch_exit_code: int = 0,
        sbatch_stdout: str = "12345;cluster-a\n",
        sbatch_stderr: str = "",
    ) -> None:
        self.files: dict[str, bytes] = {}
        self.commands: list[tuple[str, ...]] = []
        self.corrupt_job_script = corrupt_job_script
        self.sbatch_exit_code = sbatch_exit_code
        self.sbatch_stdout = sbatch_stdout
        self.sbatch_stderr = sbatch_stderr

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
        body = local_path.read_bytes()
        if self.corrupt_job_script and destination.value.endswith("job.slurm"):
            body += b"corrupt"
        self.files[remote_absolute_path(target, destination)] = body

    def download(
        self,
        *,
        target: ExecutionTargetProfile,
        source: TargetRelativePath,
        local_path: Path,
    ) -> None:
        local_path.write_bytes(self.files[remote_absolute_path(target, source)])

    def run(
        self,
        *,
        target: ExecutionTargetProfile,
        command: CommandSpec,
    ) -> CommandResult:
        self.commands.append(command.argv)
        if command.argv[0] == "sha256sum":
            path = command.argv[-1]
            body = self.files.get(path)
            if body is None:
                return CommandResult(1, stderr="missing")
            digest = hashlib.sha256(body).hexdigest()
            return CommandResult(0, stdout=f"{digest}  {path}\n")
        if command.argv[0] == "stat":
            path = command.argv[-1]
            body = self.files.get(path)
            if body is None:
                return CommandResult(1, stderr="missing")
            return CommandResult(0, stdout=f"{len(body)}\n")
        if command.argv[0] == "sbatch":
            return CommandResult(
                self.sbatch_exit_code,
                stdout=self.sbatch_stdout,
                stderr=self.sbatch_stderr,
            )
        return CommandResult(127, stderr="unsupported")


def _calculation(project: Project) -> Calculation:
    return Calculation(
        project_id=project.id,
        calculation_type=CalculationType.RELAX,
        input_structure_snapshot_id=new_structure_snapshot_id(),
        recipe_id="ECatVASP.VASP.AdsorbateRelax",
        method_fingerprint_id=new_method_fingerprint_id(),
    )


def _settings(**overrides: object) -> ExecutionSettings:
    values: dict[str, object] = {
        "ncore": 2,
        "kpar": 2,
        "nodes": 2,
        "cores": 16,
        "memory_mb": 32000,
        "walltime_seconds": 5400,
        "partition": "compute",
        "mpi_ranks": 8,
        "omp_threads": 2,
        "executable": "vasp_std",
    }
    values.update(overrides)
    return ExecutionSettings(**values)  # type: ignore[arg-type]


def _plan(calculation: Calculation, settings: ExecutionSettings) -> ExecutionPlan:
    return ExecutionPlan(
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
        expected_outputs=(),
        runtime_constraints=VaspRuntimeConstraints(),
        execution_settings=settings,
    )


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
        module_loads=("vasp/6.5.1",),
        ssh_security=SshSecurityPolicy(),
    )


def _staged(tmp_path: Path, settings: ExecutionSettings | None = None) -> RemoteStagePackage:
    project = Project(name="Slurm", slug="slurm")
    calculation = _calculation(project)
    plan = _plan(calculation, settings or _settings())
    attempt = create_execution_attempt(plan=plan, calculation=calculation)
    staged_attempt = replace(attempt, status=attempt.status.STAGING)
    target = _target()
    remote_directory = TargetRelativePath(f"execution/{attempt.id}")
    manifest = RemoteStageManifest(
        attempt_id=attempt.id,
        plan_hash=plan.plan_hash,
        execution_settings_hash=plan.execution_settings_hash,
        environment=target.sanitized_environment(),
        remote_directory=remote_directory.value,
        files=(),
    )
    (tmp_path / "artifacts" / "execution" / str(attempt.id)).mkdir(parents=True)
    return RemoteStagePackage(
        project_root=tmp_path,
        plan=plan,
        target=target,
        attempt=staged_attempt,
        remote_directory=remote_directory,
        manifest=manifest,
        artifacts=(),
    )


def test_resolve_scheduler_resources_is_exact_and_deterministic() -> None:
    resources = resolve_scheduler_resources(_settings())
    repeated = resolve_scheduler_resources(_settings())

    assert resources.nodes == 2
    assert resources.mpi_ranks == 8
    assert resources.omp_threads == 2
    assert resources.ranks_per_node == 4
    assert resources.cores_per_node == 8
    assert resources.memory_mb_per_node == 16000
    assert resources.queue_name == "compute"
    assert resources.resource_hash == repeated.resource_hash


def test_resource_resolution_fails_closed_on_ambiguous_or_invalid_topology() -> None:
    with pytest.raises(SlurmSubmissionError, match="omp_threads must be explicit"):
        resolve_scheduler_resources(_settings(omp_threads=None))
    with pytest.raises(SlurmSubmissionError, match="exact CPU topology"):
        resolve_scheduler_resources(_settings(cores=20))
    with pytest.raises(SlurmSubmissionError, match="divisible by KPAR"):
        resolve_scheduler_resources(_settings(mpi_ranks=10, cores=20, kpar=4))
    with pytest.raises(SlurmSubmissionError, match="divisible by NCORE"):
        resolve_scheduler_resources(_settings(ncore=3))
    with pytest.raises(SlurmSubmissionError, match="memory_mb must be divisible"):
        resolve_scheduler_resources(_settings(memory_mb=32001))


def test_job_script_fixes_resources_environment_and_launch_without_remote_root(
    tmp_path: Path,
) -> None:
    staged = _staged(tmp_path)
    resources = resolve_scheduler_resources(staged.plan.execution_settings)
    script = render_slurm_job_script(staged, resources)

    assert "#SBATCH --nodes=2" in script.text
    assert "#SBATCH --ntasks=8" in script.text
    assert "#SBATCH --ntasks-per-node=4" in script.text
    assert "#SBATCH --cpus-per-task=2" in script.text
    assert "#SBATCH --time=01:30:00" in script.text
    assert "#SBATCH --mem=16000M" in script.text
    assert "#SBATCH --partition=compute" in script.text
    assert "module load vasp/6.5.1" in script.text
    assert "export OMP_NUM_THREADS=2" in script.text
    assert "exec srun vasp_std" in script.text
    assert "/scratch/ecatvasp" not in script.text
    assert "cluster-a" not in script.text


def test_submit_remote_slurm_creates_remote_job_and_attempt_artifacts(tmp_path: Path) -> None:
    staged = _staged(tmp_path)
    transport = _FakeSlurmTransport()
    scheduler = SlurmAdapter(transport)
    submitted_at = datetime(2026, 9, 3, 5, 40, tzinfo=timezone.utc)

    assert isinstance(scheduler, SchedulerAdapter)
    result = submit_remote_slurm(
        staged=staged,
        transport=transport,
        scheduler=scheduler,
        submitted_at=submitted_at,
    )

    assert result.attempt.status.value == "queued"
    assert result.remote_job.scheduler is SchedulerType.SLURM
    assert result.remote_job.scheduler_job_id == "12345"
    assert result.remote_job.state is SchedulerState.PENDING
    assert result.remote_job.submitted_at == submitted_at
    assert result.remote_job.remote_directory == staged.remote_directory.value
    assert {artifact.artifact_type for artifact in result.artifacts} == {
        ArtifactType.JOB_SCRIPT,
        ArtifactType.SCHEDULER_RECORD,
    }
    job_artifact = next(
        item for item in result.artifacts if item.artifact_type is ArtifactType.JOB_SCRIPT
    )
    assert job_artifact.availability is ArtifactAvailability.BOTH
    assert job_artifact.sha256 == result.job_script.sha256

    sbatch = next(command for command in transport.commands if command[0] == "sbatch")
    absolute_stage = remote_absolute_path(staged.target, staged.remote_directory)
    assert sbatch[:2] == ("sbatch", "--parsable")
    assert sbatch[2] == f"--chdir={absolute_stage}"
    assert sbatch[3] == f"{absolute_stage}/job.slurm"

    record = (
        tmp_path
        / "artifacts"
        / "execution"
        / str(staged.attempt.id)
        / "scheduler-submit.json"
    ).read_text()
    assert '"scheduler_job_id":"12345"' in record
    assert result.resources.resource_hash in record
    assert result.job_script.sha256 in record
    assert "/scratch/ecatvasp" not in record
    assert "cluster-a" not in record


def test_submission_fails_before_sbatch_when_uploaded_script_is_corrupted(tmp_path: Path) -> None:
    staged = _staged(tmp_path)
    transport = _FakeSlurmTransport(corrupt_job_script=True)

    with pytest.raises(SlurmSubmissionError, match="SHA-256 mismatch"):
        submit_remote_slurm(
            staged=staged,
            transport=transport,
            scheduler=SlurmAdapter(transport),
        )

    assert not any(command[0] == "sbatch" for command in transport.commands)
    assert not (
        tmp_path
        / "artifacts"
        / "execution"
        / str(staged.attempt.id)
        / "job.slurm"
    ).exists()


def test_sbatch_failure_or_malformed_identity_does_not_create_remote_job_record(
    tmp_path: Path,
) -> None:
    staged = _staged(tmp_path)
    transport = _FakeSlurmTransport(sbatch_exit_code=1, sbatch_stderr="invalid partition")
    with pytest.raises(SlurmSubmissionError, match="invalid partition"):
        submit_remote_slurm(
            staged=staged,
            transport=transport,
            scheduler=SlurmAdapter(transport),
        )
    record = (
        tmp_path
        / "artifacts"
        / "execution"
        / str(staged.attempt.id)
        / "scheduler-submit.json"
    )
    assert not record.exists()

    malformed_stage = _staged(tmp_path / "second")
    malformed_transport = _FakeSlurmTransport(sbatch_stdout="Submitted batch job 99\n")
    with pytest.raises(SlurmSubmissionError, match="unsupported Slurm job id"):
        submit_remote_slurm(
            staged=malformed_stage,
            transport=malformed_transport,
            scheduler=SlurmAdapter(malformed_transport),
        )


def test_slurm_monitoring_and_cancellation_remain_deferred(tmp_path: Path) -> None:
    staged = _staged(tmp_path)
    transport = _FakeSlurmTransport()
    scheduler = SlurmAdapter(transport)

    with pytest.raises(SlurmSubmissionError, match="Block 6"):
        scheduler.query(target=staged.target, scheduler_job_id="12345")
    with pytest.raises(SlurmSubmissionError, match="Block 6"):
        scheduler.cancel(target=staged.target, scheduler_job_id="12345")

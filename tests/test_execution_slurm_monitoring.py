from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from ecatvasp.domain import (
    ArtifactAvailability,
    ArtifactType,
    ExecutionAttempt,
    ExecutionAttemptStatus,
    RemoteJob,
    SchedulerState,
    SchedulerType,
)
from ecatvasp.domain.ids import new_calculation_id
from ecatvasp.execution import (
    CommandResult,
    CommandSpec,
    ExecutionMonitoringError,
    ExecutionTargetProfile,
    SlurmAdapter,
    SlurmObservationError,
    SshSecurityPolicy,
    TargetRelativePath,
    TransportKind,
    cancel_remote_slurm,
    monitor_remote_slurm,
    remote_absolute_path,
)


class _MonitoringTransport:
    transport_kind = TransportKind.SSH

    def __init__(
        self,
        *,
        squeue_stdout: str = "RUNNING\n",
        squeue_exit_code: int = 0,
        squeue_stderr: str = "",
        sacct_stdout: str = "",
        sacct_exit_code: int = 0,
        sacct_stderr: str = "",
        scancel_exit_code: int = 0,
        after_cancel_squeue: str | None = None,
    ) -> None:
        self.squeue_stdout = squeue_stdout
        self.squeue_exit_code = squeue_exit_code
        self.squeue_stderr = squeue_stderr
        self.sacct_stdout = sacct_stdout
        self.sacct_exit_code = sacct_exit_code
        self.sacct_stderr = sacct_stderr
        self.scancel_exit_code = scancel_exit_code
        self.after_cancel_squeue = after_cancel_squeue
        self.files: dict[str, str] = {}
        self.commands: list[tuple[str, ...]] = []

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
        _ = (target, local_path, destination)

    def download(
        self,
        *,
        target: ExecutionTargetProfile,
        source: TargetRelativePath,
        local_path: Path,
    ) -> None:
        _ = (target, source, local_path)

    def run(
        self,
        *,
        target: ExecutionTargetProfile,
        command: CommandSpec,
    ) -> CommandResult:
        self.commands.append(command.argv)
        executable = command.argv[0]
        if executable == "squeue":
            return CommandResult(
                self.squeue_exit_code,
                stdout=self.squeue_stdout,
                stderr=self.squeue_stderr,
            )
        if executable == "sacct":
            return CommandResult(
                self.sacct_exit_code,
                stdout=self.sacct_stdout,
                stderr=self.sacct_stderr,
            )
        if executable == "scancel":
            if self.scancel_exit_code == 0 and self.after_cancel_squeue is not None:
                self.squeue_stdout = self.after_cancel_squeue
            return CommandResult(self.scancel_exit_code, stderr="cancel failed")
        if executable == "test":
            return CommandResult(0 if command.argv[-1] in self.files else 1)
        if executable == "tail":
            path = command.argv[-1]
            if path not in self.files:
                return CommandResult(1, stderr="missing")
            return CommandResult(0, stdout=self.files[path])
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
        module_loads=("vasp/6.5.1",),
        ssh_security=SshSecurityPolicy(),
    )


def _attempt(status: ExecutionAttemptStatus = ExecutionAttemptStatus.QUEUED) -> ExecutionAttempt:
    return ExecutionAttempt(
        calculation_id=new_calculation_id(),
        attempt_number=1,
        status=status,
        input_manifest_hash="a" * 64,
        execution_plan_hash="b" * 64,
    )


def _remote_job(attempt: ExecutionAttempt) -> RemoteJob:
    return RemoteJob(
        execution_attempt_id=attempt.id,
        scheduler=SchedulerType.SLURM,
        scheduler_job_id="12345",
        remote_directory=f"execution/{attempt.id}",
        state=SchedulerState.PENDING,
        submitted_at=datetime(2026, 9, 3, 6, 0, tzinfo=UTC),
    )


def _prepare_project(tmp_path: Path, attempt: ExecutionAttempt) -> None:
    (tmp_path / "artifacts" / "execution" / str(attempt.id)).mkdir(parents=True)


def _install_runtime_files(
    transport: _MonitoringTransport,
    target: ExecutionTargetProfile,
    remote_job: RemoteJob,
) -> None:
    oszicar = TargetRelativePath(f"{remote_job.remote_directory}/OSZICAR")
    outcar = TargetRelativePath(f"{remote_job.remote_directory}/OUTCAR")
    transport.files[remote_absolute_path(target, oszicar)] = (
        " DAV:   1    -1.0\n"
        " DAV:   6    -1.1\n"
        "   7 F= -.123 E0= -.120 d E =-.001\n"
    )
    transport.files[remote_absolute_path(target, outcar)] = (
        "reached required accuracy - stopping structural energy minimisation\n"
        "General timing and accounting informations for this job:\n"
    )


def test_slurm_query_prefers_squeue_and_duplicates_single_job_id() -> None:
    transport = _MonitoringTransport(squeue_stdout="RUNNING\n")
    scheduler = SlurmAdapter(transport)

    observation = scheduler.query(target=_target(), scheduler_job_id="12345")

    assert observation.state is SchedulerState.RUNNING
    assert observation.raw_state == "RUNNING"
    squeue = transport.commands[0]
    assert squeue == (
        "squeue",
        "--noheader",
        "--jobs=12345,12345",
        "--format=%T",
    )
    assert not any(command[0] == "sacct" for command in transport.commands)


def test_slurm_query_falls_back_to_sacct_and_filters_job_steps() -> None:
    transport = _MonitoringTransport(
        squeue_stdout="",
        sacct_stdout="12345|COMPLETED|\n12345.batch|FAILED|\n",
    )

    observation = SlurmAdapter(transport).query(
        target=_target(),
        scheduler_job_id="12345",
    )

    assert observation.state is SchedulerState.COMPLETED
    assert observation.raw_state == "COMPLETED"


def test_unknown_and_lost_are_distinct_scheduler_truths() -> None:
    unknown_transport = _MonitoringTransport(
        squeue_stdout="",
        sacct_stdout="12345|FUTURE_STATE|\n",
    )
    unknown = SlurmAdapter(unknown_transport).query(
        target=_target(),
        scheduler_job_id="12345",
    )
    assert unknown.state is SchedulerState.UNKNOWN
    assert unknown.raw_state == "FUTURE_STATE"

    lost_transport = _MonitoringTransport(squeue_stdout="", sacct_stdout="")
    lost = SlurmAdapter(lost_transport).query(
        target=_target(),
        scheduler_job_id="12345",
    )
    assert lost.state is SchedulerState.LOST
    assert lost.raw_state == "NOT_FOUND"


def test_scheduler_command_failure_is_not_relabelled_unknown_or_lost() -> None:
    with pytest.raises(SlurmObservationError, match="squeue query failed"):
        SlurmAdapter(
            _MonitoringTransport(squeue_exit_code=1, squeue_stderr="controller unavailable")
        ).query(target=_target(), scheduler_job_id="12345")

    with pytest.raises(SlurmObservationError, match="sacct query failed"):
        SlurmAdapter(
            _MonitoringTransport(
                squeue_stdout="",
                sacct_exit_code=1,
                sacct_stderr="accounting unavailable",
            )
        ).query(target=_target(), scheduler_job_id="12345")


def test_running_reconciliation_records_bounded_vasp_telemetry(tmp_path: Path) -> None:
    attempt = _attempt()
    remote_job = _remote_job(attempt)
    _prepare_project(tmp_path, attempt)
    target = _target()
    transport = _MonitoringTransport(squeue_stdout="RUNNING\n")
    _install_runtime_files(transport, target, remote_job)
    observed_at = datetime(2026, 9, 3, 6, 5, tzinfo=UTC)

    result = monitor_remote_slurm(
        project_root=tmp_path,
        attempt=attempt,
        remote_job=remote_job,
        target=target,
        transport=transport,
        scheduler=SlurmAdapter(transport),
        observed_at=observed_at,
    )

    assert result.attempt.status is ExecutionAttemptStatus.RUNNING
    assert result.remote_job.state is SchedulerState.RUNNING
    assert result.progress.oszicar_present is True
    assert result.progress.outcar_present is True
    assert result.progress.ionic_step == 7
    assert result.progress.electronic_iteration == 6
    assert result.progress.reached_required_accuracy_marker is True
    assert result.progress.timing_footer_marker is True
    assert result.artifact.artifact_type is ArtifactType.SCHEDULER_RECORD
    assert result.artifact.availability is ArtifactAvailability.LOCAL
    assert result.artifact.local_path is not None
    record = (tmp_path / result.artifact.local_path).read_text()
    assert '"scheduler_state":"running"' in record
    assert '"ionic_step":7' in record
    assert "/scratch/ecatvasp" not in record
    assert "cluster-a" not in record
    tail_commands = [command for command in transport.commands if command[0] == "tail"]
    assert {command[2] for command in tail_commands} == {"40", "200"}


def test_scheduler_completed_means_process_exit_not_scientific_convergence(
    tmp_path: Path,
) -> None:
    attempt = _attempt(status=ExecutionAttemptStatus.RUNNING)
    remote_job = _remote_job(attempt)
    _prepare_project(tmp_path, attempt)
    target = _target()
    transport = _MonitoringTransport(
        squeue_stdout="",
        sacct_stdout="12345|COMPLETED|\n",
    )
    _install_runtime_files(transport, target, remote_job)

    result = monitor_remote_slurm(
        project_root=tmp_path,
        attempt=attempt,
        remote_job=remote_job,
        target=target,
        transport=transport,
        scheduler=SlurmAdapter(transport),
        observed_at=datetime(2026, 9, 3, 6, 10, tzinfo=UTC),
    )

    assert result.remote_job.state is SchedulerState.COMPLETED
    assert result.attempt.status is ExecutionAttemptStatus.EXITED
    assert result.progress.reached_required_accuracy_marker is True


def test_unknown_or_lost_preserves_attempt_state_and_skips_vasp_tail(tmp_path: Path) -> None:
    attempt = _attempt(status=ExecutionAttemptStatus.RUNNING)
    remote_job = _remote_job(attempt)
    _prepare_project(tmp_path, attempt)
    target = _target()
    transport = _MonitoringTransport(squeue_stdout="", sacct_stdout="")

    result = monitor_remote_slurm(
        project_root=tmp_path,
        attempt=attempt,
        remote_job=remote_job,
        target=target,
        transport=transport,
        scheduler=SlurmAdapter(transport),
        observed_at=datetime(2026, 9, 3, 6, 15, tzinfo=UTC),
    )

    assert result.observation.state is SchedulerState.LOST
    assert result.attempt.status is ExecutionAttemptStatus.RUNNING
    assert not any(command[0] == "tail" for command in transport.commands)


def test_pending_cannot_regress_a_running_attempt(tmp_path: Path) -> None:
    attempt = _attempt(status=ExecutionAttemptStatus.RUNNING)
    remote_job = _remote_job(attempt)
    _prepare_project(tmp_path, attempt)
    transport = _MonitoringTransport(squeue_stdout="PENDING\n")

    with pytest.raises(ExecutionMonitoringError, match="regress"):
        monitor_remote_slurm(
            project_root=tmp_path,
            attempt=attempt,
            remote_job=remote_job,
            target=_target(),
            transport=transport,
            scheduler=SlurmAdapter(transport),
        )


def test_cancel_reconciles_observed_state_without_fabricating_cancellation(
    tmp_path: Path,
) -> None:
    attempt = _attempt(status=ExecutionAttemptStatus.RUNNING)
    remote_job = _remote_job(attempt)
    _prepare_project(tmp_path, attempt)
    target = _target()
    transport = _MonitoringTransport(
        squeue_stdout="RUNNING\n",
        after_cancel_squeue="CANCELLED\n",
    )

    result = cancel_remote_slurm(
        project_root=tmp_path,
        attempt=attempt,
        remote_job=remote_job,
        target=target,
        transport=transport,
        scheduler=SlurmAdapter(transport),
        observed_at=datetime(2026, 9, 3, 6, 20, tzinfo=UTC),
    )

    assert result.observation.state is SchedulerState.CANCELLED
    assert result.remote_job.state is SchedulerState.CANCELLED
    assert result.attempt.status is ExecutionAttemptStatus.CANCELLED
    assert any(command == ("scancel", "12345") for command in transport.commands)

    second_attempt = _attempt(status=ExecutionAttemptStatus.RUNNING)
    second_job = _remote_job(second_attempt)
    second_root = tmp_path / "second"
    _prepare_project(second_root, second_attempt)
    still_running = _MonitoringTransport(
        squeue_stdout="RUNNING\n",
        after_cancel_squeue="RUNNING\n",
    )
    second = cancel_remote_slurm(
        project_root=second_root,
        attempt=second_attempt,
        remote_job=second_job,
        target=target,
        transport=still_running,
        scheduler=SlurmAdapter(still_running),
        observed_at=datetime(2026, 9, 3, 6, 21, tzinfo=UTC),
    )
    assert second.observation.state is SchedulerState.RUNNING
    assert second.attempt.status is ExecutionAttemptStatus.RUNNING

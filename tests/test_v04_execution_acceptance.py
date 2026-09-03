from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ecatvasp.domain import (
    Artifact,
    ArtifactAvailability,
    ArtifactType,
    Calculation,
    CalculationScientificStatus,
    CalculationType,
    ExecutionAttemptProducerRef,
    ExecutionAttemptStatus,
    ExecutionSettings,
    Project,
    RemoteJob,
    RetrievalPolicy,
    SchedulerState,
    SchedulerType,
    new_artifact_id,
    new_method_fingerprint_id,
    new_structure_snapshot_id,
)
from ecatvasp.execution import (
    BatchConcurrencyPolicy,
    BatchNodeState,
    CommandResult,
    CommandSpec,
    ExecutionAcceptanceError,
    ExecutionHandoffStage,
    ExecutionTargetProfile,
    RemotePotcarLibrary,
    SchedulerDag,
    SchedulerDagNode,
    SlurmAdapter,
    SshSecurityPolicy,
    TargetRelativePath,
    TransportKind,
    create_execution_attempt,
    monitor_remote_slurm,
    reconcile_batch_dispatch,
    remote_absolute_path,
    retrieve_remote_outputs,
    stage_remote_runtime,
    submit_remote_slurm,
    validate_v04_execution_handoff,
)
from ecatvasp.vasp.contracts import VaspSystemContext, VaspSystemKind
from ecatvasp.vasp.execution_plan import (
    ExecutionPlan,
    ExpectedOutput,
    PotcarResolutionEntry,
    PotcarResolutionRequest,
    StagingInput,
    StagingInputKind,
    VaspRuntimeConstraints,
)


class _AcceptanceTransport:
    transport_kind = TransportKind.SSH

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.directories: set[str] = set()
        self.commands: list[tuple[str, ...]] = []
        self.squeue_stdout = "PENDING\n"
        self.sacct_stdout = ""

    def ensure_directory(
        self,
        *,
        target: ExecutionTargetProfile,
        path: TargetRelativePath,
    ) -> None:
        self.directories.add(remote_absolute_path(target, path))

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
        local_path.write_bytes(self.files[remote_absolute_path(target, source)])

    def run(
        self,
        *,
        target: ExecutionTargetProfile,
        command: CommandSpec,
    ) -> CommandResult:
        self.commands.append(command.argv)
        action = command.argv[0]
        if action == "mkdir":
            path = command.argv[-1]
            if path in self.directories:
                return CommandResult(1, stderr="exists")
            self.directories.add(path)
            return CommandResult(0)
        if action == "sha256sum":
            path = command.argv[-1]
            body = self.files.get(path)
            if body is None:
                return CommandResult(1, stderr="missing")
            digest = hashlib.sha256(body).hexdigest()
            return CommandResult(0, stdout=f"{digest}  {path}\n")
        if action == "stat":
            path = command.argv[-1]
            body = self.files.get(path)
            if body is None:
                return CommandResult(1, stderr="missing")
            return CommandResult(0, stdout=f"{len(body)}\n")
        if action == "cp":
            source, destination = command.argv[-2:]
            body = self.files.get(source)
            if body is None:
                return CommandResult(1, stderr="missing")
            self.files[destination] = body
            return CommandResult(0)
        if action == "dd":
            source = next(item[3:] for item in command.argv if item.startswith("if="))
            destination = next(item[3:] for item in command.argv if item.startswith("of="))
            body = self.files.get(source)
            if body is None:
                return CommandResult(1, stderr="missing")
            self.files[destination] = self.files.get(destination, b"") + body
            return CommandResult(0)
        if action == "sbatch":
            return CommandResult(0, stdout="12345;cluster-a\n")
        if action == "squeue":
            return CommandResult(0, stdout=self.squeue_stdout)
        if action == "sacct":
            return CommandResult(0, stdout=self.sacct_stdout)
        if action == "test":
            return CommandResult(0 if command.argv[-1] in self.files else 1)
        if action == "tail":
            body = self.files.get(command.argv[-1])
            if body is None:
                return CommandResult(1, stderr="missing")
            return CommandResult(0, stdout=body.decode("utf-8"))
        if action == "rm":
            path = command.argv[-1]
            if path not in self.files:
                return CommandResult(1, stderr="missing")
            del self.files[path]
            return CommandResult(0)
        return CommandResult(127, stderr="unsupported")


def _calculation(project: Project) -> Calculation:
    return Calculation(
        project_id=project.id,
        calculation_type=CalculationType.RELAX,
        input_structure_snapshot_id=new_structure_snapshot_id(),
        recipe_id="ECatVASP.VASP.AdsorbateRelax",
        method_fingerprint_id=new_method_fingerprint_id(),
        status=CalculationScientificStatus.READY,
        slug="pb3-cooh-relax",
    )


def _write_input(root: Path, name: str, body: bytes) -> StagingInput:
    source = root / "inputs" / name
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(body)
    artifact_type = {
        "INCAR": ArtifactType.INCAR,
        "KPOINTS": ArtifactType.KPOINTS,
        "POSCAR": ArtifactType.POSCAR,
    }[name]
    return StagingInput(
        role=name.lower(),
        kind=StagingInputKind.VASP_INPUT,
        artifact_id=new_artifact_id(),
        artifact_type=artifact_type,
        source_relative_path=f"inputs/{name}",
        target_relative_path=name,
        sha256=hashlib.sha256(body).hexdigest(),
        size_bytes=len(body),
    )


def _plan(root: Path, calculation: Calculation, potcar_body: bytes) -> ExecutionPlan:
    staging = tuple(
        sorted(
            (
                _write_input(root, "INCAR", b"ENCUT = 450\nEDIFF = 1E-5\n"),
                _write_input(root, "KPOINTS", b"Gamma\n0\nGamma\n1 1 1\n0 0 0\n"),
                _write_input(root, "POSCAR", b"Pb\n1.0\n"),
            ),
            key=lambda item: item.role,
        )
    )
    outputs = (
        ExpectedOutput(
            "oszicar",
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
            "wavecar",
            ArtifactType.WAVECAR,
            "WAVECAR",
            RetrievalPolicy.ON_DEMAND,
            False,
        ),
    )
    return ExecutionPlan(
        calculation_id=calculation.id,
        recipe_id=calculation.recipe_id,
        system_context=VaspSystemContext(VaspSystemKind.PERIODIC_3D),
        input_manifest_artifact_id=new_artifact_id(),
        input_manifest_sha256="a" * 64,
        preparation_hash="b" * 64,
        staging_inputs=staging,
        potcar_resolution=PotcarResolutionRequest(
            family="PBE_54",
            core_method_hash="c" * 64,
            metadata_hash="d" * 64,
            entries=(
                PotcarResolutionEntry(
                    "Pb",
                    "Pb_d",
                    hashlib.sha256(potcar_body).hexdigest(),
                ),
            ),
        ),
        expected_outputs=outputs,
        runtime_constraints=VaspRuntimeConstraints(),
        execution_settings=ExecutionSettings(
            ncore=2,
            kpar=2,
            nodes=1,
            cores=4,
            memory_mb=16000,
            walltime_seconds=3600,
            partition="compute",
            mpi_ranks=4,
            omp_threads=1,
            executable="vasp_std",
        ),
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


def _seed_vasp_outputs(
    *,
    transport: _AcceptanceTransport,
    target: ExecutionTargetProfile,
    remote_directory: str,
) -> None:
    bodies = {
        "OSZICAR": (
            b" DAV:   1    -1.0\n"
            b" DAV:   6    -1.1\n"
            b"   7 F= -.123 E0= -.120 d E =-.001\n"
        ),
        "OUTCAR": (
            b"reached required accuracy - stopping structural energy minimisation\n"
            b"General timing and accounting informations for this job:\n"
        ),
        "WAVECAR": b"large-binary-placeholder",
    }
    for name, body in bodies.items():
        relative = TargetRelativePath(f"{remote_directory}/{name}")
        transport.files[remote_absolute_path(target, relative)] = body


def test_v04_remote_execution_acceptance_runs_blocks_1_through_9_handoff(
    tmp_path: Path,
) -> None:
    project = Project(name="v0.4 acceptance", slug="v04-acceptance")
    calculation = _calculation(project)
    potcar_body = b"licensed-pb-potcar\n"
    plan = _plan(tmp_path, calculation, potcar_body)
    attempt = create_execution_attempt(plan=plan, calculation=calculation)
    target = _target()
    transport = _AcceptanceTransport()
    transport.files["/opt/vasp/potpaw_PBE_54/Pb_d/POTCAR"] = potcar_body

    staged = stage_remote_runtime(
        project_root=tmp_path,
        plan=plan,
        calculation=calculation,
        attempt=attempt,
        target=target,
        transport=transport,
        potcars=RemotePotcarLibrary(
            resolver_id="pbe54-remote",
            family="PBE_54",
            root="/opt/vasp/potpaw_PBE_54",
        ),
    )
    scheduler = SlurmAdapter(transport)
    submitted = submit_remote_slurm(
        staged=staged,
        transport=transport,
        scheduler=scheduler,
        submitted_at=datetime(2026, 9, 3, 8, 0, tzinfo=UTC),
    )

    _seed_vasp_outputs(
        transport=transport,
        target=target,
        remote_directory=submitted.remote_job.remote_directory,
    )
    transport.squeue_stdout = ""
    transport.sacct_stdout = "12345|COMPLETED|\n"
    monitored = monitor_remote_slurm(
        project_root=tmp_path,
        attempt=submitted.attempt,
        remote_job=submitted.remote_job,
        target=target,
        transport=transport,
        scheduler=scheduler,
        observed_at=datetime(2026, 9, 3, 8, 30, tzinfo=UTC),
    )
    retrieved = retrieve_remote_outputs(
        project_root=tmp_path,
        plan=plan,
        attempt=monitored.attempt,
        remote_job=monitored.remote_job,
        target=target,
        transport=transport,
        retrieved_at=datetime(2026, 9, 3, 8, 31, tzinfo=UTC),
    )

    dag = SchedulerDag(
        nodes=(SchedulerDagNode("pb3-cooh", calculation=calculation, plan=plan),)
    )
    batch = reconcile_batch_dispatch(
        dag=dag,
        concurrency=BatchConcurrencyPolicy(max_active=1),
        attempts=(retrieved.attempt,),
        remote_jobs=(retrieved.remote_job,),
    )
    execution_artifacts = (
        *submitted.artifacts,
        monitored.artifact,
    )
    report = validate_v04_execution_handoff(
        calculation=calculation,
        plan=plan,
        attempt=retrieved.attempt,
        target=target,
        execution_artifacts=execution_artifacts,
        remote_job=retrieved.remote_job,
        retrieval=retrieved,
        batch_snapshot=batch,
        batch_node_id="pb3-cooh",
    )

    assert report.stage is ExecutionHandoffStage.RETRIEVAL
    assert report.batch_state is BatchNodeState.COMPLETE
    assert report.scheduler_state is SchedulerState.COMPLETED
    assert report.retrieval_hash == retrieved.manifest.retrieval_hash
    assert report.scientific_convergence_assessed is False
    assert calculation.status is CalculationScientificStatus.READY
    assert ArtifactType.RETRIEVAL_MANIFEST in report.artifact_types
    assert ArtifactType.OUTCAR in report.artifact_types
    assert ArtifactType.WAVECAR in report.artifact_types
    assert len(report.acceptance_hash) == 64
    assert report.acceptance_hash == validate_v04_execution_handoff(
        calculation=calculation,
        plan=plan,
        attempt=retrieved.attempt,
        target=target,
        execution_artifacts=execution_artifacts,
        remote_job=retrieved.remote_job,
        retrieval=retrieved,
        batch_snapshot=batch,
        batch_node_id="pb3-cooh",
    ).acceptance_hash

    assert (tmp_path / "inputs" / "INCAR").read_text() == "ENCUT = 450\nEDIFF = 1E-5\n"
    artifact_directory = tmp_path / "artifacts" / "execution" / str(attempt.id)
    assert not (artifact_directory / "POTCAR").exists()
    wavecar_remote = remote_absolute_path(
        target,
        TargetRelativePath(f"{retrieved.remote_job.remote_directory}/WAVECAR"),
    )
    wavecar_artifact = next(
        item for item in retrieved.output_artifacts if item.artifact_type is ArtifactType.WAVECAR
    )
    assert wavecar_remote in transport.files
    assert wavecar_artifact.availability is ArtifactAvailability.REMOTE


def test_local_final_handoff_remains_scheduler_free() -> None:
    project = Project(name="local", slug="local")
    calculation = _calculation(project)
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
        expected_outputs=(),
        runtime_constraints=VaspRuntimeConstraints(),
        execution_settings=ExecutionSettings(executable="vasp_std"),
    )
    attempt = replace(
        create_execution_attempt(plan=plan, calculation=calculation),
        status=ExecutionAttemptStatus.EXITED,
    )
    target = ExecutionTargetProfile(
        target_id="local-workstation",
        transport=TransportKind.LOCAL,
        potcar_resolver_id="pbe54-local",
        vasp_executable="vasp_std",
    )
    stdout = Artifact(
        artifact_type=ArtifactType.STDOUT,
        producer=ExecutionAttemptProducerRef(attempt.id),
        availability=ArtifactAvailability.LOCAL,
        retrieval_policy=RetrievalPolicy.ALWAYS,
        local_path=f"artifacts/execution/{attempt.id}/stdout.log",
    )

    report = validate_v04_execution_handoff(
        calculation=calculation,
        plan=plan,
        attempt=attempt,
        target=target,
        execution_artifacts=(stdout,),
    )

    assert report.stage is ExecutionHandoffStage.EXITED
    assert report.remote_job_id is None
    assert report.scheduler_state is None
    assert report.scientific_convergence_assessed is False


def test_acceptance_rejects_scheduler_completion_without_reconciled_attempt_state(
    tmp_path: Path,
) -> None:
    project = Project(name="bad", slug="bad")
    calculation = _calculation(project)
    plan = _plan(tmp_path, calculation, b"potcar")
    attempt = replace(
        create_execution_attempt(plan=plan, calculation=calculation),
        status=ExecutionAttemptStatus.RUNNING,
    )
    target = _target()
    remote_job = RemoteJob(
        execution_attempt_id=attempt.id,
        scheduler=SchedulerType.SLURM,
        scheduler_job_id="12345",
        remote_directory=f"execution/{attempt.id}",
        state=SchedulerState.COMPLETED,
    )

    with pytest.raises(ExecutionAcceptanceError, match="scheduler state"):
        validate_v04_execution_handoff(
            calculation=calculation,
            plan=plan,
            attempt=attempt,
            target=target,
            remote_job=remote_job,
        )


def test_acceptance_rejects_submitted_remote_attempt_without_scheduler_provenance(
    tmp_path: Path,
) -> None:
    project = Project(name="missing", slug="missing")
    calculation = _calculation(project)
    plan = _plan(tmp_path, calculation, b"potcar")
    attempt = replace(
        create_execution_attempt(plan=plan, calculation=calculation),
        status=ExecutionAttemptStatus.QUEUED,
    )
    target = _target()
    producer = ExecutionAttemptProducerRef(attempt.id)
    staged_only = tuple(
        Artifact(
            artifact_type=artifact_type,
            producer=producer,
            availability=ArtifactAvailability.LOCAL,
            retrieval_policy=RetrievalPolicy.ALWAYS,
            local_path=f"artifacts/execution/{attempt.id}/{artifact_type.value}",
        )
        for artifact_type in (
            ArtifactType.EXECUTION_PLAN,
            ArtifactType.INCAR,
            ArtifactType.REMOTE_STAGE_MANIFEST,
        )
    )

    with pytest.raises(ExecutionAcceptanceError, match="scheduler provenance"):
        validate_v04_execution_handoff(
            calculation=calculation,
            plan=plan,
            attempt=attempt,
            target=target,
            execution_artifacts=staged_only,
        )

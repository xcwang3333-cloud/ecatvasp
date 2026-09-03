from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ecatvasp.domain import (
    Artifact,
    ArtifactAvailability,
    ArtifactType,
    Calculation,
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
    CommandResult,
    CommandSpec,
    ExecutionHandoffError,
    ExecutionHandoffSource,
    ExecutionTargetProfile,
    LocalExecutionResult,
    LocalOutputPackage,
    LocalRuntimePackage,
    RetrievalFileRecord,
    RetrievalManifest,
    RuntimeInputManifest,
    SshSecurityPolicy,
    TransportKind,
    build_local_execution_handoff,
    build_remote_execution_handoff,
    collect_local_outputs,
    create_execution_attempt,
)
from ecatvasp.execution.retrieval import RemoteRetrievalPackage
from ecatvasp.vasp.contracts import VaspSystemContext, VaspSystemKind
from ecatvasp.vasp.execution_plan import (
    ExecutionPlan,
    ExpectedOutput,
    PotcarResolutionEntry,
    PotcarResolutionRequest,
    VaspRuntimeConstraints,
)


def _calculation() -> Calculation:
    project = Project(name="Handoff", slug="handoff")
    return Calculation(
        project_id=project.id,
        calculation_type=CalculationType.RELAX,
        input_structure_snapshot_id=new_structure_snapshot_id(),
        recipe_id="ECatVASP.VASP.AdsorbateRelax",
        method_fingerprint_id=new_method_fingerprint_id(),
    )


def _plan(calculation: Calculation) -> ExecutionPlan:
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
        expected_outputs=(
            ExpectedOutput(
                role="outcar",
                artifact_type=ArtifactType.OUTCAR,
                relative_path="OUTCAR",
                retrieval_policy=RetrievalPolicy.ALWAYS,
                required=True,
            ),
        ),
        runtime_constraints=VaspRuntimeConstraints(),
        execution_settings=ExecutionSettings(),
    )


def _local_runtime(
    *,
    tmp_path: Path,
    calculation: Calculation,
    plan: ExecutionPlan,
):
    project_root = tmp_path / "project"
    project_root.mkdir()
    run_directory = tmp_path / "scratch" / "run"
    run_directory.mkdir(parents=True)
    attempt = create_execution_attempt(plan=plan, calculation=calculation)
    staging = replace(attempt, status=ExecutionAttemptStatus.STAGING)
    target = ExecutionTargetProfile(
        target_id="local-acceptance",
        transport=TransportKind.LOCAL,
        potcar_resolver_id="local-pbe54",
        vasp_executable="fake_vasp",
    )
    manifest = RuntimeInputManifest(
        attempt_id=attempt.id,
        plan_hash=plan.plan_hash,
        execution_settings_hash=plan.execution_settings_hash,
        environment=target.sanitized_environment(),
        files=(),
    )
    runtime = LocalRuntimePackage(
        project_root=project_root,
        run_directory=run_directory,
        artifact_directory_relative=f"artifacts/execution/{attempt.id}",
        plan=plan,
        target=target,
        attempt=staging,
        manifest=manifest,
        artifacts=(),
    )
    return project_root, attempt, runtime


def test_local_output_collection_and_handoff_are_integrity_backed(tmp_path: Path) -> None:
    calculation = _calculation()
    plan = _plan(calculation)
    project_root, attempt, runtime = _local_runtime(
        tmp_path=tmp_path,
        calculation=calculation,
        plan=plan,
    )
    (runtime.run_directory / "OUTCAR").write_text("process output\n")
    exited = replace(attempt, status=ExecutionAttemptStatus.EXITED)
    result = LocalExecutionResult(
        attempt=exited,
        command=CommandSpec(argv=("fake_vasp",)),
        launched=True,
        command_result=CommandResult(exit_code=7, stdout="", stderr=""),
        artifacts=(),
    )

    outputs = collect_local_outputs(
        project_root=project_root,
        calculation=calculation,
        plan=plan,
        runtime=runtime,
        result=result,
    )
    handoff = build_local_execution_handoff(
        calculation=calculation,
        plan=plan,
        runtime=runtime,
        result=result,
        outputs=outputs,
    )

    assert handoff.source is ExecutionHandoffSource.LOCAL
    assert handoff.process_exit_code == 7
    assert handoff.scheduler_state is None
    assert len(handoff.handoff_hash) == 64
    assert handoff.locally_available_output_artifact_ids == (outputs.output_artifacts[0].id,)
    artifact = outputs.output_artifacts[0]
    assert artifact.availability is ArtifactAvailability.LOCAL
    assert artifact.sha256 is not None
    assert artifact.local_path is not None
    assert (project_root / artifact.local_path).read_text() == "process output\n"


def test_local_collection_requires_required_output_after_exit(tmp_path: Path) -> None:
    calculation = _calculation()
    plan = _plan(calculation)
    project_root, attempt, runtime = _local_runtime(
        tmp_path=tmp_path,
        calculation=calculation,
        plan=plan,
    )
    result = LocalExecutionResult(
        attempt=replace(attempt, status=ExecutionAttemptStatus.EXITED),
        command=CommandSpec(argv=("fake_vasp",)),
        launched=True,
        command_result=CommandResult(exit_code=0),
        artifacts=(),
    )

    with pytest.raises(ExecutionHandoffError, match="required local VASP output"):
        collect_local_outputs(
            project_root=project_root,
            calculation=calculation,
            plan=plan,
            runtime=runtime,
            result=result,
        )


def _remote_retrieval(
    *,
    calculation: Calculation,
    plan: ExecutionPlan,
    missing: bool = False,
) -> RemoteRetrievalPackage:
    attempt = create_execution_attempt(plan=plan, calculation=calculation)
    retrieving = replace(attempt, status=ExecutionAttemptStatus.RETRIEVING)
    target = ExecutionTargetProfile(
        target_id="cluster-a",
        transport=TransportKind.SSH,
        potcar_resolver_id="remote-pbe54",
        scheduler=SchedulerType.SLURM,
        host_alias="cluster-a",
        remote_work_root="/work/ecatvasp",
        vasp_executable="vasp_std",
        ssh_security=SshSecurityPolicy(),
    )
    remote_job = RemoteJob(
        execution_attempt_id=attempt.id,
        scheduler=SchedulerType.SLURM,
        scheduler_job_id="12345",
        remote_directory=f"execution/{attempt.id}",
        state=SchedulerState.COMPLETED,
    )
    availability = ArtifactAvailability.MISSING if missing else ArtifactAvailability.BOTH
    output = Artifact(
        artifact_type=ArtifactType.OUTCAR,
        producer=ExecutionAttemptProducerRef(attempt.id),
        availability=availability,
        retrieval_policy=RetrievalPolicy.ALWAYS,
        local_path=None if missing else f"artifacts/execution/{attempt.id}/outputs/OUTCAR",
        remote_path=None if missing else f"execution/{attempt.id}/OUTCAR",
        size_bytes=None if missing else 12,
        sha256=None if missing else "f" * 64,
    )
    file_record = RetrievalFileRecord(
        role="outcar",
        artifact_type=ArtifactType.OUTCAR,
        relative_path="OUTCAR",
        retrieval_policy=RetrievalPolicy.ALWAYS,
        required=True,
        remote_present=not missing,
        remote_sha256=None if missing else "f" * 64,
        remote_size_bytes=None if missing else 12,
        local_retrieved=not missing,
        local_relative_path=(
            None if missing else f"artifacts/execution/{attempt.id}/outputs/OUTCAR"
        ),
        remote_retained=not missing,
        final_availability=availability,
    )
    manifest = RetrievalManifest(
        attempt_id=attempt.id,
        remote_job_id=remote_job.id,
        plan_hash=plan.plan_hash,
        target=target.sanitized_environment(),
        remote_directory=remote_job.remote_directory,
        requested_roles=(),
        release_remote_roles=(),
        discard_remote_roles=(),
        retrieved_at=datetime.now(UTC),
        files=(file_record,),
    )
    manifest_artifact = Artifact(
        artifact_type=ArtifactType.RETRIEVAL_MANIFEST,
        producer=ExecutionAttemptProducerRef(attempt.id),
        availability=ArtifactAvailability.LOCAL,
        retrieval_policy=RetrievalPolicy.ALWAYS,
        local_path=f"artifacts/execution/{attempt.id}/retrieval-manifest.json",
        size_bytes=len(manifest.text.encode()),
        sha256=manifest.sha256,
    )
    return RemoteRetrievalPackage(
        attempt=retrieving,
        remote_job=remote_job,
        manifest=manifest,
        output_artifacts=(output,),
        manifest_artifact=manifest_artifact,
    )


def test_remote_retrieval_builds_same_final_handoff_contract() -> None:
    calculation = _calculation()
    plan = _plan(calculation)
    retrieval = _remote_retrieval(calculation=calculation, plan=plan)

    handoff = build_remote_execution_handoff(
        calculation=calculation,
        plan=plan,
        retrieval=retrieval,
    )

    assert handoff.source is ExecutionHandoffSource.REMOTE
    assert handoff.scheduler_state is SchedulerState.COMPLETED
    assert handoff.remote_job_id == retrieval.remote_job.id
    assert handoff.retrieval_manifest_artifact_id == retrieval.manifest_artifact.id
    assert handoff.process_exit_code is None
    assert handoff.locally_available_output_artifact_ids == (
        retrieval.output_artifacts[0].id,
    )


def test_remote_handoff_fails_closed_on_missing_required_output() -> None:
    calculation = _calculation()
    plan = _plan(calculation)
    retrieval = _remote_retrieval(calculation=calculation, plan=plan, missing=True)

    with pytest.raises(ExecutionHandoffError, match="required execution output"):
        build_remote_execution_handoff(
            calculation=calculation,
            plan=plan,
            retrieval=retrieval,
        )


def test_local_output_package_rejects_foreign_producer(tmp_path: Path) -> None:
    calculation = _calculation()
    plan = _plan(calculation)
    project_root, attempt, _ = _local_runtime(
        tmp_path=tmp_path,
        calculation=calculation,
        plan=plan,
    )
    foreign = create_execution_attempt(plan=plan, calculation=calculation, existing_attempts=(attempt,))
    artifact = Artifact(
        artifact_type=ArtifactType.OUTCAR,
        producer=ExecutionAttemptProducerRef(foreign.id),
        availability=ArtifactAvailability.LOCAL,
        local_path="foreign/OUTCAR",
        size_bytes=1,
        sha256="0" * 64,
    )

    with pytest.raises(ValueError, match="exact attempt"):
        LocalOutputPackage(
            project_root=project_root,
            attempt=attempt,
            output_artifacts=(artifact,),
        )

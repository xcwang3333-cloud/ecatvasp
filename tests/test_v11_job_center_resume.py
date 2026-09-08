from __future__ import annotations

import hashlib
from pathlib import Path

from ecatvasp.api.job_center import (
    JobCenterExecutionPreparation,
    ProjectJobCenterApplicationService,
)
from ecatvasp.domain import (
    Artifact,
    ArtifactAvailability,
    ArtifactType,
    Calculation,
    CalculationType,
    ExecutionAttempt,
    ExecutionAttemptProducerRef,
    ExecutionAttemptStatus,
    ExecutionSettings,
    KPointPolicy,
    KPointPolicyKind,
    Lattice,
    MethodDefinition,
    MethodFingerprint,
    PotcarIdentity,
    Project,
    ProtocolDefinition,
    RecipeIdentity,
    SchedulerState,
    SchedulerType,
    ScientificWorkflowPlan,
    StructureSite,
    StructureSnapshot,
    WorkflowRecipeIdentity,
    WorkflowStepBinding,
    WorkflowStepSpec,
    canonical_json,
    new_artifact_id,
    new_atom_uid,
)
from ecatvasp.execution.adapters import CommandResult, CommandSpec, TargetRelativePath
from ecatvasp.execution.remote import RemoteStageManifest
from ecatvasp.execution.ssh import remote_absolute_path
from ecatvasp.execution.targets import (
    ExecutionTargetProfile,
    SshSecurityPolicy,
    TransportKind,
)
from ecatvasp.storage import ProjectBundle, ProjectStore
from ecatvasp.vasp.contracts import VaspSystemContext, VaspSystemKind
from ecatvasp.vasp.execution_plan import (
    ExecutionPlan,
    PotcarResolutionEntry,
    PotcarResolutionRequest,
    VaspRuntimeConstraints,
)


def _sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


class _ResumeTransport:
    transport_kind = TransportKind.SSH

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.upload_calls = 0
        self.sbatch_calls = 0

    def ensure_directory(
        self,
        *,
        target: ExecutionTargetProfile,
        path: TargetRelativePath,
    ) -> None:
        del target, path

    def upload(
        self,
        *,
        target: ExecutionTargetProfile,
        local_path: Path,
        destination: TargetRelativePath,
    ) -> None:
        self.upload_calls += 1
        self.files[remote_absolute_path(target, destination)] = local_path.read_bytes()

    def download(
        self,
        *,
        target: ExecutionTargetProfile,
        source: TargetRelativePath,
        local_path: Path,
    ) -> None:
        del target, source, local_path
        raise AssertionError("resume submission must not download files")

    def run(
        self,
        *,
        target: ExecutionTargetProfile,
        command: CommandSpec,
    ) -> CommandResult:
        del target
        action = command.argv[0]
        if action == "sha256sum":
            body = self.files[command.argv[-1]]
            return CommandResult(
                0,
                stdout=f"{_sha(body)}  {command.argv[-1]}\n",
            )
        if action == "stat":
            body = self.files[command.argv[-1]]
            return CommandResult(0, stdout=f"{len(body)}\n")
        if action == "sbatch":
            self.sbatch_calls += 1
            return CommandResult(0, stdout="24680\n")
        raise AssertionError(f"unexpected resumed submission command: {command.argv!r}")


def _target() -> ExecutionTargetProfile:
    return ExecutionTargetProfile(
        target_id="cluster-a",
        transport=TransportKind.SSH,
        scheduler=SchedulerType.SLURM,
        host_alias="cluster-a",
        remote_work_root="/scratch/ecatvasp",
        potcar_resolver_id="pbe54-remote",
        vasp_executable="vasp_std",
        launcher="srun",
        ssh_security=SshSecurityPolicy(),
    )


def _case(tmp_path: Path) -> tuple[
    ProjectStore,
    JobCenterExecutionPreparation,
    ExecutionAttempt,
    ExecutionTargetProfile,
]:
    project = Project(name="Job resume", slug="job-resume")
    snapshot = StructureSnapshot(
        lattice=Lattice(
            vectors=((8.0, 0.0, 0.0), (0.0, 8.0, 0.0), (0.0, 0.0, 15.0))
        ),
        sites=(StructureSite(new_atom_uid(), "C", (0.5, 0.5, 0.5)),),
    )
    recipe_id = "ECatVASP.VASP.SlabRelax"
    fingerprint = MethodFingerprint(
        method=MethodDefinition(
            xc_functional="PBE",
            potcar_family="PBE_54",
            potcars=(PotcarIdentity("C", "C", "1" * 64),),
        ),
        protocol=ProtocolDefinition(
            encut_ev=450.0,
            kpoints=KPointPolicy(KPointPolicyKind.GAMMA_ONLY),
            ediffg_ev_per_angstrom=-0.02,
        ),
        recipe=RecipeIdentity(recipe_id),
    )
    calculation = Calculation(
        project_id=project.id,
        calculation_type=CalculationType.RELAX,
        input_structure_snapshot_id=snapshot.id,
        recipe_id=recipe_id,
        method_fingerprint_id=fingerprint.id,
    )
    workflow = ScientificWorkflowPlan(
        project_id=project.id,
        workflow_recipe=WorkflowRecipeIdentity("ECatVASP.Workflow.Relax"),
        root_structure_snapshot_id=snapshot.id,
        steps=(WorkflowStepSpec("relax", CalculationType.RELAX, recipe_id),),
    )
    binding = WorkflowStepBinding(
        workflow_plan_id=workflow.id,
        step_key="relax",
        generation=1,
        calculation_id=calculation.id,
        resolved_input_structure_snapshot_id=snapshot.id,
        materialization_reason="root_input",
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
            entries=(PotcarResolutionEntry("C", "C", "1" * 64),),
        ),
        expected_outputs=(),
        runtime_constraints=VaspRuntimeConstraints(),
        execution_settings=ExecutionSettings(
            nodes=1,
            cores=1,
            mpi_ranks=1,
            walltime_seconds=3600,
        ),
    )
    attempt = ExecutionAttempt(
        calculation_id=calculation.id,
        attempt_number=1,
        status=ExecutionAttemptStatus.STAGING,
        input_manifest_hash=plan.input_manifest_sha256,
        execution_plan_hash=plan.plan_hash,
    )
    target = _target()
    stage = RemoteStageManifest(
        attempt_id=attempt.id,
        plan_hash=plan.plan_hash,
        execution_settings_hash=plan.execution_settings_hash,
        environment=target.sanitized_environment(),
        remote_directory=f"execution/{attempt.id}",
        files=(),
    )

    artifact_dir = tmp_path / "artifacts" / "execution" / str(attempt.id)
    artifact_dir.mkdir(parents=True)
    plan_text = canonical_json(
        {"schema_version": 1, "plan_hash": plan.plan_hash, "plan": plan}
    ) + "\n"
    bodies = {
        ArtifactType.EXECUTION_PLAN: plan_text.encode(),
        ArtifactType.INCAR: b"ENCUT = 450\n",
        ArtifactType.REMOTE_STAGE_MANIFEST: stage.text.encode(),
    }
    filenames = {
        ArtifactType.EXECUTION_PLAN: "execution-plan.json",
        ArtifactType.INCAR: "INCAR.runtime",
        ArtifactType.REMOTE_STAGE_MANIFEST: "remote-stage-manifest.json",
    }
    artifacts: list[Artifact] = []
    for artifact_type, body in bodies.items():
        path = artifact_dir / filenames[artifact_type]
        path.write_bytes(body)
        artifacts.append(
            Artifact(
                artifact_type=artifact_type,
                producer=ExecutionAttemptProducerRef(attempt.id),
                availability=ArtifactAvailability.LOCAL,
                local_path=path.relative_to(tmp_path).as_posix(),
                size_bytes=len(body),
                sha256=_sha(body),
            )
        )

    store = ProjectStore(tmp_path)
    store.save(
        ProjectBundle(
            project=project,
            structure_snapshots=(snapshot,),
            method_fingerprints=(fingerprint,),
            workflow_plans=(workflow,),
            calculations=(calculation,),
            workflow_step_bindings=(binding,),
            execution_attempts=(attempt,),
            artifacts=tuple(artifacts),
        )
    )
    preparation = JobCenterExecutionPreparation(
        calculation_id=calculation.id,
        workflow_plan_id=workflow.id,
        step_key="relax",
        plan=plan,
        reused_input_artifacts=True,
    )
    return store, preparation, attempt, target


def test_staging_resume_submits_once_and_replays_durable_receipt(tmp_path: Path) -> None:
    store, preparation, attempt, target = _case(tmp_path)
    transport = _ResumeTransport()
    service = ProjectJobCenterApplicationService(store)

    first = service.submit_remote_slurm(
        preparation=preparation,
        target=target,
        remote_potcars=object(),  # type: ignore[arg-type]
        transport=transport,
    )
    reopened = store.open()

    assert first.attempt_id == attempt.id
    assert first.attempt_status is ExecutionAttemptStatus.QUEUED
    assert first.scheduler_state is SchedulerState.PENDING
    assert first.scheduler_job_id == "24680"
    assert transport.upload_calls == 1
    assert transport.sbatch_calls == 1
    assert len(reopened.execution_attempts) == 1
    assert len(reopened.remote_jobs) == 1

    second = ProjectJobCenterApplicationService(store).submit_remote_slurm(
        preparation=preparation,
        target=target,
        remote_potcars=object(),  # type: ignore[arg-type]
        transport=transport,
    )
    replayed = store.open()

    assert second == first
    assert transport.upload_calls == 1
    assert transport.sbatch_calls == 1
    assert len(replayed.execution_attempts) == 1
    assert len(replayed.remote_jobs) == 1
    assert sum(
        item.artifact_type is ArtifactType.JOB_SCRIPT
        for item in replayed.artifacts
    ) == 1

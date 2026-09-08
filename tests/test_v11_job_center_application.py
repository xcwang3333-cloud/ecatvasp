from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from ecatvasp.api.application import ApplicationServiceError
from ecatvasp.api.execution_plan_resolver import resolve_execution_plan
from ecatvasp.api.job_center import ProjectJobCenterApplicationService
from ecatvasp.domain import (
    Artifact,
    ArtifactAvailability,
    ArtifactType,
    Calculation,
    CalculationScientificStatus,
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
    RemoteJob,
    SchedulerState,
    SchedulerType,
    StructureSite,
    StructureSnapshot,
    canonical_json,
    new_artifact_id,
    new_atom_uid,
)
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


def _snapshot() -> StructureSnapshot:
    return StructureSnapshot(
        lattice=Lattice(
            vectors=((8.0, 0.0, 0.0), (0.0, 8.0, 0.0), (0.0, 0.0, 15.0))
        ),
        sites=(StructureSite(new_atom_uid(), "C", (0.5, 0.5, 0.5)),),
        label="job-center-root",
    )


def _fingerprint() -> MethodFingerprint:
    return MethodFingerprint(
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
        recipe=RecipeIdentity("ECatVASP.VASP.SlabRelax"),
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


def _target(target_id: str, host_alias: str) -> ExecutionTargetProfile:
    return ExecutionTargetProfile(
        target_id=target_id,
        transport=TransportKind.SSH,
        scheduler=SchedulerType.SLURM,
        host_alias=host_alias,
        remote_work_root="/scratch/ecatvasp",
        potcar_resolver_id="pbe54-remote",
        vasp_executable="vasp_std",
        launcher="srun",
        ssh_security=SshSecurityPolicy(),
    )


def _case(tmp_path: Path) -> tuple[
    ProjectStore,
    Calculation,
    ExecutionAttempt,
    RemoteJob,
    ExecutionPlan,
    ExecutionTargetProfile,
]:
    project = Project(name="Job Center", slug="job-center")
    snapshot = _snapshot()
    fingerprint = _fingerprint()
    calculation = Calculation(
        project_id=project.id,
        calculation_type=CalculationType.RELAX,
        input_structure_snapshot_id=snapshot.id,
        recipe_id=fingerprint.recipe.recipe_id,
        method_fingerprint_id=fingerprint.id,
        status=CalculationScientificStatus.DRAFT,
    )
    plan = _plan(calculation)
    attempt = ExecutionAttempt(
        calculation_id=calculation.id,
        attempt_number=1,
        status=ExecutionAttemptStatus.EXITED,
        input_manifest_hash=plan.input_manifest_sha256,
        execution_plan_hash=plan.plan_hash,
    )
    remote_job = RemoteJob(
        execution_attempt_id=attempt.id,
        scheduler=SchedulerType.SLURM,
        scheduler_job_id="12345",
        remote_directory=f"execution/{attempt.id}",
        state=SchedulerState.COMPLETED,
    )

    execution_dir = tmp_path / "artifacts" / "execution" / str(attempt.id)
    execution_dir.mkdir(parents=True)
    plan_text = canonical_json(
        {"schema_version": 1, "plan_hash": plan.plan_hash, "plan": plan}
    ) + "\n"
    plan_path = execution_dir / "execution-plan.json"
    plan_path.write_text(plan_text, encoding="utf-8")
    plan_artifact = Artifact(
        artifact_type=ArtifactType.EXECUTION_PLAN,
        producer=ExecutionAttemptProducerRef(attempt.id),
        availability=ArtifactAvailability.LOCAL,
        local_path=plan_path.relative_to(tmp_path).as_posix(),
        size_bytes=len(plan_text.encode()),
        sha256=_sha(plan_text.encode()),
    )

    target = _target("cluster-a", "cluster-a")
    stage_payload = {"environment": {"target_hash": target.target_hash}}
    stage_text = json.dumps(stage_payload, sort_keys=True) + "\n"
    stage_path = execution_dir / "remote-stage-manifest.json"
    stage_path.write_text(stage_text, encoding="utf-8")
    stage_artifact = Artifact(
        artifact_type=ArtifactType.REMOTE_STAGE_MANIFEST,
        producer=ExecutionAttemptProducerRef(attempt.id),
        availability=ArtifactAvailability.LOCAL,
        local_path=stage_path.relative_to(tmp_path).as_posix(),
        size_bytes=len(stage_text.encode()),
        sha256=_sha(stage_text.encode()),
    )

    store = ProjectStore(tmp_path)
    store.save(
        ProjectBundle(
            project=project,
            structure_snapshots=(snapshot,),
            method_fingerprints=(fingerprint,),
            calculations=(calculation,),
            execution_attempts=(attempt,),
            remote_jobs=(remote_job,),
            artifacts=(plan_artifact, stage_artifact),
        )
    )
    return store, calculation, attempt, remote_job, plan, target


def test_job_catalog_keeps_scientific_attempt_and_scheduler_states_separate(
    tmp_path: Path,
) -> None:
    store, calculation, attempt, remote_job, _, _ = _case(tmp_path)

    payload = ProjectJobCenterApplicationService(store).catalog()
    rows = payload["calculations"]
    assert isinstance(rows, list)
    assert len(rows) == 1
    row = rows[0]
    assert isinstance(row, dict)
    assert row["calculation_id"] == str(calculation.id)
    assert row["scientific_status"] == CalculationScientificStatus.DRAFT.value
    assert row["attempt_status"] == attempt.status.value
    assert row["scheduler_state"] == remote_job.state.value
    assert row["scientific_status"] != row["scheduler_state"]


def test_execution_plan_resolver_round_trips_and_rejects_hash_drift(
    tmp_path: Path,
) -> None:
    store, _, attempt, _, plan, _ = _case(tmp_path)
    bundle = store.open()

    resolved = resolve_execution_plan(store.root, bundle, attempt.id)
    assert resolved == plan
    assert resolved.plan_hash == attempt.execution_plan_hash

    artifact = next(
        item
        for item in bundle.artifacts
        if item.artifact_type is ArtifactType.EXECUTION_PLAN
    )
    assert artifact.local_path is not None
    (store.root / artifact.local_path).write_text("{}\n", encoding="utf-8")
    with pytest.raises(ApplicationServiceError, match="SHA-256 drift"):
        resolve_execution_plan(store.root, bundle, attempt.id)


def test_job_refresh_rejects_target_identity_drift_before_transport_use(
    tmp_path: Path,
) -> None:
    store, _, _, remote_job, _, _ = _case(tmp_path)
    wrong_target = _target("cluster-b", "cluster-b")

    with pytest.raises(ApplicationServiceError, match="target does not match"):
        ProjectJobCenterApplicationService(store).refresh_job(
            remote_job_id=remote_job.id,
            target=wrong_target,
            transport=object(),  # type: ignore[arg-type]
        )

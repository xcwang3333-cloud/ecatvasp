from __future__ import annotations

from dataclasses import replace

import pytest

from ecatvasp.domain import (
    Calculation,
    CalculationType,
    ExecutionAttemptStatus,
    ExecutionSettings,
    Project,
    RemoteJob,
    SchedulerState,
    SchedulerType,
    new_artifact_id,
    new_method_fingerprint_id,
    new_structure_snapshot_id,
)
from ecatvasp.execution import (
    BatchConcurrencyPolicy,
    BatchDispatchError,
    SchedulerDag,
    SchedulerDagNode,
    create_execution_attempt,
    reconcile_batch_dispatch,
)
from ecatvasp.vasp.contracts import VaspSystemContext, VaspSystemKind
from ecatvasp.vasp.execution_plan import (
    ExecutionPlan,
    PotcarResolutionEntry,
    PotcarResolutionRequest,
    VaspRuntimeConstraints,
)


def _calculation(project: Project) -> Calculation:
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
        expected_outputs=(),
        runtime_constraints=VaspRuntimeConstraints(),
        execution_settings=ExecutionSettings(),
    )


def test_resume_rejects_nonterminal_older_attempt() -> None:
    project = Project(name="Batch Safety", slug="batch-safety")
    calculation = _calculation(project)
    plan = _plan(calculation)
    dag = SchedulerDag(
        nodes=(SchedulerDagNode(node_id="a", calculation=calculation, plan=plan),)
    )
    attempt_1 = replace(
        create_execution_attempt(plan=plan, calculation=calculation),
        status=ExecutionAttemptStatus.RUNNING,
    )
    attempt_2 = create_execution_attempt(
        plan=plan,
        calculation=calculation,
        existing_attempts=(attempt_1,),
    )

    with pytest.raises(BatchDispatchError, match="older ExecutionAttempt must be terminal"):
        reconcile_batch_dispatch(
            dag=dag,
            concurrency=BatchConcurrencyPolicy(max_active=2),
            attempts=(attempt_1, attempt_2),
        )


def test_resume_rejects_multiple_active_remote_jobs_for_one_attempt() -> None:
    project = Project(name="Batch Safety", slug="batch-safety")
    calculation = _calculation(project)
    plan = _plan(calculation)
    dag = SchedulerDag(
        nodes=(SchedulerDagNode(node_id="a", calculation=calculation, plan=plan),)
    )
    queued = replace(
        create_execution_attempt(plan=plan, calculation=calculation),
        status=ExecutionAttemptStatus.QUEUED,
    )
    jobs = (
        RemoteJob(
            execution_attempt_id=queued.id,
            scheduler=SchedulerType.SLURM,
            scheduler_job_id="1001",
            remote_directory=f"execution/{queued.id}/submission-1",
            state=SchedulerState.PENDING,
        ),
        RemoteJob(
            execution_attempt_id=queued.id,
            scheduler=SchedulerType.SLURM,
            scheduler_job_id="1002",
            remote_directory=f"execution/{queued.id}/submission-2",
            state=SchedulerState.UNKNOWN,
        ),
    )

    with pytest.raises(BatchDispatchError, match="multiple active/uncertain RemoteJobs"):
        reconcile_batch_dispatch(
            dag=dag,
            concurrency=BatchConcurrencyPolicy(max_active=1),
            attempts=(queued,),
            remote_jobs=jobs,
        )

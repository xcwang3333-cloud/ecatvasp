from __future__ import annotations

import pytest

from ecatvasp.domain import (
    Calculation,
    CalculationType,
    ExecutionAttempt,
    ExecutionSettings,
    Project,
    RemoteJob,
    SchedulerState,
    SchedulerType,
    new_method_fingerprint_id,
    new_structure_snapshot_id,
    validate_remote_job_context,
)
from ecatvasp.domain.ids import new_artifact_id
from ecatvasp.execution import (
    ExecutionProvenanceError,
    create_execution_attempt,
    validate_execution_attempt_plan,
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


def test_create_execution_attempt_pins_plan_and_manifest_hashes() -> None:
    project = Project(name="Execution", slug="execution")
    calculation = _calculation(project)
    plan = _plan(calculation)

    attempt_1 = create_execution_attempt(plan=plan, calculation=calculation)
    attempt_2 = create_execution_attempt(
        plan=plan,
        calculation=calculation,
        existing_attempts=(attempt_1,),
    )

    assert attempt_1.attempt_number == 1
    assert attempt_1.previous_attempt_id is None
    assert attempt_1.execution_plan_hash == plan.plan_hash
    assert attempt_1.input_manifest_hash == plan.input_manifest_sha256
    assert attempt_2.attempt_number == 2
    assert attempt_2.previous_attempt_id == attempt_1.id
    assert attempt_2.execution_plan_hash == plan.plan_hash


def test_execution_bridge_rejects_plan_for_another_calculation() -> None:
    project = Project(name="Execution", slug="execution")
    calculation = _calculation(project)
    other = _calculation(project)
    plan = _plan(calculation)

    with pytest.raises(ExecutionProvenanceError, match="ExecutionPlan"):
        create_execution_attempt(plan=plan, calculation=other)


def test_execution_bridge_rejects_invalid_existing_history() -> None:
    project = Project(name="Execution", slug="execution")
    calculation = _calculation(project)
    other = _calculation(project)
    plan = _plan(calculation)
    foreign_attempt = ExecutionAttempt(calculation_id=other.id, attempt_number=1)

    with pytest.raises(ExecutionProvenanceError, match="all attempts"):
        create_execution_attempt(
            plan=plan,
            calculation=calculation,
            existing_attempts=(foreign_attempt,),
        )


def test_legacy_attempt_is_allowed_but_not_valid_v04_plan_provenance() -> None:
    project = Project(name="Execution", slug="execution")
    calculation = _calculation(project)
    plan = _plan(calculation)
    legacy = ExecutionAttempt(
        calculation_id=calculation.id,
        attempt_number=1,
        input_manifest_hash=plan.input_manifest_sha256,
    )

    assert legacy.execution_plan_hash is None
    with pytest.raises(ExecutionProvenanceError, match="requires execution_plan_hash"):
        validate_execution_attempt_plan(plan=plan, calculation=calculation, attempt=legacy)


def test_execution_attempt_plan_validation_rejects_hash_drift() -> None:
    project = Project(name="Execution", slug="execution")
    calculation = _calculation(project)
    plan = _plan(calculation)
    drifted = ExecutionAttempt(
        calculation_id=calculation.id,
        attempt_number=1,
        execution_plan_hash="f" * 64,
        input_manifest_hash=plan.input_manifest_sha256,
    )

    with pytest.raises(ExecutionProvenanceError, match="execution_plan_hash"):
        validate_execution_attempt_plan(plan=plan, calculation=calculation, attempt=drifted)


def test_one_execution_attempt_may_reference_multiple_remote_jobs() -> None:
    project = Project(name="Execution", slug="execution")
    calculation = _calculation(project)
    attempt = ExecutionAttempt(calculation_id=calculation.id, attempt_number=1)
    jobs = (
        RemoteJob(
            execution_attempt_id=attempt.id,
            scheduler=SchedulerType.SLURM,
            scheduler_job_id="1001",
            remote_directory="/scratch/ecatvasp/attempt-1/submission-1",
        ),
        RemoteJob(
            execution_attempt_id=attempt.id,
            scheduler=SchedulerType.SLURM,
            scheduler_job_id="1002",
            remote_directory="/scratch/ecatvasp/attempt-1/submission-2",
        ),
    )

    for job in jobs:
        validate_remote_job_context(remote_job=job, attempt=attempt)

    assert jobs[0].id != jobs[1].id
    assert {job.execution_attempt_id for job in jobs} == {attempt.id}


def test_scheduler_lost_state_is_distinct_from_unknown() -> None:
    assert SchedulerState.LOST.value == "lost"
    assert SchedulerState.LOST is not SchedulerState.UNKNOWN

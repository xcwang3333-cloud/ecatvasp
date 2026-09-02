"""Unit tests for Calculation, Attempt, Job, Artifact, and Analysis boundaries."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from ecatvasp.domain import (
    Analysis,
    AnalysisStatus,
    AnalysisType,
    Artifact,
    ArtifactAvailability,
    ArtifactType,
    Calculation,
    CalculationScientificStatus,
    CalculationType,
    DomainIntegrityError,
    ExecutionAttempt,
    ExecutionAttemptProducerRef,
    ExecutionAttemptStatus,
    Project,
    RemoteJob,
    RetrievalPolicy,
    SchedulerState,
    SchedulerType,
    new_method_fingerprint_id,
    new_structure_snapshot_id,
    validate_analysis_inputs,
    validate_attempt_history,
    validate_remote_job_context,
)


def _calculation(project: Project, *, status: CalculationScientificStatus) -> Calculation:
    return Calculation(
        project_id=project.id,
        calculation_type=CalculationType.RELAX,
        input_structure_snapshot_id=new_structure_snapshot_id(),
        recipe_id="ECatVASP.VASP.AdsorbateRelax",
        method_fingerprint_id=new_method_fingerprint_id(),
        status=status,
        slug="pb3-cooh-relax",
    )


def test_calculation_attempt_and_scheduler_states_are_independent() -> None:
    project = Project(name="Pb3 CO2RR", slug="pb3-co2rr")
    calculation = _calculation(
        project,
        status=CalculationScientificStatus.COMPLETED_UNCONVERGED,
    )
    attempt = ExecutionAttempt(
        calculation_id=calculation.id,
        attempt_number=1,
        status=ExecutionAttemptStatus.PARSED,
    )
    remote_job = RemoteJob(
        execution_attempt_id=attempt.id,
        scheduler=SchedulerType.SLURM,
        scheduler_job_id="912384",
        remote_directory="/scratch/pb3/cooh/relax/001",
        state=SchedulerState.COMPLETED,
    )

    validate_remote_job_context(remote_job=remote_job, attempt=attempt)

    assert calculation.status is CalculationScientificStatus.COMPLETED_UNCONVERGED
    assert attempt.status is ExecutionAttemptStatus.PARSED
    assert remote_job.state is SchedulerState.COMPLETED


def test_attempt_history_preserves_failed_and_successful_attempts() -> None:
    project = Project(name="Pb3 CO2RR", slug="pb3-co2rr")
    calculation = _calculation(project, status=CalculationScientificStatus.CONVERGED)
    attempt_1 = ExecutionAttempt(
        calculation_id=calculation.id,
        attempt_number=1,
        status=ExecutionAttemptStatus.FAILED,
    )
    attempt_2 = ExecutionAttempt(
        calculation_id=calculation.id,
        attempt_number=2,
        previous_attempt_id=attempt_1.id,
        status=ExecutionAttemptStatus.PARSED,
    )

    validate_attempt_history(calculation=calculation, attempts=(attempt_1, attempt_2))

    with pytest.raises(FrozenInstanceError):
        attempt_1.status = ExecutionAttemptStatus.PARSED  # type: ignore[misc]

    duplicate_number = ExecutionAttempt(
        calculation_id=calculation.id,
        attempt_number=2,
    )
    with pytest.raises(DomainIntegrityError, match="attempt_number values must be unique"):
        validate_attempt_history(
            calculation=calculation,
            attempts=(attempt_1, attempt_2, duplicate_number),
        )


def test_artifact_is_metadata_and_analysis_consumes_artifact_ids() -> None:
    project = Project(name="Pb3 CO2RR", slug="pb3-co2rr")
    calculation = _calculation(project, status=CalculationScientificStatus.CONVERGED)
    attempt = ExecutionAttempt(calculation_id=calculation.id, attempt_number=1)
    chgcar = Artifact(
        artifact_type=ArtifactType.CHGCAR,
        producer=ExecutionAttemptProducerRef(attempt.id),
        availability=ArtifactAvailability.REMOTE,
        retrieval_policy=RetrievalPolicy.ON_DEMAND,
        remote_path="/scratch/pb3/cooh/static/CHGCAR",
        size_bytes=1_000_000,
    )
    bader = Analysis(
        project_id=project.id,
        analysis_type=AnalysisType.BADER,
        input_artifact_ids=(chgcar.id,),
        status=AnalysisStatus.READY,
        tool="Henkelman Bader",
    )

    validate_analysis_inputs(analysis=bader, artifacts=(chgcar,))
    assert bader.input_artifact_ids == (chgcar.id,)


def test_analysis_semantics_are_not_calculation_types() -> None:
    calculation_values = {member.value for member in CalculationType}
    analysis_values = {member.value for member in AnalysisType}

    assert {"bader", "pdos", "charge_difference", "cohp"}.isdisjoint(calculation_values)
    assert {"bader", "pdos", "charge_difference", "cohp"} <= analysis_values


def test_remote_job_time_order_is_validated() -> None:
    project = Project(name="Pb3 CO2RR", slug="pb3-co2rr")
    calculation = _calculation(project, status=CalculationScientificStatus.RUNNING)
    attempt = ExecutionAttempt(calculation_id=calculation.id, attempt_number=1)
    submitted = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)
    started = datetime(2026, 9, 2, 9, 59, tzinfo=UTC)

    with pytest.raises(ValueError, match="started_at must not be earlier than submitted_at"):
        RemoteJob(
            execution_attempt_id=attempt.id,
            scheduler=SchedulerType.SLURM,
            scheduler_job_id="912384",
            remote_directory="/scratch/pb3/cooh/relax/001",
            submitted_at=submitted,
            started_at=started,
        )

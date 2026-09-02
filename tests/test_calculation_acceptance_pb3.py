"""Acceptance test: Pb3-*COOH calculation lifecycle and post-processing boundaries."""

from ecatvasp.domain import (
    Analysis,
    AnalysisType,
    Artifact,
    ArtifactAvailability,
    ArtifactType,
    Calculation,
    CalculationScientificStatus,
    CalculationType,
    ExecutionAttempt,
    ExecutionAttemptProducerRef,
    ExecutionAttemptStatus,
    Project,
    RemoteJob,
    SchedulerState,
    SchedulerType,
    new_method_fingerprint_id,
    new_structure_snapshot_id,
    validate_analysis_inputs,
    validate_attempt_history,
    validate_remote_job_context,
)


def test_pb3_cooh_keeps_calculation_attempt_job_and_bader_separate() -> None:
    project = Project(name="Pb atomic ensemble CO2RR", slug="pb-ensemble-co2rr")
    snapshot_id = new_structure_snapshot_id()
    method_id = new_method_fingerprint_id()

    charge_static = Calculation(
        project_id=project.id,
        calculation_type=CalculationType.CHARGE_STATIC,
        input_structure_snapshot_id=snapshot_id,
        recipe_id="ECatVASP.VASP.BaderStatic",
        method_fingerprint_id=method_id,
        status=CalculationScientificStatus.CONVERGED,
        slug="pb3-cooh-charge-static",
    )
    failed_attempt = ExecutionAttempt(
        calculation_id=charge_static.id,
        attempt_number=1,
        status=ExecutionAttemptStatus.FAILED,
    )
    successful_attempt = ExecutionAttempt(
        calculation_id=charge_static.id,
        attempt_number=2,
        previous_attempt_id=failed_attempt.id,
        status=ExecutionAttemptStatus.PARSED,
    )
    slurm_job = RemoteJob(
        execution_attempt_id=successful_attempt.id,
        scheduler=SchedulerType.SLURM,
        scheduler_job_id="81988",
        remote_directory="/scratch/pb3/cooh/bader/002",
        state=SchedulerState.COMPLETED,
    )

    validate_attempt_history(
        calculation=charge_static,
        attempts=(failed_attempt, successful_attempt),
    )
    validate_remote_job_context(remote_job=slurm_job, attempt=successful_attempt)

    chgcar = Artifact(
        artifact_type=ArtifactType.CHGCAR,
        producer=ExecutionAttemptProducerRef(successful_attempt.id),
        availability=ArtifactAvailability.REMOTE,
        remote_path="/scratch/pb3/cooh/bader/002/CHGCAR",
    )
    aeccar0 = Artifact(
        artifact_type=ArtifactType.AECCAR0,
        producer=ExecutionAttemptProducerRef(successful_attempt.id),
        availability=ArtifactAvailability.REMOTE,
        remote_path="/scratch/pb3/cooh/bader/002/AECCAR0",
    )
    aeccar2 = Artifact(
        artifact_type=ArtifactType.AECCAR2,
        producer=ExecutionAttemptProducerRef(successful_attempt.id),
        availability=ArtifactAvailability.REMOTE,
        remote_path="/scratch/pb3/cooh/bader/002/AECCAR2",
    )
    bader = Analysis(
        project_id=project.id,
        analysis_type=AnalysisType.BADER,
        input_artifact_ids=(chgcar.id, aeccar0.id, aeccar2.id),
        tool="Henkelman Bader",
    )

    validate_analysis_inputs(analysis=bader, artifacts=(chgcar, aeccar0, aeccar2))

    assert charge_static.calculation_type is CalculationType.CHARGE_STATIC
    assert bader.analysis_type is AnalysisType.BADER
    assert successful_attempt.calculation_id == charge_static.id
    assert slurm_job.execution_attempt_id == successful_attempt.id
    assert failed_attempt.id != successful_attempt.id

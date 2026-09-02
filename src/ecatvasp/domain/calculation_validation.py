"""Cross-entity validation for calculation, execution, artifact, and analysis objects."""

from __future__ import annotations

from collections.abc import Iterable

from ecatvasp.domain.calculation import (
    Analysis,
    AnalysisProducerRef,
    Artifact,
    Calculation,
    ExecutionAttempt,
    RemoteJob,
)
from ecatvasp.domain.entities import Project
from ecatvasp.domain.validation import DomainIntegrityError


def validate_calculation_project(*, calculation: Calculation, project: Project) -> None:
    """Require a Calculation to belong to the supplied Project."""

    if calculation.project_id != project.id:
        raise DomainIntegrityError("calculation does not belong to the supplied Project")


def validate_attempt_history(
    *, calculation: Calculation, attempts: Iterable[ExecutionAttempt]
) -> None:
    """Validate immutable attempt history for one Calculation."""

    materialized = tuple(attempts)
    if any(attempt.calculation_id != calculation.id for attempt in materialized):
        raise DomainIntegrityError("all attempts must reference the supplied Calculation")

    ids = tuple(attempt.id for attempt in materialized)
    if len(ids) != len(set(ids)):
        raise DomainIntegrityError("ExecutionAttempt IDs must be unique")

    numbers = tuple(attempt.attempt_number for attempt in materialized)
    if len(numbers) != len(set(numbers)):
        raise DomainIntegrityError("attempt_number values must be unique per Calculation")

    by_id = {attempt.id: attempt for attempt in materialized}
    for attempt in materialized:
        previous_id = attempt.previous_attempt_id
        if previous_id is None:
            continue
        previous = by_id.get(previous_id)
        if previous is None:
            raise DomainIntegrityError("previous_attempt_id must reference supplied history")
        if previous.attempt_number >= attempt.attempt_number:
            raise DomainIntegrityError("previous attempt must have a lower attempt_number")


def validate_remote_job_context(*, remote_job: RemoteJob, attempt: ExecutionAttempt) -> None:
    """Require a RemoteJob to reference the supplied ExecutionAttempt."""

    if remote_job.execution_attempt_id != attempt.id:
        raise DomainIntegrityError("remote job does not reference the supplied ExecutionAttempt")


def validate_analysis_inputs(*, analysis: Analysis, artifacts: Iterable[Artifact]) -> None:
    """Validate that all declared Analysis inputs are supplied and non-self-produced."""

    materialized = tuple(artifacts)
    by_id = {artifact.id: artifact for artifact in materialized}
    if len(by_id) != len(materialized):
        raise DomainIntegrityError("supplied Artifact IDs must be unique")

    missing = [
        artifact_id
        for artifact_id in analysis.input_artifact_ids
        if artifact_id not in by_id
    ]
    if missing:
        raise DomainIntegrityError(
            "analysis input_artifact_ids are missing from supplied artifacts"
        )

    for artifact_id in analysis.input_artifact_ids:
        producer = by_id[artifact_id].producer
        if isinstance(producer, AnalysisProducerRef) and producer.id == analysis.id:
            raise DomainIntegrityError("an Analysis cannot consume an Artifact it produced itself")

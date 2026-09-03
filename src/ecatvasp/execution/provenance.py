"""Bridge immutable v0.3 ExecutionPlan values into v0.4 ExecutionAttempt provenance."""

from __future__ import annotations

from collections.abc import Iterable

from ecatvasp.domain import Calculation, ExecutionAttempt, validate_attempt_history
from ecatvasp.vasp.execution_plan import ExecutionPlan


class ExecutionProvenanceError(ValueError):
    """Raised when an execution attempt cannot be bound to one exact ExecutionPlan."""


def validate_execution_attempt_plan(
    *,
    plan: ExecutionPlan,
    calculation: Calculation,
    attempt: ExecutionAttempt,
) -> None:
    """Require a v0.4 attempt to pin the exact portable plan and source manifest."""

    if plan.calculation_id != calculation.id:
        raise ExecutionProvenanceError("ExecutionPlan does not reference the supplied Calculation")
    if attempt.calculation_id != calculation.id:
        raise ExecutionProvenanceError(
            "ExecutionAttempt does not reference the supplied Calculation"
        )
    if attempt.execution_plan_hash is None:
        raise ExecutionProvenanceError("v0.4 ExecutionAttempt requires execution_plan_hash")
    if attempt.execution_plan_hash != plan.plan_hash:
        raise ExecutionProvenanceError("ExecutionAttempt execution_plan_hash does not match plan")
    if attempt.input_manifest_hash != plan.input_manifest_sha256:
        raise ExecutionProvenanceError("ExecutionAttempt input_manifest_hash does not match plan")


def create_execution_attempt(
    *,
    plan: ExecutionPlan,
    calculation: Calculation,
    existing_attempts: Iterable[ExecutionAttempt] = (),
) -> ExecutionAttempt:
    """Create the next immutable attempt for one exact Calculation and ExecutionPlan.

    Attempt numbering is monotonically increasing but need not be gap-free. Existing legacy
    attempts may omit ``execution_plan_hash``; every attempt created through this v0.4 bridge is
    required to pin both the plan hash and the immutable input-manifest hash.
    """

    if plan.calculation_id != calculation.id:
        raise ExecutionProvenanceError("ExecutionPlan does not reference the supplied Calculation")

    history = tuple(existing_attempts)
    try:
        validate_attempt_history(calculation=calculation, attempts=history)
    except ValueError as error:
        raise ExecutionProvenanceError(str(error)) from error

    if history:
        latest = max(history, key=lambda item: item.attempt_number)
        attempt_number = latest.attempt_number + 1
        previous_attempt_id = latest.id
    else:
        attempt_number = 1
        previous_attempt_id = None

    attempt = ExecutionAttempt(
        calculation_id=calculation.id,
        attempt_number=attempt_number,
        previous_attempt_id=previous_attempt_id,
        input_manifest_hash=plan.input_manifest_sha256,
        execution_plan_hash=plan.plan_hash,
    )
    validate_execution_attempt_plan(plan=plan, calculation=calculation, attempt=attempt)
    return attempt

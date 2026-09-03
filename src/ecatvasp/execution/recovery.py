"""Fail-closed retry, restart, and recovery classification for v0.4 Block 8."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, fields, replace
from enum import StrEnum

from ecatvasp.domain import Calculation, ExecutionAttempt
from ecatvasp.domain.method import ExecutionSettings, canonical_sha256
from ecatvasp.execution.provenance import create_execution_attempt
from ecatvasp.vasp.execution_plan import ExecutionPlan

_EXECUTION_ONLY_INCAR_TAGS = frozenset({"NCORE", "KPAR", "NPAR"})
_INITIALIZATION_RESTART_TAGS = frozenset({"ISTART", "ICHARG"})


class RecoveryClassificationError(ValueError):
    """Raised when recovery intent cannot be classified without guessing."""


class RecoveryCause(StrEnum):
    """High-level reason one execution needs retry or recovery."""

    TRANSPORT_FAILURE = "transport_failure"
    SCHEDULER_FAILURE = "scheduler_failure"
    VASP_FAILURE = "vasp_failure"
    EXECUTION_TUNING = "execution_tuning"
    SCIENTIFIC_INPUT_CHANGE = "scientific_input_change"
    CONTCAR_CONTINUATION = "contcar_continuation"


class ExecutionEvidence(StrEnum):
    """Positive evidence about remote side effects and whether VASP launched."""

    NO_REMOTE_SIDE_EFFECT_CONFIRMED = "no_remote_side_effect_confirmed"
    NO_VASP_LAUNCH_CONFIRMED = "no_vasp_launch_confirmed"
    VASP_LAUNCH_CONFIRMED = "vasp_launch_confirmed"
    EXECUTION_UNCERTAIN = "execution_uncertain"


class RecoveryAction(StrEnum):
    """Identity boundary selected by recovery classification."""

    RETRY_SAME_ATTEMPT = "retry_same_attempt"
    RESUBMIT_SAME_ATTEMPT = "resubmit_same_attempt"
    NEW_EXECUTION_ATTEMPT = "new_execution_attempt"
    NEW_CALCULATION = "new_calculation"
    NEW_STRUCTURE_AND_CALCULATION = "new_structure_and_calculation"
    MANUAL_REVIEW_REQUIRED = "manual_review_required"


class RecoveryChangeLayer(StrEnum):
    """Highest identity layer touched by a proposed recovery change."""

    NONE = "none"
    EXECUTION = "execution"
    SCIENTIFIC_INITIALIZATION = "scientific_initialization"
    SCIENTIFIC_INPUT = "scientific_input"
    STRUCTURE = "structure"


@dataclass(frozen=True, slots=True)
class RecoveryRequest:
    """Explicit evidence and proposed changes used to classify one recovery operation."""

    cause: RecoveryCause
    evidence: ExecutionEvidence
    proposed_execution_settings: ExecutionSettings | None = None
    proposed_incar_tags: tuple[str, ...] = ()
    continue_from_contcar: bool = False
    automatic: bool = False

    def __post_init__(self) -> None:
        normalized = tuple(sorted({tag.strip().upper() for tag in self.proposed_incar_tags}))
        if any(not tag for tag in normalized):
            raise ValueError("proposed_incar_tags must not contain blank names")
        object.__setattr__(self, "proposed_incar_tags", normalized)
        if self.continue_from_contcar and self.cause is not RecoveryCause.CONTCAR_CONTINUATION:
            raise ValueError("continue_from_contcar requires CONTCAR_CONTINUATION cause")
        if self.cause is RecoveryCause.CONTCAR_CONTINUATION and not self.continue_from_contcar:
            raise ValueError("CONTCAR_CONTINUATION requires continue_from_contcar=True")


@dataclass(frozen=True, slots=True)
class RecoveryDecision:
    """Auditable recovery decision without mutating scientific inputs automatically."""

    action: RecoveryAction
    change_layer: RecoveryChangeLayer
    source_plan_hash: str
    source_execution_hash: str
    target_execution_hash: str | None
    changed_execution_fields: tuple[str, ...]
    proposed_incar_tags: tuple[str, ...]
    scientific_identity_preserved: bool
    requires_new_execution_plan: bool
    requires_new_execution_attempt: bool
    requires_new_calculation: bool
    requires_new_structure_snapshot: bool
    reason: str

    @property
    def decision_hash(self) -> str:
        """Return deterministic recovery-decision provenance."""

        return canonical_sha256(self)


def classify_recovery(
    *,
    plan: ExecutionPlan,
    request: RecoveryRequest,
) -> RecoveryDecision:
    """Classify recovery at the strictest identity boundary touched by the request."""

    changed_execution_fields = _changed_execution_fields(
        plan.execution_settings,
        request.proposed_execution_settings,
    )
    proposed_tags = request.proposed_incar_tags
    target_execution_hash = (
        request.proposed_execution_settings.execution_hash
        if request.proposed_execution_settings is not None
        else None
    )

    if request.continue_from_contcar:
        return _decision(
            plan=plan,
            action=RecoveryAction.NEW_STRUCTURE_AND_CALCULATION,
            layer=RecoveryChangeLayer.STRUCTURE,
            target_execution_hash=target_execution_hash,
            changed_execution_fields=changed_execution_fields,
            proposed_tags=proposed_tags,
            reason=(
                "CONTCAR continuation changes the StructureSnapshot input and therefore requires "
                "a new Calculation and MethodFingerprint instance"
            ),
        )

    non_execution_tags = tuple(
        tag for tag in proposed_tags if tag not in _EXECUTION_ONLY_INCAR_TAGS
    )
    if non_execution_tags:
        initialization_only = set(non_execution_tags).issubset(_INITIALIZATION_RESTART_TAGS)
        layer = (
            RecoveryChangeLayer.SCIENTIFIC_INITIALIZATION
            if initialization_only
            else RecoveryChangeLayer.SCIENTIFIC_INPUT
        )
        if request.automatic:
            return _decision(
                plan=plan,
                action=RecoveryAction.MANUAL_REVIEW_REQUIRED,
                layer=layer,
                target_execution_hash=target_execution_hash,
                changed_execution_fields=changed_execution_fields,
                proposed_tags=proposed_tags,
                reason=(
                    "automatic VASP scientific correction is forbidden; proposed INCAR changes "
                    "must be reviewed and represented by a new scientific Calculation identity"
                ),
            )
        return _decision(
            plan=plan,
            action=RecoveryAction.NEW_CALCULATION,
            layer=layer,
            target_execution_hash=target_execution_hash,
            changed_execution_fields=changed_execution_fields,
            proposed_tags=proposed_tags,
            reason=(
                "ISTART/ICHARG and every non-execution INCAR change cross the scientific "
                "identity boundary and require a new Calculation/MethodFingerprint"
            ),
        )

    execution_change = bool(changed_execution_fields or proposed_tags)
    if execution_change:
        if request.automatic:
            return _decision(
                plan=plan,
                action=RecoveryAction.MANUAL_REVIEW_REQUIRED,
                layer=RecoveryChangeLayer.EXECUTION,
                target_execution_hash=target_execution_hash,
                changed_execution_fields=changed_execution_fields,
                proposed_tags=proposed_tags,
                reason=(
                    "Block 8 classifies execution tuning but does not silently auto-tune VASP "
                    "or scheduler settings"
                ),
            )
        return _decision(
            plan=plan,
            action=RecoveryAction.NEW_EXECUTION_ATTEMPT,
            layer=RecoveryChangeLayer.EXECUTION,
            target_execution_hash=target_execution_hash,
            changed_execution_fields=changed_execution_fields,
            proposed_tags=proposed_tags,
            reason=(
                "execution-only changes preserve Calculation/MethodFingerprint identity but "
                "require a new ExecutionPlan and ExecutionAttempt"
            ),
        )

    if request.cause in {
        RecoveryCause.EXECUTION_TUNING,
        RecoveryCause.SCIENTIFIC_INPUT_CHANGE,
    }:
        raise RecoveryClassificationError(
            f"{request.cause.value} requires an explicit proposed change"
        )

    if request.evidence is ExecutionEvidence.NO_REMOTE_SIDE_EFFECT_CONFIRMED:
        return _decision(
            plan=plan,
            action=RecoveryAction.RETRY_SAME_ATTEMPT,
            layer=RecoveryChangeLayer.NONE,
            target_execution_hash=None,
            changed_execution_fields=(),
            proposed_tags=(),
            reason=(
                "the failed transport/control operation is positively known to have produced no "
                "remote side effect, so the same immutable ExecutionAttempt may retry it"
            ),
        )

    if request.evidence is ExecutionEvidence.NO_VASP_LAUNCH_CONFIRMED:
        return _decision(
            plan=plan,
            action=RecoveryAction.RESUBMIT_SAME_ATTEMPT,
            layer=RecoveryChangeLayer.NONE,
            target_execution_hash=None,
            changed_execution_fields=(),
            proposed_tags=(),
            reason=(
                "a scheduler job may be replaced within the same ExecutionAttempt only because "
                "positive evidence establishes that VASP never launched"
            ),
        )

    if request.evidence in {
        ExecutionEvidence.VASP_LAUNCH_CONFIRMED,
        ExecutionEvidence.EXECUTION_UNCERTAIN,
    }:
        return _decision(
            plan=plan,
            action=RecoveryAction.NEW_EXECUTION_ATTEMPT,
            layer=RecoveryChangeLayer.NONE,
            target_execution_hash=None,
            changed_execution_fields=(),
            proposed_tags=(),
            reason=(
                "confirmed or uncertain VASP launch forbids reusing an ExecutionAttempt; the "
                "same Calculation may be retried only as a new attempt"
            ),
        )

    raise AssertionError(f"unhandled recovery evidence: {request.evidence}")


def derive_execution_recovery_plan(
    *,
    plan: ExecutionPlan,
    execution_settings: ExecutionSettings,
) -> ExecutionPlan:
    """Derive a new plan for execution-only tuning while preserving scientific handoff fields."""

    if execution_settings.execution_hash == plan.execution_settings.execution_hash:
        raise RecoveryClassificationError("execution recovery settings do not change the plan")
    recovered = replace(plan, execution_settings=execution_settings)
    if recovered.plan_hash == plan.plan_hash:
        raise RecoveryClassificationError("execution-only recovery must change ExecutionPlan hash")
    return recovered


def create_recovery_execution_attempt(
    *,
    plan: ExecutionPlan,
    calculation: Calculation,
    existing_attempts: Iterable[ExecutionAttempt],
    decision: RecoveryDecision,
) -> ExecutionAttempt:
    """Create the next attempt only for a decision that preserves scientific identity."""

    if decision.action is not RecoveryAction.NEW_EXECUTION_ATTEMPT:
        raise RecoveryClassificationError("recovery decision does not authorize a new attempt")
    if not decision.scientific_identity_preserved or decision.requires_new_calculation:
        raise RecoveryClassificationError("scientific recovery requires a new Calculation instead")
    if decision.source_plan_hash != plan.plan_hash and not decision.requires_new_execution_plan:
        raise RecoveryClassificationError("recovery decision is not bound to this ExecutionPlan")
    if decision.requires_new_execution_plan:
        if decision.target_execution_hash is None:
            raise RecoveryClassificationError("execution recovery decision lacks target hash")
        if decision.target_execution_hash != plan.execution_settings.execution_hash:
            raise RecoveryClassificationError("new ExecutionPlan does not match recovery decision")
    elif decision.source_plan_hash != plan.plan_hash:
        raise RecoveryClassificationError("retry plan does not match recovery decision")
    return create_execution_attempt(
        plan=plan,
        calculation=calculation,
        existing_attempts=existing_attempts,
    )


def _changed_execution_fields(
    current: ExecutionSettings,
    proposed: ExecutionSettings | None,
) -> tuple[str, ...]:
    if proposed is None:
        return ()
    return tuple(
        item.name
        for item in fields(ExecutionSettings)
        if getattr(current, item.name) != getattr(proposed, item.name)
    )


def _decision(
    *,
    plan: ExecutionPlan,
    action: RecoveryAction,
    layer: RecoveryChangeLayer,
    target_execution_hash: str | None,
    changed_execution_fields: tuple[str, ...],
    proposed_tags: tuple[str, ...],
    reason: str,
) -> RecoveryDecision:
    new_plan = layer is RecoveryChangeLayer.EXECUTION
    new_attempt = action is RecoveryAction.NEW_EXECUTION_ATTEMPT
    new_calculation = action in {
        RecoveryAction.NEW_CALCULATION,
        RecoveryAction.NEW_STRUCTURE_AND_CALCULATION,
        RecoveryAction.MANUAL_REVIEW_REQUIRED,
    } and layer in {
        RecoveryChangeLayer.SCIENTIFIC_INITIALIZATION,
        RecoveryChangeLayer.SCIENTIFIC_INPUT,
        RecoveryChangeLayer.STRUCTURE,
    }
    new_structure = action is RecoveryAction.NEW_STRUCTURE_AND_CALCULATION
    scientific_preserved = layer in {RecoveryChangeLayer.NONE, RecoveryChangeLayer.EXECUTION}
    return RecoveryDecision(
        action=action,
        change_layer=layer,
        source_plan_hash=plan.plan_hash,
        source_execution_hash=plan.execution_settings.execution_hash,
        target_execution_hash=target_execution_hash,
        changed_execution_fields=changed_execution_fields,
        proposed_incar_tags=proposed_tags,
        scientific_identity_preserved=scientific_preserved,
        requires_new_execution_plan=new_plan,
        requires_new_execution_attempt=new_attempt,
        requires_new_calculation=new_calculation,
        requires_new_structure_snapshot=new_structure,
        reason=reason,
    )

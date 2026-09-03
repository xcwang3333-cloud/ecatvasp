"""Deterministic batch dispatch and scheduler-DAG orchestration for v0.4 Block 9."""

from __future__ import annotations

import bisect
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TypedDict

from ecatvasp.domain import (
    Calculation,
    CalculationId,
    ExecutionAttempt,
    ExecutionAttemptId,
    ExecutionAttemptStatus,
    RemoteJob,
    SchedulerState,
    canonical_sha256,
    validate_attempt_history,
    validate_remote_job_context,
)
from ecatvasp.execution.provenance import (
    create_execution_attempt,
    validate_execution_attempt_plan,
)
from ecatvasp.execution.recovery import (
    RecoveryAction,
    RecoveryDecision,
    create_recovery_execution_attempt,
)
from ecatvasp.vasp.execution_plan import ExecutionPlan

_SAFE_NODE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_ACTIVE_BATCH_STATES = frozenset(
    {
        "reserved",
        "staging",
        "queued",
        "running",
    }
)
_SCHEDULER_MAY_BE_ACTIVE = frozenset(
    {
        SchedulerState.PENDING,
        SchedulerState.RUNNING,
        SchedulerState.UNKNOWN,
        SchedulerState.LOST,
    }
)
_RECOVERY_TERMINAL_ATTEMPTS = frozenset(
    {
        ExecutionAttemptStatus.EXITED,
        ExecutionAttemptStatus.PARSED,
        ExecutionAttemptStatus.FAILED,
        ExecutionAttemptStatus.CANCELLED,
    }
)


class _ObservationCommon(TypedDict):
    node_id: str
    latest_attempt_id: ExecutionAttemptId
    latest_attempt_number: int
    latest_plan_hash: str | None
    remote_job_count: int


class BatchDispatchError(ValueError):
    """Raised when batch execution cannot be reconciled without guessing."""


class BatchNodeState(StrEnum):
    """Execution-only scheduler-DAG state; it is not scientific Calculation status."""

    WAITING_DEPENDENCIES = "waiting_dependencies"
    READY = "ready"
    RESERVED = "reserved"
    STAGING = "staging"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETE = "complete"
    RECOVERY_REQUIRED = "recovery_required"
    STALE_PLAN = "stale_plan"
    BLOCKED_DEPENDENCY = "blocked_dependency"


class BatchDispatchMode(StrEnum):
    """How one batch ticket may proceed without changing scientific identity silently."""

    NEW_ATTEMPT = "new_attempt"
    RECOVERY_NEW_ATTEMPT = "recovery_new_attempt"
    CONTINUE_CREATED_ATTEMPT = "continue_created_attempt"


@dataclass(frozen=True, slots=True)
class SchedulerDagNode:
    """One already-materialized Calculation/ExecutionPlan handoff in a scheduler-only DAG.

    ``depends_on`` expresses dispatch ordering only. It cannot create scientific data dependencies,
    because every downstream ``ExecutionPlan`` must already exist before this node can be built.
    """

    node_id: str
    calculation: Calculation
    plan: ExecutionPlan
    depends_on: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not _SAFE_NODE_ID.fullmatch(self.node_id):
            raise ValueError("node_id must be one portable scheduler identifier")
        if self.plan.calculation_id != self.calculation.id:
            raise ValueError("scheduler node ExecutionPlan must belong to its Calculation")
        if self.plan.recipe_id != self.calculation.recipe_id:
            raise ValueError("scheduler node ExecutionPlan recipe must match Calculation")
        dependencies = tuple(sorted(self.depends_on))
        if len(dependencies) != len(set(dependencies)):
            raise ValueError("scheduler node dependencies must be unique")
        if self.node_id in dependencies:
            raise ValueError("scheduler node cannot depend on itself")
        for dependency in dependencies:
            if not _SAFE_NODE_ID.fullmatch(dependency):
                raise ValueError("scheduler dependency ids must be portable identifiers")
        object.__setattr__(self, "depends_on", dependencies)


@dataclass(frozen=True, slots=True)
class SchedulerDag:
    """Immutable execution-order DAG over exact portable ExecutionPlans."""

    nodes: tuple[SchedulerDagNode, ...]
    topological_order: tuple[str, ...] = ()
    dag_hash: str = ""

    def __post_init__(self) -> None:
        if not self.nodes:
            raise ValueError("SchedulerDag requires at least one node")
        nodes = tuple(sorted(self.nodes, key=lambda item: item.node_id))
        if len({item.node_id for item in nodes}) != len(nodes):
            raise ValueError("SchedulerDag node ids must be unique")
        if len({item.calculation.id for item in nodes}) != len(nodes):
            raise ValueError("SchedulerDag permits only one node per Calculation")
        by_id = {item.node_id: item for item in nodes}
        for node in nodes:
            missing = tuple(item for item in node.depends_on if item not in by_id)
            if missing:
                raise ValueError(
                    f"scheduler node {node.node_id!r} references missing dependencies: "
                    + ", ".join(missing)
                )
        order = _topological_order(nodes)
        digest = canonical_sha256(
            tuple(
                {
                    "node_id": node.node_id,
                    "calculation_id": node.calculation.id,
                    "plan_hash": node.plan.plan_hash,
                    "depends_on": node.depends_on,
                }
                for node in nodes
            )
        )
        object.__setattr__(self, "nodes", nodes)
        object.__setattr__(self, "topological_order", order)
        object.__setattr__(self, "dag_hash", digest)

    def node(self, node_id: str) -> SchedulerDagNode:
        """Return one node by stable scheduler id."""

        for item in self.nodes:
            if item.node_id == node_id:
                return item
        raise KeyError(node_id)


@dataclass(frozen=True, slots=True)
class BatchConcurrencyPolicy:
    """Maximum number of reserved/staging/queued/running batch nodes."""

    max_active: int

    def __post_init__(self) -> None:
        if self.max_active < 1:
            raise ValueError("max_active must be positive")


@dataclass(frozen=True, slots=True)
class BatchNodeObservation:
    """One resume-safe observation of a scheduler-DAG node."""

    node_id: str
    state: BatchNodeState
    reason: str
    latest_attempt_id: ExecutionAttemptId | None = None
    latest_attempt_number: int | None = None
    latest_plan_hash: str | None = None
    remote_job_count: int = 0
    dispatch_mode: BatchDispatchMode | None = None
    recovery_decision_hash: str | None = None

    def __post_init__(self) -> None:
        if not _SAFE_NODE_ID.fullmatch(self.node_id):
            raise ValueError("BatchNodeObservation node_id is invalid")
        if not self.reason.strip():
            raise ValueError("BatchNodeObservation reason must not be blank")
        if self.latest_attempt_number is not None and self.latest_attempt_number < 1:
            raise ValueError("latest_attempt_number must be positive")
        if self.remote_job_count < 0:
            raise ValueError("remote_job_count must not be negative")
        if self.state is BatchNodeState.READY and self.dispatch_mode not in {
            BatchDispatchMode.NEW_ATTEMPT,
            BatchDispatchMode.RECOVERY_NEW_ATTEMPT,
        }:
            raise ValueError("READY batch nodes require an explicit dispatch mode")
        if self.state is BatchNodeState.RESERVED:
            if self.dispatch_mode is not BatchDispatchMode.CONTINUE_CREATED_ATTEMPT:
                raise ValueError("RESERVED batch nodes must continue their exact CREATED attempt")
        elif self.state is not BatchNodeState.READY and self.dispatch_mode is not None:
            raise ValueError("non-dispatchable batch state cannot carry dispatch_mode")


@dataclass(frozen=True, slots=True)
class BatchDispatchSnapshot:
    """Deterministic scheduler-only batch state reconstructed from persisted execution facts."""

    dag_hash: str
    max_active: int
    observations: tuple[BatchNodeObservation, ...]
    snapshot_hash: str = ""

    def __post_init__(self) -> None:
        if self.max_active < 1:
            raise ValueError("BatchDispatchSnapshot max_active must be positive")
        if len({item.node_id for item in self.observations}) != len(self.observations):
            raise ValueError("BatchDispatchSnapshot observations must have unique node ids")
        object.__setattr__(
            self,
            "snapshot_hash",
            canonical_sha256(
                {
                    "dag_hash": self.dag_hash,
                    "max_active": self.max_active,
                    "observations": self.observations,
                }
            ),
        )

    @property
    def active_count(self) -> int:
        """Count nodes that already consume one batch concurrency slot."""

        return sum(item.state.value in _ACTIVE_BATCH_STATES for item in self.observations)

    @property
    def available_slots(self) -> int:
        """Return capacity for new attempts without cancelling existing work."""

        return max(0, self.max_active - self.active_count)

    @property
    def dispatchable_node_ids(self) -> tuple[str, ...]:
        """Return READY nodes selected deterministically up to available capacity."""

        ready = tuple(
            item.node_id for item in self.observations if item.state is BatchNodeState.READY
        )
        return ready[: self.available_slots]

    @property
    def resumable_created_node_ids(self) -> tuple[str, ...]:
        """Return exact CREATED attempts that may continue without allocating a new attempt."""

        return tuple(
            item.node_id for item in self.observations if item.state is BatchNodeState.RESERVED
        )


@dataclass(frozen=True, slots=True)
class BatchDispatchTicket:
    """Two-phase ticket: persist a new attempt before any staging/submission side effect."""

    node_id: str
    calculation: Calculation
    plan: ExecutionPlan
    attempt: ExecutionAttempt
    mode: BatchDispatchMode
    recovery_decision_hash: str | None = None

    def __post_init__(self) -> None:
        if self.attempt.status is not ExecutionAttemptStatus.CREATED:
            raise ValueError("BatchDispatchTicket requires a CREATED ExecutionAttempt")
        validate_execution_attempt_plan(
            plan=self.plan,
            calculation=self.calculation,
            attempt=self.attempt,
        )
        if self.mode is BatchDispatchMode.RECOVERY_NEW_ATTEMPT:
            if self.recovery_decision_hash is None:
                raise ValueError("recovery dispatch ticket requires decision provenance")
        elif self.recovery_decision_hash is not None:
            raise ValueError("non-recovery dispatch ticket cannot carry recovery decision hash")


@dataclass(frozen=True, slots=True)
class BatchDispatchWave:
    """Resume-safe batch dispatch selection; it does not stage or submit jobs itself."""

    dag_hash: str
    snapshot_hash: str
    tickets: tuple[BatchDispatchTicket, ...]
    wave_hash: str = ""

    def __post_init__(self) -> None:
        if len({item.node_id for item in self.tickets}) != len(self.tickets):
            raise ValueError("BatchDispatchWave tickets must have unique node ids")
        object.__setattr__(
            self,
            "wave_hash",
            canonical_sha256(
                {
                    "dag_hash": self.dag_hash,
                    "snapshot_hash": self.snapshot_hash,
                    "tickets": tuple(
                        {
                            "node_id": item.node_id,
                            "plan_hash": item.plan.plan_hash,
                            "attempt_id": item.attempt.id,
                            "attempt_number": item.attempt.attempt_number,
                            "mode": item.mode,
                            "recovery_decision_hash": item.recovery_decision_hash,
                        }
                        for item in self.tickets
                    ),
                }
            ),
        )

    @property
    def new_attempts(self) -> tuple[ExecutionAttempt, ...]:
        """Return only attempts that must be persisted before execution side effects."""

        return tuple(
            item.attempt
            for item in self.tickets
            if item.mode is not BatchDispatchMode.CONTINUE_CREATED_ATTEMPT
        )


def reconcile_batch_dispatch(
    *,
    dag: SchedulerDag,
    concurrency: BatchConcurrencyPolicy,
    attempts: Iterable[ExecutionAttempt] = (),
    remote_jobs: Iterable[RemoteJob] = (),
    recovery_decisions: Mapping[str, RecoveryDecision] | None = None,
) -> BatchDispatchSnapshot:
    """Reconstruct batch state without issuing scheduler or transport side effects."""

    attempt_values = tuple(attempts)
    job_values = tuple(remote_jobs)
    decisions = dict(recovery_decisions or {})
    node_ids = {node.node_id for node in dag.nodes}
    unknown_decisions = sorted(set(decisions).difference(node_ids))
    if unknown_decisions:
        raise BatchDispatchError(
            "recovery decisions reference unknown batch nodes: " + ", ".join(unknown_decisions)
        )

    histories: dict[CalculationId, tuple[ExecutionAttempt, ...]] = {}
    attempt_by_id: dict[ExecutionAttemptId, ExecutionAttempt] = {}
    for node in dag.nodes:
        history = tuple(
            item for item in attempt_values if item.calculation_id == node.calculation.id
        )
        try:
            validate_attempt_history(calculation=node.calculation, attempts=history)
        except ValueError as error:
            raise BatchDispatchError(str(error)) from error
        histories[node.calculation.id] = history
        for history_attempt in history:
            if history_attempt.id in attempt_by_id:
                raise BatchDispatchError(
                    "ExecutionAttempt ids must be unique in batch resume input"
                )
            attempt_by_id[history_attempt.id] = history_attempt

    jobs_by_attempt: dict[ExecutionAttemptId, list[RemoteJob]] = {
        attempt_id: [] for attempt_id in attempt_by_id
    }
    for job in job_values:
        job_attempt = attempt_by_id.get(job.execution_attempt_id)
        if job_attempt is None:
            continue
        try:
            validate_remote_job_context(remote_job=job, attempt=job_attempt)
        except ValueError as error:
            raise BatchDispatchError(str(error)) from error
        jobs_by_attempt[job_attempt.id].append(job)

    for node in dag.nodes:
        ordered_history = tuple(
            sorted(histories[node.calculation.id], key=lambda item: item.attempt_number)
        )
        for prior_attempt in ordered_history[:-1]:
            if prior_attempt.status not in _RECOVERY_TERMINAL_ATTEMPTS:
                raise BatchDispatchError(
                    "older ExecutionAttempt must be terminal before a newer attempt exists"
                )
            _validate_remote_job_consistency(
                prior_attempt,
                tuple(jobs_by_attempt.get(prior_attempt.id, ())),
            )

    base: dict[str, BatchNodeObservation] = {}
    for node_id in dag.topological_order:
        node = dag.node(node_id)
        history = histories[node.calculation.id]
        latest = max(history, key=lambda item: item.attempt_number) if history else None
        jobs = tuple(jobs_by_attempt.get(latest.id, ())) if latest is not None else ()
        base[node_id] = _base_observation(
            node=node,
            latest=latest,
            remote_jobs=jobs,
            recovery_decision=decisions.get(node_id),
        )

    final: dict[str, BatchNodeObservation] = {}
    blocking_states = {
        BatchNodeState.RECOVERY_REQUIRED,
        BatchNodeState.STALE_PLAN,
        BatchNodeState.BLOCKED_DEPENDENCY,
    }
    for node_id in dag.topological_order:
        node = dag.node(node_id)
        observation = base[node_id]
        if observation.state is not BatchNodeState.READY:
            final[node_id] = observation
            continue
        dependencies = tuple(final[item] for item in node.depends_on)
        if all(item.state is BatchNodeState.COMPLETE for item in dependencies):
            final[node_id] = observation
            continue
        if any(item.state in blocking_states for item in dependencies):
            final[node_id] = replace(
                observation,
                state=BatchNodeState.BLOCKED_DEPENDENCY,
                dispatch_mode=None,
                reason="one or more scheduler-order dependencies require recovery or replacement",
            )
            continue
        final[node_id] = replace(
            observation,
            state=BatchNodeState.WAITING_DEPENDENCIES,
            dispatch_mode=None,
            reason="scheduler-order dependencies have not reached execution completion",
        )

    observations = tuple(final[node_id] for node_id in dag.topological_order)
    return BatchDispatchSnapshot(
        dag_hash=dag.dag_hash,
        max_active=concurrency.max_active,
        observations=observations,
    )


def prepare_batch_dispatch_wave(
    *,
    dag: SchedulerDag,
    concurrency: BatchConcurrencyPolicy,
    attempts: Iterable[ExecutionAttempt] = (),
    remote_jobs: Iterable[RemoteJob] = (),
    recovery_decisions: Mapping[str, RecoveryDecision] | None = None,
) -> BatchDispatchWave:
    """Create resume/new-attempt tickets while leaving all external side effects to Blocks 4-6.

    Every attempt returned by ``new_attempts`` must be persisted before the caller stages files or
    submits a scheduler job. Re-running this function against that persisted state will reconstruct
    the CREATED attempt as ``CONTINUE_CREATED_ATTEMPT`` instead of allocating a duplicate attempt.
    """

    attempt_values = tuple(attempts)
    job_values = tuple(remote_jobs)
    decisions = dict(recovery_decisions or {})
    snapshot = reconcile_batch_dispatch(
        dag=dag,
        concurrency=concurrency,
        attempts=attempt_values,
        remote_jobs=job_values,
        recovery_decisions=decisions,
    )
    attempt_by_id = {item.id: item for item in attempt_values}
    histories = {
        node.calculation.id: tuple(
            item for item in attempt_values if item.calculation_id == node.calculation.id
        )
        for node in dag.nodes
    }
    observations = {item.node_id: item for item in snapshot.observations}
    tickets: list[BatchDispatchTicket] = []

    for node_id in snapshot.resumable_created_node_ids:
        observation = observations[node_id]
        if observation.latest_attempt_id is None:
            raise BatchDispatchError("RESERVED node is missing its persisted ExecutionAttempt")
        attempt = attempt_by_id[observation.latest_attempt_id]
        node = dag.node(node_id)
        tickets.append(
            BatchDispatchTicket(
                node_id=node_id,
                calculation=node.calculation,
                plan=node.plan,
                attempt=attempt,
                mode=BatchDispatchMode.CONTINUE_CREATED_ATTEMPT,
            )
        )

    for node_id in snapshot.dispatchable_node_ids:
        node = dag.node(node_id)
        observation = observations[node_id]
        history = histories[node.calculation.id]
        if observation.dispatch_mode is BatchDispatchMode.NEW_ATTEMPT:
            attempt = create_execution_attempt(
                plan=node.plan,
                calculation=node.calculation,
                existing_attempts=history,
            )
            recovery_hash = None
        elif observation.dispatch_mode is BatchDispatchMode.RECOVERY_NEW_ATTEMPT:
            decision = decisions.get(node_id)
            if decision is None:
                raise BatchDispatchError("recovery-ready node is missing RecoveryDecision")
            attempt = create_recovery_execution_attempt(
                plan=node.plan,
                calculation=node.calculation,
                existing_attempts=history,
                decision=decision,
            )
            recovery_hash = decision.decision_hash
        else:
            raise BatchDispatchError("READY node is missing a valid batch dispatch mode")
        tickets.append(
            BatchDispatchTicket(
                node_id=node_id,
                calculation=node.calculation,
                plan=node.plan,
                attempt=attempt,
                mode=observation.dispatch_mode,
                recovery_decision_hash=recovery_hash,
            )
        )

    return BatchDispatchWave(
        dag_hash=dag.dag_hash,
        snapshot_hash=snapshot.snapshot_hash,
        tickets=tuple(tickets),
    )


def _base_observation(
    *,
    node: SchedulerDagNode,
    latest: ExecutionAttempt | None,
    remote_jobs: tuple[RemoteJob, ...],
    recovery_decision: RecoveryDecision | None,
) -> BatchNodeObservation:
    if latest is None:
        if recovery_decision is not None:
            raise BatchDispatchError(
                f"batch node {node.node_id!r} has a RecoveryDecision but no prior attempt"
            )
        return BatchNodeObservation(
            node_id=node.node_id,
            state=BatchNodeState.READY,
            reason="no prior execution attempt exists for this exact scientific handoff",
            dispatch_mode=BatchDispatchMode.NEW_ATTEMPT,
        )

    _validate_remote_job_consistency(latest, remote_jobs)
    latest_hash = latest.execution_plan_hash
    common: _ObservationCommon = {
        "node_id": node.node_id,
        "latest_attempt_id": latest.id,
        "latest_attempt_number": latest.attempt_number,
        "latest_plan_hash": latest_hash,
        "remote_job_count": len(remote_jobs),
    }

    if recovery_decision is not None:
        _validate_recovery_authorization(
            node=node,
            latest=latest,
            decision=recovery_decision,
        )
        return BatchNodeObservation(
            **common,
            state=BatchNodeState.READY,
            reason="explicit Block 8 RecoveryDecision authorizes one new ExecutionAttempt",
            dispatch_mode=BatchDispatchMode.RECOVERY_NEW_ATTEMPT,
            recovery_decision_hash=recovery_decision.decision_hash,
        )

    if latest_hash is None:
        return BatchNodeObservation(
            **common,
            state=BatchNodeState.STALE_PLAN,
            reason=(
                "legacy attempt lacks v0.4 ExecutionPlan provenance; automatic batch resume "
                "is unsafe"
            ),
        )
    if latest_hash != node.plan.plan_hash:
        return BatchNodeObservation(
            **common,
            state=BatchNodeState.STALE_PLAN,
            reason=(
                "latest attempt pins a different ExecutionPlan; explicit recovery "
                "authorization is required"
            ),
        )

    validate_execution_attempt_plan(
        plan=node.plan,
        calculation=node.calculation,
        attempt=latest,
    )
    if latest.status is ExecutionAttemptStatus.CREATED:
        return BatchNodeObservation(
            **common,
            state=BatchNodeState.RESERVED,
            reason="persisted CREATED attempt reserves this node and must be continued exactly",
            dispatch_mode=BatchDispatchMode.CONTINUE_CREATED_ATTEMPT,
        )
    if latest.status is ExecutionAttemptStatus.STAGING:
        return BatchNodeObservation(
            **common,
            state=BatchNodeState.STAGING,
            reason="staging is already in progress or requires explicit stage reconciliation",
        )
    if latest.status is ExecutionAttemptStatus.QUEUED:
        return BatchNodeObservation(
            **common,
            state=BatchNodeState.QUEUED,
            reason="scheduler submission already exists; batch resume must not submit a duplicate",
        )
    if latest.status is ExecutionAttemptStatus.RUNNING:
        return BatchNodeObservation(
            **common,
            state=BatchNodeState.RUNNING,
            reason="scheduler-backed VASP execution is already running",
        )
    if latest.status in {
        ExecutionAttemptStatus.EXITED,
        ExecutionAttemptStatus.RETRIEVING,
        ExecutionAttemptStatus.PARSED,
    }:
        return BatchNodeObservation(
            **common,
            state=BatchNodeState.COMPLETE,
            reason=(
                "execution-order dependency is complete; this does not assert scientific "
                "convergence"
            ),
        )
    if latest.status in {
        ExecutionAttemptStatus.FAILED,
        ExecutionAttemptStatus.CANCELLED,
    }:
        return BatchNodeObservation(
            **common,
            state=BatchNodeState.RECOVERY_REQUIRED,
            reason="failed/cancelled attempts require an explicit Block 8 recovery decision",
        )
    raise AssertionError(f"unhandled ExecutionAttempt status: {latest.status}")


def _validate_remote_job_consistency(
    attempt: ExecutionAttempt,
    remote_jobs: tuple[RemoteJob, ...],
) -> None:
    if attempt.status in {
        ExecutionAttemptStatus.CREATED,
        ExecutionAttemptStatus.STAGING,
    } and remote_jobs:
        raise BatchDispatchError("CREATED/STAGING attempt cannot already have a RemoteJob")

    active_jobs = tuple(job for job in remote_jobs if job.state in _SCHEDULER_MAY_BE_ACTIVE)
    if len(active_jobs) > 1:
        raise BatchDispatchError(
            "one ExecutionAttempt cannot have multiple active/uncertain RemoteJobs"
        )
    if attempt.status in {
        ExecutionAttemptStatus.QUEUED,
        ExecutionAttemptStatus.RUNNING,
    }:
        if not remote_jobs:
            raise BatchDispatchError("QUEUED/RUNNING batch attempt requires persisted RemoteJob")
        if not active_jobs:
            raise BatchDispatchError(
                "QUEUED/RUNNING attempt conflicts with only terminal scheduler records"
            )
    if attempt.status in {
        ExecutionAttemptStatus.EXITED,
        ExecutionAttemptStatus.RETRIEVING,
        ExecutionAttemptStatus.PARSED,
        ExecutionAttemptStatus.FAILED,
        ExecutionAttemptStatus.CANCELLED,
    } and active_jobs:
        raise BatchDispatchError(
            "terminal/post-exit ExecutionAttempt conflicts with an active/uncertain RemoteJob"
        )


def _validate_recovery_authorization(
    *,
    node: SchedulerDagNode,
    latest: ExecutionAttempt,
    decision: RecoveryDecision,
) -> None:
    if decision.action is not RecoveryAction.NEW_EXECUTION_ATTEMPT:
        raise BatchDispatchError(
            "batch recovery may create attempts only from NEW_EXECUTION_ATTEMPT decisions"
        )
    if latest.status not in _RECOVERY_TERMINAL_ATTEMPTS:
        raise BatchDispatchError(
            "batch recovery cannot create a new attempt while the latest attempt may still run"
        )
    if not decision.scientific_identity_preserved or decision.requires_new_calculation:
        raise BatchDispatchError("scientific recovery cannot be executed inside scheduler DAG")
    if latest.execution_plan_hash is None:
        raise BatchDispatchError("legacy attempt cannot authorize automatic batch recovery")

    if decision.requires_new_execution_plan:
        if decision.source_plan_hash != latest.execution_plan_hash:
            raise BatchDispatchError("recovery decision source plan does not match latest attempt")
        if node.plan.plan_hash == decision.source_plan_hash:
            raise BatchDispatchError("execution-tuning recovery requires a distinct ExecutionPlan")
        if decision.target_execution_hash != node.plan.execution_settings.execution_hash:
            raise BatchDispatchError(
                "recovery decision target execution hash does not match node plan"
            )
    else:
        if decision.source_plan_hash != node.plan.plan_hash:
            raise BatchDispatchError(
                "same-plan recovery decision does not match scheduler node plan"
            )
        if latest.execution_plan_hash != node.plan.plan_hash:
            raise BatchDispatchError(
                "same-plan recovery does not match latest attempt provenance"
            )


def _topological_order(nodes: tuple[SchedulerDagNode, ...]) -> tuple[str, ...]:
    by_id = {item.node_id: item for item in nodes}
    indegree = {item.node_id: len(item.depends_on) for item in nodes}
    children: dict[str, list[str]] = {item.node_id: [] for item in nodes}
    for node in nodes:
        for dependency in node.depends_on:
            children[dependency].append(node.node_id)
    for values in children.values():
        values.sort()

    ready = sorted(node_id for node_id, count in indegree.items() if count == 0)
    ordered: list[str] = []
    while ready:
        node_id = ready.pop(0)
        ordered.append(node_id)
        for child in children[node_id]:
            indegree[child] -= 1
            if indegree[child] == 0:
                bisect.insort(ready, child)
    if len(ordered) != len(by_id):
        raise ValueError("SchedulerDag must be acyclic")
    return tuple(ordered)

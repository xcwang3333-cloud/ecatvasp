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
    BatchDispatchMode,
    BatchDispatchSnapshot,
    BatchNodeState,
    ExecutionEvidence,
    RecoveryCause,
    RecoveryRequest,
    SchedulerDag,
    SchedulerDagNode,
    classify_recovery,
    create_execution_attempt,
    derive_execution_recovery_plan,
    prepare_batch_dispatch_wave,
    reconcile_batch_dispatch,
)
from ecatvasp.vasp.contracts import VaspSystemContext, VaspSystemKind
from ecatvasp.vasp.execution_plan import (
    ExecutionPlan,
    PotcarResolutionEntry,
    PotcarResolutionRequest,
    VaspRuntimeConstraints,
)


def _calculation(project: Project, slug: str) -> Calculation:
    return Calculation(
        project_id=project.id,
        calculation_type=CalculationType.RELAX,
        input_structure_snapshot_id=new_structure_snapshot_id(),
        recipe_id="ECatVASP.VASP.AdsorbateRelax",
        method_fingerprint_id=new_method_fingerprint_id(),
        slug=slug,
    )


def _plan(
    calculation: Calculation,
    execution_settings: ExecutionSettings | None = None,
) -> ExecutionPlan:
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
        execution_settings=execution_settings or ExecutionSettings(),
    )


def _node(
    node_id: str,
    calculation: Calculation,
    *,
    plan: ExecutionPlan | None = None,
    depends_on: tuple[str, ...] = (),
) -> SchedulerDagNode:
    return SchedulerDagNode(
        node_id=node_id,
        calculation=calculation,
        plan=plan or _plan(calculation),
        depends_on=depends_on,
    )


def _state(snapshot: BatchDispatchSnapshot, node_id: str) -> BatchNodeState:
    return next(item.state for item in snapshot.observations if item.node_id == node_id)


def test_scheduler_dag_is_deterministic_and_rejects_cycles() -> None:
    project = Project(name="Batch", slug="batch")
    a = _calculation(project, "a")
    b = _calculation(project, "b")
    c = _calculation(project, "c")
    dag = SchedulerDag(
        nodes=(
            _node("c", c, depends_on=("a", "b")),
            _node("b", b),
            _node("a", a),
        )
    )

    assert dag.topological_order == ("a", "b", "c")
    assert len(dag.dag_hash) == 64

    with pytest.raises(ValueError, match="acyclic"):
        SchedulerDag(
            nodes=(
                _node("a", a, depends_on=("b",)),
                _node("b", b, depends_on=("a",)),
            )
        )


def test_scheduler_dag_requires_exact_scientific_handoff() -> None:
    project = Project(name="Batch", slug="batch")
    a = _calculation(project, "a")
    b = _calculation(project, "b")

    with pytest.raises(ValueError, match="belong"):
        SchedulerDagNode(node_id="a", calculation=a, plan=_plan(b))


def test_batch_wave_respects_concurrency_and_is_resume_idempotent() -> None:
    project = Project(name="Batch", slug="batch")
    calculations = tuple(_calculation(project, value) for value in ("a", "b", "c"))
    dag = SchedulerDag(
        nodes=tuple(
            _node(value, calculation)
            for value, calculation in zip("abc", calculations, strict=True)
        )
    )
    concurrency = BatchConcurrencyPolicy(max_active=2)

    wave = prepare_batch_dispatch_wave(dag=dag, concurrency=concurrency)

    assert tuple(item.node_id for item in wave.tickets) == ("a", "b")
    assert all(item.mode is BatchDispatchMode.NEW_ATTEMPT for item in wave.tickets)
    assert len(wave.new_attempts) == 2

    resumed = prepare_batch_dispatch_wave(
        dag=dag,
        concurrency=concurrency,
        attempts=wave.new_attempts,
    )

    assert tuple(item.node_id for item in resumed.tickets) == ("a", "b")
    assert all(
        item.mode is BatchDispatchMode.CONTINUE_CREATED_ATTEMPT
        for item in resumed.tickets
    )
    assert resumed.new_attempts == ()


def test_existing_scheduler_job_consumes_capacity_without_duplicate_submission() -> None:
    project = Project(name="Batch", slug="batch")
    a = _calculation(project, "a")
    b = _calculation(project, "b")
    dag = SchedulerDag(nodes=(_node("a", a), _node("b", b)))
    a_node = dag.node("a")
    attempt = create_execution_attempt(plan=a_node.plan, calculation=a)
    queued = replace(attempt, status=ExecutionAttemptStatus.QUEUED)
    job = RemoteJob(
        execution_attempt_id=queued.id,
        scheduler=SchedulerType.SLURM,
        scheduler_job_id="1001",
        remote_directory=f"execution/{queued.id}",
        state=SchedulerState.PENDING,
    )

    snapshot = reconcile_batch_dispatch(
        dag=dag,
        concurrency=BatchConcurrencyPolicy(max_active=1),
        attempts=(queued,),
        remote_jobs=(job,),
    )

    assert _state(snapshot, "a") is BatchNodeState.QUEUED
    assert snapshot.active_count == 1
    assert snapshot.dispatchable_node_ids == ()


def test_created_attempt_is_resumable_but_staging_is_not_recreated() -> None:
    project = Project(name="Batch", slug="batch")
    calculation = _calculation(project, "a")
    dag = SchedulerDag(nodes=(_node("a", calculation),))
    node = dag.node("a")
    created = create_execution_attempt(plan=node.plan, calculation=calculation)

    created_wave = prepare_batch_dispatch_wave(
        dag=dag,
        concurrency=BatchConcurrencyPolicy(max_active=1),
        attempts=(created,),
    )
    assert created_wave.tickets[0].attempt.id == created.id
    assert created_wave.tickets[0].mode is BatchDispatchMode.CONTINUE_CREATED_ATTEMPT

    staging = replace(created, status=ExecutionAttemptStatus.STAGING)
    staging_wave = prepare_batch_dispatch_wave(
        dag=dag,
        concurrency=BatchConcurrencyPolicy(max_active=1),
        attempts=(staging,),
    )
    assert staging_wave.tickets == ()


def test_dependency_is_execution_order_only_and_waits_for_exit() -> None:
    project = Project(name="Batch", slug="batch")
    upstream = _calculation(project, "upstream")
    downstream = _calculation(project, "downstream")
    dag = SchedulerDag(
        nodes=(
            _node("upstream", upstream),
            _node("downstream", downstream, depends_on=("upstream",)),
        )
    )
    policy = BatchConcurrencyPolicy(max_active=2)

    initial = reconcile_batch_dispatch(dag=dag, concurrency=policy)
    assert _state(initial, "upstream") is BatchNodeState.READY
    assert _state(initial, "downstream") is BatchNodeState.WAITING_DEPENDENCIES

    up_node = dag.node("upstream")
    exited = replace(
        create_execution_attempt(plan=up_node.plan, calculation=upstream),
        status=ExecutionAttemptStatus.EXITED,
    )
    resumed = reconcile_batch_dispatch(
        dag=dag,
        concurrency=policy,
        attempts=(exited,),
    )

    assert _state(resumed, "upstream") is BatchNodeState.COMPLETE
    assert _state(resumed, "downstream") is BatchNodeState.READY
    up_observation = next(item for item in resumed.observations if item.node_id == "upstream")
    assert "does not assert scientific convergence" in up_observation.reason


def test_failed_dependency_blocks_descendants_until_explicit_recovery() -> None:
    project = Project(name="Batch", slug="batch")
    upstream = _calculation(project, "upstream")
    downstream = _calculation(project, "downstream")
    dag = SchedulerDag(
        nodes=(
            _node("upstream", upstream),
            _node("downstream", downstream, depends_on=("upstream",)),
        )
    )
    up_node = dag.node("upstream")
    failed = replace(
        create_execution_attempt(plan=up_node.plan, calculation=upstream),
        status=ExecutionAttemptStatus.FAILED,
    )

    snapshot = reconcile_batch_dispatch(
        dag=dag,
        concurrency=BatchConcurrencyPolicy(max_active=2),
        attempts=(failed,),
    )

    assert _state(snapshot, "upstream") is BatchNodeState.RECOVERY_REQUIRED
    assert _state(snapshot, "downstream") is BatchNodeState.BLOCKED_DEPENDENCY


def test_same_plan_recovery_decision_authorizes_next_attempt() -> None:
    project = Project(name="Batch", slug="batch")
    calculation = _calculation(project, "a")
    dag = SchedulerDag(nodes=(_node("a", calculation),))
    node = dag.node("a")
    failed = replace(
        create_execution_attempt(plan=node.plan, calculation=calculation),
        status=ExecutionAttemptStatus.FAILED,
    )
    decision = classify_recovery(
        plan=node.plan,
        request=RecoveryRequest(
            cause=RecoveryCause.VASP_FAILURE,
            evidence=ExecutionEvidence.VASP_LAUNCH_CONFIRMED,
        ),
    )

    wave = prepare_batch_dispatch_wave(
        dag=dag,
        concurrency=BatchConcurrencyPolicy(max_active=1),
        attempts=(failed,),
        recovery_decisions={"a": decision},
    )

    assert wave.tickets[0].mode is BatchDispatchMode.RECOVERY_NEW_ATTEMPT
    assert wave.tickets[0].attempt.attempt_number == 2
    assert wave.tickets[0].attempt.previous_attempt_id == failed.id
    assert wave.tickets[0].recovery_decision_hash == decision.decision_hash


def test_execution_tuning_recovery_requires_explicit_new_plan_authorization() -> None:
    project = Project(name="Batch", slug="batch")
    calculation = _calculation(project, "a")
    old_plan = _plan(calculation)
    failed = replace(
        create_execution_attempt(plan=old_plan, calculation=calculation),
        status=ExecutionAttemptStatus.FAILED,
    )
    settings = replace(old_plan.execution_settings, ncore=2)
    decision = classify_recovery(
        plan=old_plan,
        request=RecoveryRequest(
            cause=RecoveryCause.EXECUTION_TUNING,
            evidence=ExecutionEvidence.VASP_LAUNCH_CONFIRMED,
            proposed_execution_settings=settings,
        ),
    )
    new_plan = derive_execution_recovery_plan(
        plan=old_plan,
        execution_settings=settings,
    )
    dag = SchedulerDag(nodes=(_node("a", calculation, plan=new_plan),))

    without_decision = reconcile_batch_dispatch(
        dag=dag,
        concurrency=BatchConcurrencyPolicy(max_active=1),
        attempts=(failed,),
    )
    assert _state(without_decision, "a") is BatchNodeState.STALE_PLAN

    wave = prepare_batch_dispatch_wave(
        dag=dag,
        concurrency=BatchConcurrencyPolicy(max_active=1),
        attempts=(failed,),
        recovery_decisions={"a": decision},
    )
    assert wave.tickets[0].attempt.execution_plan_hash == new_plan.plan_hash
    assert wave.tickets[0].attempt.attempt_number == 2


def test_scientific_recovery_decision_cannot_enter_scheduler_dag() -> None:
    project = Project(name="Batch", slug="batch")
    calculation = _calculation(project, "a")
    dag = SchedulerDag(nodes=(_node("a", calculation),))
    node = dag.node("a")
    failed = replace(
        create_execution_attempt(plan=node.plan, calculation=calculation),
        status=ExecutionAttemptStatus.FAILED,
    )
    scientific = classify_recovery(
        plan=node.plan,
        request=RecoveryRequest(
            cause=RecoveryCause.SCIENTIFIC_INPUT_CHANGE,
            evidence=ExecutionEvidence.VASP_LAUNCH_CONFIRMED,
            proposed_incar_tags=("ENCUT",),
        ),
    )

    with pytest.raises(BatchDispatchError, match="NEW_EXECUTION_ATTEMPT"):
        reconcile_batch_dispatch(
            dag=dag,
            concurrency=BatchConcurrencyPolicy(max_active=1),
            attempts=(failed,),
            recovery_decisions={"a": scientific},
        )


def test_queued_batch_attempt_without_remote_job_fails_closed() -> None:
    project = Project(name="Batch", slug="batch")
    calculation = _calculation(project, "a")
    dag = SchedulerDag(nodes=(_node("a", calculation),))
    node = dag.node("a")
    queued = replace(
        create_execution_attempt(plan=node.plan, calculation=calculation),
        status=ExecutionAttemptStatus.QUEUED,
    )

    with pytest.raises(BatchDispatchError, match="requires persisted RemoteJob"):
        reconcile_batch_dispatch(
            dag=dag,
            concurrency=BatchConcurrencyPolicy(max_active=1),
            attempts=(queued,),
        )

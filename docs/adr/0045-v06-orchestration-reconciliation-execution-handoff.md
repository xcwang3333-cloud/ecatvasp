# ADR-045: v0.6 Orchestration Reconciliation and Execution/Materialization Handoff

- Status: Accepted
- Date: 2026-09-04

## Context

ADR-043 derives current workflow generations and scientific gates. ADR-044 then classifies recovery
and continuation without performing side effects. The workflow layer can therefore answer whether a
logical step is scientifically ready, waiting, satisfied, blocked, stale, or requires a specific
recovery boundary, but there is still no single deterministic bridge from those decisions to the
already-frozen Block 4 materialization and v0.4 execution contracts.

That bridge must preserve several separations:

- scientific workflow edges are not scheduler dependencies;
- an unmaterialized downstream Calculation cannot be scheduled merely because an upstream scheduler
  job completed;
- a DRAFT/READY Calculation must not execute without an exact ExecutionPlan belonging to the current
  binding generation;
- SUBMITTED/RUNNING/PARSING work must not be dispatched a second time merely because reconciliation
  runs again;
- same-attempt retry/resubmission from ADR-027 must not be converted into a new ExecutionAttempt;
- only `NEW_EXECUTION_ATTEMPT` recovery belongs in the existing v0.4 scheduler-DAG new-attempt path;
- durable workflow resume/idempotency is still Block 8 scope.

## Decision

### 1. Block 7 adds a pure orchestration reconciler

Block 7 adds `reconcile_workflow_orchestration()`. It consumes:

- one canonical `ScientificWorkflowPlan`;
- the exact Block 5 `WorkflowScientificGateEvaluation`;
- the exact Block 6 `WorkflowRecoveryPolicyEvaluation`;
- optional caller-supplied `WorkflowExecutionSource` values for current binding generations.

It returns `WorkflowOrchestrationEvaluation`, containing one `WorkflowStepOrchestration` handoff per
logical step plus, when applicable, an existing v0.4 `SchedulerDag` and exact recovery-decision map.

The evaluator is side-effect free. It does not write ProjectStore, create/persist ExecutionAttempts,
stage files, submit jobs, poll schedulers, reconstruct CONTCAR, create MethodFingerprints, or call
`materialize_workflow_step()` automatically.

### 2. Ordinary materialization is an exact typed handoff

When Block 5 says an unmaterialized step is `READY` and Block 6 correctly returns recovery action
`NONE`, Block 7 emits `MATERIALIZE_STEP`.

For a root step the handoff pins:

- `ScientificWorkflowPlan.root_structure_snapshot_id`;
- no upstream source binding;
- no previous binding generation.

For a downstream step the handoff requires exactly one current open `accepted_structure` edge and
pins:

- `WorkflowEdgeGate.accepted_structure_snapshot_id`;
- `WorkflowEdgeGate.source_binding_id`.

The caller must still supply the concrete StructureSnapshot/AcceptedStructureSource,
MethodFingerprint, VASP system context, and numerical lock to Block 4. Block 7 never fabricates any of
those values.

### 3. Recovery rematerialization preserves Block 6 identity

When Block 6 returns `REMATERIALIZE_STEP`, Block 7 emits the same `MATERIALIZE_STEP` handoff but also
pins:

- the exact previous/current binding UUID to supersede;
- the exact target StructureSnapshot UUID;
- current accepted upstream source-binding UUID where applicable;
- Block 6 materialization reason.

The target is independently reconciled against the current Block 5 edge before handoff. A stale
Block 6 projection therefore cannot rematerialize against an older upstream structure.

### 4. DRAFT/READY Calculations require an exact ExecutionPlan

Block 5 intentionally groups DRAFT, READY, SUBMITTED, RUNNING, and PARSING into workflow
`IN_PROGRESS`; Block 6 therefore treats all of them as non-recovery progress.

Block 7 refines only the orchestration handoff using the current Calculation status:

- DRAFT/READY without a supplied ExecutionPlan -> `EXECUTION_PLAN_REQUIRED`;
- DRAFT/READY with an exact current-generation ExecutionPlan -> `EXECUTION_READY`;
- SUBMITTED/RUNNING/PARSING -> `EXECUTION_IN_FLIGHT`.

A `WorkflowExecutionSource` must reference:

- the exact current WorkflowStepBinding;
- the exact current Calculation;
- an ExecutionPlan whose Calculation and recipe match that generation.

Execution sources from superseded generations fail closed.

### 5. Scientific workflow edges are not copied into SchedulerDag

For execution-ready generations, Block 7 creates v0.4 `SchedulerDagNode` values with no scheduler
`depends_on` edges.

This is intentional. The scientific workflow dependency has already been satisfied before a
downstream Calculation can be materialized. Copying `accepted_structure` into a scheduler dependency
would create a second scientific workflow engine and could incorrectly equate scheduler completion
with scientific convergence/promotion.

Consequently, multiple downstream calculations materialized from the same accepted structure may be
handed to the execution layer concurrently.

Scheduler node ids are deterministic within one workflow projection:

`workflow-{step_key}-g{generation}`.

### 6. Existing v0.4 batch contracts remain authoritative

Block 7 constructs only `SchedulerDag`/recovery mapping values already understood by
`reconcile_batch_dispatch()` and `prepare_batch_dispatch_wave()`.

It does not create ExecutionAttempts itself. The v0.4 batch contract continues to require every newly
created attempt to be persisted before SSH staging or scheduler submission.

Ordinary execution-ready nodes carry no RecoveryDecision.

### 7. Recovery routing preserves ADR-027 exactly

For Block 6 `EXECUTION_RECOVERY`, the caller supplies the exact `RecoveryDecision` together with the
candidate dispatch ExecutionPlan.

Block 7 validates the decision hash/action against Block 6 and preserves the following routing:

- `RETRY_SAME_ATTEMPT` -> `RETRY_SAME_ATTEMPT` handoff, not SchedulerDag new-attempt recovery;
- `RESUBMIT_SAME_ATTEMPT` -> `RESUBMIT_SAME_ATTEMPT` handoff, not SchedulerDag new-attempt recovery;
- `NEW_EXECUTION_ATTEMPT` -> `EXECUTION_RECOVERY_READY` plus exact v0.4 SchedulerDag recovery map.

If execution-only tuning requires a new ExecutionPlan, the dispatch plan must be distinct from the
source plan and its ExecutionSettings hash must equal `RecoveryDecision.target_execution_hash`.
Otherwise the exact source plan must be reused.

Block 7 never promotes `NEW_CALCULATION`, `NEW_STRUCTURE_AND_CALCULATION`, or manual recovery into an
execution action; those were already resolved by Block 6 at the scientific/workflow boundary.

### 8. Waiting, manual, and new-plan states remain observational

Block 7 maps Block 6 states without inventing side effects:

- `WAIT_FOR_PREREQUISITE` -> `WAIT` unless a DRAFT/READY current Calculation merely needs an
  ExecutionPlan;
- `RECOVERY_DECISION_REQUIRED` -> same explicit requirement;
- `NEW_WORKFLOW_PLAN_REQUIRED` -> same explicit requirement;
- `MANUAL_REVIEW_REQUIRED` -> same explicit requirement;
- scientifically passed current generation -> `SATISFIED`.

Supplying an execution source for a waiting, satisfied, in-flight, materialization, manual, or
new-workflow-plan state fails closed rather than silently dispatching it.

### 9. The orchestration projection is deterministic but not durable workflow state

`WorkflowOrchestrationEvaluation` computes an `orchestration_hash` over:

- workflow plan UUID;
- ordered per-step handoffs;
- optional SchedulerDag hash;
- scheduler recovery decision hashes.

This hash is audit evidence for one reconciliation input, not an idempotency token, lease, durable
command queue, or exactly-once key.

Project schema remains v3 and the development version remains `0.6.0.dev0`.

## Compatibility with frozen lower layers

Block 7 preserves:

- ADR-027 recovery identity and same-attempt/new-attempt separation;
- ADR-028 scheduler-only DAG semantics and two-phase attempt persistence boundary;
- ADR-042 exact Calculation/binding materialization;
- ADR-043 scientific gate/current-generation semantics;
- ADR-044 workflow recovery/continuation policy.

No scheduler state is interpreted as scientific convergence and no older workflow generation is used
as fallback input.

## Reserved later scope

- Block 8: durable workflow persistence/reopen/resume/idempotency and repeated-reconciliation safety;
- Block 9: end-to-end workflow acceptance and hardening.

## Explicit non-scope

Block 7 does not add:

- daemon, polling loop, background worker, or scheduler monitor;
- ProjectStore writes or automatic persistence;
- workflow command rows, leases, claims, retry counters, resume cursors, or idempotency keys;
- automatic MethodFingerprint construction;
- automatic Block 4 Calculation/binding materialization;
- automatic ExecutionPlan construction from local files;
- ExecutionAttempt persistence or scheduler submission;
- SSH staging, RemoteJob creation, retrieval, or parsing;
- CONTCAR reconstruction/promotion or arbitrary continuation injection;
- scientific-edge-to-scheduler-edge translation;
- thermochemistry, Bader, DOS/PDOS, COHP, band-center, or LOBSTER interpretation;
- schema v4, tag, GitHub Release, or PyPI publication.

## Consequences

ECatVASP now has one deterministic reconciliation boundary between scientific workflow state and
existing materialization/execution machinery. Ready scientific work becomes an exact materialization
or execution handoff, in-flight work remains observational, recovery preserves the frozen v0.4
identity model, and the execution layer never substitutes scheduler completion for scientific gate
satisfaction. Block 8 can add durable repeated-reconciliation semantics without changing these
scientific or execution identities.

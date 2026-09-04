# ADR-046: v0.6 Durable Workflow Reopen, Resume, and Idempotency

- Status: Accepted
- Date: 2026-09-04

## Context

ADR-045 produces a deterministic, side-effect-free orchestration projection, but explicitly does not
make that projection durable. A process may stop after persisting a workflow plan, after creating a
Calculation/binding generation, or after allocating an ExecutionAttempt but before the caller stages
or submits it. Re-running the same orchestration after `ProjectStore` reopen must not create duplicate
scientific generations or duplicate execution attempts.

The durable layer must preserve the identity boundaries already frozen below it:

- `ScientificWorkflowPlan.plan_hash` is the deterministic scientific workflow intent identity, while
  the plan UUID remains a persistence identity;
- `WorkflowStepBinding` generations are append-only and supersede explicitly;
- `Calculation` scientific identity remains independent from execution retries;
- `ExecutionAttempt.previous_attempt_id` already records immutable retry ancestry;
- v0.4 batch dispatch requires every new attempt to be persisted before staging/submission;
- same-attempt retry/resubmission may involve remote side effects that cannot be transactionally
  committed with the local ProjectStore.

A second persisted workflow state machine, command queue, cursor, or lease would duplicate facts that
already exist in the ProjectStore and create new disagreement modes. Block 8 therefore uses existing
immutable domain facts as durable receipts wherever they are sufficient.

## Decision

### 1. Durable resume is reconstructed from ProjectStore facts

Block 8 adds `reopen_workflow_resume_state()`.

For one persisted `ScientificWorkflowPlan`, it reconstructs the exact durable subset containing:

- every `WorkflowStepBinding` generation for the plan;
- every Calculation referenced by those bindings;
- every ExecutionAttempt for those Calculations;
- every RemoteJob for those attempts.

`WorkflowResumeState.resume_hash` is a deterministic audit hash over those exact reopened facts. It is
not a mutable workflow-state row and does not replace Block 5/6/7 evaluation. Scientific gates,
recovery policy, and orchestration remain derived again from current durable facts by their existing
layers.

### 2. Persisted workflow-plan reuse is keyed by exact scientific `plan_hash`

`persist_or_reuse_workflow_plan()` consumes one Block 3 `WorkflowPlanningResult` and an already
initialized ProjectStore.

The candidate must belong to the persisted Project and its exact root StructureSnapshot must already
be durable.

- no persisted plan with the candidate `plan_hash` -> persist the candidate plan;
- exactly one persisted plan with that hash -> return the existing plan and its original UUID;
- more than one persisted plan with that hash -> fail closed because persistence history is ambiguous.

This makes repeated planning after reopen idempotent without replacing UUIDv7 entity identity with a
content-derived UUID.

### 3. Materialization idempotency uses the binding generation slot

`persist_or_reuse_workflow_materialization()` consumes an exact Block 7 `MATERIALIZE_STEP` handoff
plus the concrete Block 4 inputs.

Before materialization, Block 8 requires the referenced plan, MethodFingerprint, root or accepted
StructureSnapshot lineage, and previous binding (when rematerializing) to be durably present.

The function calls the existing Block 4 materializer as the sole constructor/validator of the new
Calculation and binding semantics. The expected `(workflow plan, step key, generation)` is then used
as the durable idempotency slot.

If the expected generation already exists, Block 8 reuses it only when:

- plan, step, generation, resolved input StructureSnapshot, and supersession semantics match;
- the persisted Calculation has the same Project, CalculationType, engine, input snapshot, recipe,
  and MethodFingerprint identity.

A conflicting Calculation or binding in the same generation slot fails closed. Human-readable
`materialization_reason` remains audit context and is not part of scientific reuse identity.

If the generation does not exist, the Calculation and WorkflowStepBinding are appended in one
ProjectStore save and the store is reopened before the receipt is returned.

Therefore a process crash after the save but before receiving the result is safe: replay discovers and
returns the already-persisted generation instead of creating another Calculation.

### 4. New ExecutionAttempts cross a persistence barrier before exposure

`persist_workflow_dispatch_wave()` consumes the exact Block 7 SchedulerDag handoff and current durable
workflow facts.

It delegates attempt allocation to the existing v0.4 `prepare_batch_dispatch_wave()` contract. Any
new attempts from that wave are persisted to ProjectStore before the function returns a scheduler
wave to the caller.

After saving, Block 8 reopens the store and runs batch reconciliation again. Consequently newly
allocated attempts appear to the caller as the exact persisted `CONTINUE_CREATED_ATTEMPT` tickets,
not as unpersisted in-memory attempt objects.

This preserves ADR-028's two-phase boundary: persistence precedes SSH staging or scheduler
submission.

### 5. Recovery replay is bound to an explicit source attempt

A v0.4 `RecoveryDecision` intentionally describes identity changes and plan hashes, not a workflow
queue position. To make repeated `NEW_EXECUTION_ATTEMPT` recovery durable without adding a new receipt
entity, Block 8 adds `WorkflowRecoveryAttemptSource` containing the exact source ExecutionAttempt ID.

For a recovery-ready scheduler node:

- the source attempt must belong to the current workflow Calculation;
- if it has no direct successor, it must be the latest persisted terminal attempt before the recovery
  decision may allocate a new attempt;
- if it already has exactly one direct successor through `previous_attempt_id`, that successor is the
  durable evidence that recovery from this source attempt was already consumed;
- the successor must be attempt number `source + 1` and must pin the exact dispatch ExecutionPlan;
- multiple direct successors fail closed as an invalid fork.

Once a direct successor exists, replaying the old source-bound recovery decision never allocates a
second child, even if the existing child later fails. Recovering that later failure requires a new
explicit recovery operation bound to that child attempt as the new source.

This uses the existing immutable attempt chain as the durable receipt and avoids a redundant recovery
command table.

### 6. Stale orchestration cannot dispatch a superseded workflow generation

Before batch reconciliation, every SchedulerDag node is checked against the reopened workflow state.
Its handoff must still reference the exact current WorkflowStepBinding and the exact persisted
Calculation object.

An orchestration projection produced before a later binding generation or Calculation-state update
therefore fails closed instead of silently dispatching old work.

### 7. Same-attempt external operations are not claimed to be exactly-once

`RETRY_SAME_ATTEMPT` and `RESUBMIT_SAME_ATTEMPT` remain governed by ADR-027 and the lower execution
adapters. They are deliberately not converted into persisted workflow commands by Block 8.

ProjectStore cannot atomically commit an SSH copy, scheduler submission, or other remote side effect.
Exactly-once semantics for those external effects would therefore be false. Existing positive
no-side-effect/no-launch evidence and remote reconciliation remain authoritative.

Block 8 guarantees durable local identity replay for workflow plan reuse, Calculation/binding
materialization, and new-attempt allocation; it does not promise exactly-once network or scheduler
operations.

### 8. ProjectStore remains single-writer for workflow mutation

ProjectStore already provides atomic file replacement and integrity verification, but it does not
provide a compare-and-swap generation counter, distributed lock, or lease protocol.

Block 8 therefore defines replay safety for a single logical writer. Concurrent independent processes
must not mutate the same project directory simultaneously. Multi-writer leases/claims, daemon
coordination, and distributed exactly-once execution are outside v0.6 scope.

### 9. No schema or development-version change

Block 8 adds no persisted entity and no persisted field. Existing schema-v3 facts are sufficient:

- ScientificWorkflowPlan for persisted workflow intent;
- WorkflowStepBinding generation/supersession for materialization receipts;
- ExecutionAttempt attempt number/previous-attempt chain for dispatch/recovery receipts;
- RemoteJob for scheduler facts.

Project schema therefore remains v3 and the development version remains `0.6.0.dev0`.

## Compatibility with frozen lower layers

Block 8 preserves:

- ADR-006 file-first ProjectStore and explicit schema migration rules;
- ADR-020 immutable execution-attempt provenance;
- ADR-027 recovery identity and evidence classification;
- ADR-028 persist-before-side-effect scheduler dispatch;
- ADR-039 workflow-plan and binding persistence identity;
- ADR-041 deterministic planning identity;
- ADR-042 sole Calculation/binding materialization constructor;
- ADR-043 scientific current-generation and freshness semantics;
- ADR-044 recovery/continuation policy;
- ADR-045 orchestration handoff semantics.

No scheduler state is converted into scientific convergence and no superseded workflow generation is
revived as current work.

## Reserved later scope

- Block 9: end-to-end workflow acceptance, adversarial replay tests, and final v0.6 hardening.

## Explicit non-scope

Block 8 does not add:

- a workflow daemon, polling loop, worker process, or scheduler monitor;
- a durable command queue, claim/lease table, retry counter, mutable resume cursor, or workflow state
  machine;
- multi-process/multi-host writer coordination;
- exactly-once guarantees for SSH or scheduler side effects;
- automatic MethodFingerprint construction;
- automatic scientific correction or recovery classification;
- scheduler dependencies derived from scientific workflow edges;
- CONTCAR reconstruction/promotion or arbitrary continuation injection;
- thermochemistry, reference energies, CHE, reaction free energies, potential/pH corrections;
- Bader, charge-density difference, DOS/PDOS, COHP, band-center, or LOBSTER interpretation;
- schema v4, tag, GitHub Release, or PyPI publication.

## Consequences

ECatVASP workflow orchestration can now survive process restart using only durable scientific and
execution identities. Repeated planning reuses one persisted workflow plan, repeated materialization
reuses one generation slot, newly allocated execution attempts are persisted before exposure, and a
source-attempt-bound recovery cannot be replayed into duplicate child attempts. The result remains a
small single-writer persistence layer rather than a second workflow engine.

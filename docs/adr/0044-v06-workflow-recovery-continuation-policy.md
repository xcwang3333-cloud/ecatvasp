# ADR-044: v0.6 Workflow Recovery and Continuation Policy

- Status: Accepted
- Date: 2026-09-04

## Context

ADR-043 gives each workflow step a current scientific state and readiness verdict, but intentionally
stops at `BLOCKED`: it does not decide whether a failed/unconverged step should retry execution,
create a new scientific Calculation generation, wait for a newer upstream accepted structure, or
require manual intervention.

ECatVASP already has a stricter lower-layer identity classifier from ADR-027. The v0.4
`RecoveryDecision` distinguishes:

- transport/control retry within the same ExecutionAttempt;
- scheduler replacement within the same ExecutionAttempt only with positive no-launch evidence;
- new ExecutionAttempt for confirmed/uncertain VASP launch;
- execution-only tuning with a new ExecutionPlan but the same Calculation;
- scientific initialization/input change with a new Calculation;
- CONTCAR continuation with a new StructureSnapshot and Calculation;
- manual review when automatic correction/tuning would otherwise change inputs/settings.

Block 6 must compose those frozen execution identity rules with v0.6 workflow generations. It must not
reclassify scheduler evidence, invent INCAR correction rules, or turn a scientific failure into an
execution retry merely because that is convenient for orchestration.

A second workflow-specific case also exists: after an upstream relaxation is rematerialized, a
previously completed downstream Calculation may still point to the older accepted structure. ADR-043
marks that downstream generation workflow `STALE`. The recovery layer must identify the exact
replacement input and authorize a new binding generation without claiming that the old immutable
snapshot was scientifically invalid.

## Decision

### 1. Block 6 is a pure policy layer

Block 6 adds `evaluate_workflow_recovery_policy()`. It consumes:

- one canonical `ScientificWorkflowPlan`;
- the exact Block 5 `WorkflowScientificGateEvaluation`;
- optional `WorkflowRecoverySource` values containing the current binding, current Calculation,
  source `ExecutionPlan`, and an already-classified v0.4 `RecoveryDecision`.

It returns one immutable `WorkflowStepRecoveryPolicy` per logical step.

The evaluator does not:

- execute a retry;
- create an ExecutionPlan, ExecutionAttempt, or RemoteJob;
- materialize a new Calculation/binding generation;
- reconstruct/promote CONTCAR;
- create a new ScientificWorkflowPlan;
- write ProjectStore;
- poll or reconcile workflow/execution state.

Those side effects remain delegated to later orchestration or existing lower-layer functions.

### 2. Recovery evidence must bind to the exact current generation

A `WorkflowRecoverySource` is accepted only when:

- its binding belongs to the requested workflow plan and step;
- that binding is the current Block 5 generation, never a superseded generation;
- the supplied Calculation is the exact Calculation referenced by that binding and gate;
- the supplied ExecutionPlan belongs to that Calculation and recipe;
- `RecoveryDecision.source_plan_hash` equals the supplied ExecutionPlan hash;
- `RecoveryDecision.source_execution_hash` equals the source ExecutionSettings hash;
- the decision's identity flags are internally consistent with its v0.4 `RecoveryAction`.

A recovery decision from an older workflow generation cannot be replayed against a newer generation.
A decision for another ExecutionPlan also fails closed.

### 3. Block 6 never reclassifies v0.4 recovery semantics

The workflow layer consumes, rather than duplicates, ADR-027.

The following v0.4 actions preserve the current workflow Calculation/binding generation:

- `RETRY_SAME_ATTEMPT`;
- `RESUBMIT_SAME_ATTEMPT`;
- `NEW_EXECUTION_ATTEMPT`.

They map to workflow `EXECUTION_RECOVERY`. Any new ExecutionPlan/new ExecutionAttempt details remain
owned by the existing v0.4 recovery helpers.

`NEW_CALCULATION` maps to workflow `REMATERIALIZE_STEP`: a new
`WorkflowStepBinding` generation is required and Block 4 remains the only function allowed to create
that Calculation/binding pair.

`MANUAL_REVIEW_REQUIRED` remains manual at workflow level. Block 6 never upgrades it to an automatic
action.

### 4. Ordinary workflow progress is not recovery

An unmaterialized step whose Block 5 readiness is `READY` receives workflow recovery action `NONE`.
It is ready for ordinary Block 7 orchestration/materialization, not a recovery operation.

An unmaterialized step waiting on upstream science, or a current generation still in progress,
receives `WAIT_FOR_PREREQUISITE`.

A current scientifically `PASSED`/`SATISFIED` step receives `NONE` and rejects an attached recovery
decision as a stale or inappropriate request.

### 5. Blocked generations require explicit recovery classification

A current workflow step in Block 5 `BLOCKED` state receives
`RECOVERY_DECISION_REQUIRED` unless its current scientific input is itself unavailable because an
upstream gate is not open.

When the current upstream scientific input is not available, the step waits for the upstream lineage
instead of attempting execution recovery against a no-longer-current input.

This prevents a downstream retry from racing ahead of a changed or unresolved upstream relaxation.

### 6. Scientific-input recovery creates a new binding generation

When a v0.4 `RecoveryDecision` requires `NEW_CALCULATION` without a new StructureSnapshot, Block 6
authorizes `REMATERIALIZE_STEP`.

The policy records:

- the exact previous/current binding UUID that must be superseded;
- the exact target StructureSnapshot UUID that the new generation must consume;
- the source ExecutionPlan and RecoveryDecision hashes;
- a recovery materialization reason.

For a root step the target remains the exact
`ScientificWorkflowPlan.root_structure_snapshot_id`.

For a downstream step the target must come from the current open `accepted_structure` edge. If the
current upstream accepted structure is not available, rematerialization waits rather than reusing an
older input.

Block 6 does not construct the new MethodFingerprint. Scientific changes must still be represented by
an explicit caller-supplied fingerprint that passes Block 4/VASP recipe and ProjectNumericalLock
validation.

### 7. Upstream binding supersession deterministically rematerializes stale downstream lineage

When ADR-043 marks a current downstream generation `STALE` specifically because
`accepted_structure_binding_superseded`, Block 6 does not require an execution recovery decision.

If the current upstream `accepted_structure` edge is open, Block 6 returns `REMATERIALIZE_STEP` with:

- the stale downstream binding as `previous_binding_id`;
- the exact current accepted upstream StructureSnapshot UUID as target input.

If that upstream edge is not yet open, the downstream step waits.

This is workflow lineage continuation, not VASP error correction.

### 8. Arbitrary CONTCAR continuation requires a new workflow plan

ADR-027 classifies `CONTCAR_CONTINUATION` as `NEW_STRUCTURE_AND_CALCULATION` because the scientific
structure input changes.

Within the current v0.6 canonical workflow contract, Block 4 enforces:

- root steps consume exactly `ScientificWorkflowPlan.root_structure_snapshot_id`;
- downstream steps consume exactly the current explicitly promoted `accepted_structure` edge.

Therefore an arbitrary continuation StructureSnapshot cannot be inserted as generation N+1 inside
the same workflow plan without bypassing ADR-042.

Block 6 maps `NEW_STRUCTURE_AND_CALCULATION` to `NEW_WORKFLOW_PLAN_REQUIRED`. After the continuation
geometry is explicitly reconstructed/imported as a new immutable StructureSnapshot, a new workflow
plan must be created with the appropriate new scientific root intent.

Block 6 does not create that StructureSnapshot or new plan.

### 9. Generic STALE/INVALID states remain fail closed

A workflow generation that is scientifically `INVALID`, or is generically `STALE` for reasons other
than the explicit upstream-binding supersession case, maps to `MANUAL_REVIEW_REQUIRED`.

Block 6 does not assume that rematerializing the same Calculation request will repair arbitrary
scientific provenance drift or invalidity. A human/caller must first determine which scientific
identity/input actually changed.

A current generation unexpectedly marked `SUPERSEDED` also requires manual resolution because it
conflicts with the normal terminal-generation selection invariant.

### 10. No durable recovery queue or automatic execution

`WorkflowRecoveryPolicyEvaluation` is a derived value, not a persisted workflow state machine. It
contains no lease, retry counter, resume cursor, pending command, or exactly-once execution token.

Block 7 may consume these policy values when reconciling workflow state and handing work to existing
execution/materialization functions. Block 8 remains responsible for durable persistence,
reopen/resume, and idempotency semantics.

Project schema remains v3 and the development version remains `0.6.0.dev0`.

## Compatibility with frozen lower layers

Block 6 preserves the following boundaries:

- ADR-027 remains authoritative for execution/scheduler recovery identity;
- ADR-035/037 remain authoritative for scientific convergence, result provenance, and promoted
  structures;
- ADR-042 remains authoritative for new Calculation/binding materialization;
- ADR-043 remains authoritative for current generation, freshness, and scientific gate state.

No scheduler state is promoted directly to scientific success and no old successful workflow
generation is used as a fallback.

## Reserved later scope

- Block 7: orchestration reconciliation and execution/materialization handoff;
- Block 8: durable persistence, reopen/resume, and idempotency;
- Block 9: end-to-end workflow acceptance and hardening.

## Explicit non-scope

Block 6 does not add:

- automatic VASP error-string handlers or custodian-style scientific corrections;
- automatic INCAR/KPOINTS/POTCAR changes;
- execution retry/resubmission side effects;
- automatic creation of a replacement MethodFingerprint;
- CONTCAR parsing/reconstruction/promotion;
- arbitrary continuation geometry injection into an existing workflow plan;
- ExecutionPlan, ExecutionAttempt, RemoteJob, scheduler submission, or scheduler polling creation;
- workflow reconciler/daemon/polling loop;
- ProjectStore writes, recovery queue rows, leases, retry counters, resume cursors, or idempotency
  keys;
- thermochemistry, Bader, DOS/PDOS, COHP, band-center, or LOBSTER interpretation;
- schema v4, tag, GitHub Release, or PyPI publication.

## Consequences

ECatVASP now has an explicit bridge from scientific workflow gates to the already-frozen execution
recovery identity model. Execution-only failures can recover without creating fake scientific
versions; scientific input changes become new binding generations; upstream structure supersession
creates deterministic downstream rematerialization; and arbitrary structure continuation cannot
silently mutate the scientific intent of an existing workflow plan.

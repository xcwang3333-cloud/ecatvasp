# ADR-047: v0.6 Final Workflow Acceptance and Hardening

- Status: Accepted
- Date: 2026-09-04

## Context

Blocks 1–8 established durable scientific workflow identity, canonical recipe DAGs, deterministic
planning, exact Calculation/binding materialization, scientific gates, recovery/continuation policy,
orchestration handoff, and replay-safe ProjectStore resume semantics. The final v0.6 block must prove
that those independently frozen layers compose into one coherent workbench boundary without adding a
second workflow engine or silently changing lower-layer identities.

Final acceptance must be stricter than checking that each helper succeeds in isolation. A workflow
projection may have been computed before the ProjectStore changed; an `accepted_structure` edge may
reference scientific evidence that was never durably saved; a scheduler handoff may refer to a
superseded Calculation; or duplicate persisted plans may carry the same scientific `plan_hash`.
These cases must fail closed during final cross-layer validation.

## Decision

### 1. Final acceptance is pure and read-only

Block 9 adds `validate_v06_workflow_acceptance()` and immutable `WorkflowAcceptanceReport` values.
The validator performs no materialization, retry, submission, parser invocation, structure promotion,
or ProjectStore mutation.

It reopens ProjectStore and validates one supplied canonical `ScientificWorkflowPlan` against the
current durable facts plus the exact Block 5, Block 6, and Block 7 projections supplied by the caller.

### 2. Durable current generation is authoritative

The validator recomputes current workflow binding generations from reopened
`WorkflowStepBinding`/`Calculation` facts using the frozen Block 5 generation resolver.

The recomputed selections must exactly equal the binding selections used by the supplied scientific
gates. This detects projections created before a Calculation-state mutation or binding-generation
change. Recovery policies and orchestration handoffs must then reference the same exact current
binding and Calculation IDs.

No older successful generation may be revived during acceptance.

### 3. Persisted workflow scientific identity must be unique

The exact supplied workflow plan must occur once in ProjectStore, and its scientific `plan_hash` must
also be unique. Multiple persisted plan UUIDs with the same `plan_hash` are treated as ambiguous
history and fail closed rather than choosing one arbitrarily.

### 4. Open accepted-structure edges require durable scientific structure identity

Every open canonical `accepted_structure` edge must:

- reference the current upstream binding generation;
- carry an exact promoted StructureSnapshot ID;
- point to a StructureSnapshot that is currently persisted in ProjectStore;
- match the exact input StructureSnapshot of an already-materialized current downstream binding.

This final check does not reconstruct or promote CONTCAR. It verifies that the output of the existing
v0.5 promotion boundary actually crossed the durable workflow boundary before downstream scientific
work is accepted as current.

### 5. Scheduler handoff remains execution-only and current-generation-only

If the Block 7 orchestration projection contains a `SchedulerDag`, every scheduler node must match the
current workflow Calculation for its step and the exact ExecutionPlan hash named by the workflow
handoff.

Scheduler nodes may not reference superseded workflow generations. Scheduler recovery decision hashes
must match the corresponding current workflow recovery handoff exactly.

Scientific workflow edges are still not converted into scheduler dependencies. Block 9 validates the
handoff that already exists; it does not create a new scheduler DAG or submit work.

### 6. Recovery attempt ancestry must not fork

Final acceptance validates the reopened `ExecutionAttempt.previous_attempt_id` graph for workflow
Calculations. A recovery child must remain inside one Calculation, increment its parent's attempt
number contiguously, and a parent attempt may have at most one direct successor.

This hardens the Block 8 durable-receipt rule against adversarial persisted history. It does not claim
exactly-once semantics for SSH or scheduler side effects.

### 7. Acceptance state classifies the validated projection, not scientific quantities

`WorkflowAcceptanceReport.state` has three values:

- `COMPLETE`: every current workflow step is scientifically `PASSED` and `SATISFIED`, every Block 7
  handoff is `SATISFIED`, and no scheduler work remains;
- `RESUMABLE`: the cross-layer state is valid and can continue through an already-defined safe v0.6
  handoff such as materialization, waiting, execution preparation, in-flight execution, or authorized
  execution recovery;
- `ACTION_REQUIRED`: the validated workflow requires an explicit recovery decision, a new workflow
  plan, or manual scientific review.

The report is a deterministic audit projection and includes a canonical `acceptance_hash` over the
plan, reopened resume state, accepted step states, scheduler-node identities, checks, and schema
version.

### 8. Final acceptance does not persist another state machine

`WorkflowAcceptanceReport` is a value object only. It is deliberately not added to `ProjectBundle`,
provenance subjects, or the schema. Reopening a project and recomputing acceptance from unchanged
facts produces the same `resume_hash` and `acceptance_hash`.

Project schema therefore remains v3 and the development version remains `0.6.0.dev0`.

## End-to-end acceptance coverage

Block 9 acceptance tests exercise:

- a complete canonical slab workflow from planning through exact structure-bound materialization,
  explicit promoted relaxed structure, scientific gates, recovery policy, orchestration, ProjectStore
  reopen, and final `COMPLETE` report;
- an initial canonical workflow accepted as safely `RESUMABLE`;
- a failed current generation classified as `ACTION_REQUIRED`;
- rejection of a gate/orchestration projection that became stale after durable Calculation mutation;
- rejection of an open accepted-structure edge whose promoted snapshot was never durably persisted;
- rejection of duplicate persisted workflow plans sharing one scientific `plan_hash`;
- deterministic acceptance hashes across ProjectStore reopen.

Existing Block 8 tests remain the authoritative crash-window and duplicate-dispatch tests for durable
plan, binding-generation, and ExecutionAttempt replay.

## Compatibility with frozen boundaries

Block 9 preserves:

- `Calculation != ExecutionAttempt != RemoteJob`;
- scheduler success is not scientific convergence;
- parser facts and convergence verdict remain separate;
- explicit v0.5 structure promotion before downstream use;
- exact immutable StructureSnapshot identity with no nearest-neighbour guessing;
- scientific freshness and workflow-generation currentness as separate concepts;
- v0.4 recovery identity and persist-before-side-effect dispatch;
- Block 8 single-writer replay semantics without false remote exactly-once guarantees.

## Explicit non-scope

Block 9 does not add:

- DOS/PDOS interpretation, Bader, charge-density difference, COHP, band center, or LOBSTER analysis;
- ZPE, entropy, thermal corrections, reference energies, CHE, reaction free energies, potential, or
  pH correction;
- workflow daemon, polling service, distributed lease, command queue, or multi-writer protocol;
- new scheduler backend, Slurm array support, GUI, or plugin runtime;
- schema v4, tag, GitHub Release, or PyPI publication.

## Consequences

v0.6 now has a final read-only acceptance boundary that can prove one reopened scientific workflow is
internally coherent across planning, scientific state, recovery, execution handoff, and durable
identity. The next phase can add electronic-structure analysis without redefining workflow execution
or persistence semantics.

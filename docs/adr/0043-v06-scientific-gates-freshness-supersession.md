# ADR-043: v0.6 Scientific Gates, Freshness, and Supersession

- Status: Accepted
- Date: 2026-09-04

## Context

ADR-039 introduced append-only `WorkflowStepBinding` generations. ADR-040 defined the canonical
workflow DAGs and the logical `accepted_structure` edge. ADR-041 made planning deterministic, and
ADR-042 created exact `Calculation`/binding identities while requiring downstream structure binding
to pass the existing v0.5 explicit promotion boundary.

The workflow layer can now materialize steps, but it still lacks a pure answer to two different
questions:

1. which binding generation is current for each logical step; and
2. whether the current scientific state is sufficient to open a downstream workflow edge.

Those questions must not be answered from scheduler success, from the newest CONTCAR on disk, or by
falling back to an older successful generation. They also must not duplicate the v0.5
`FreshnessEngine`, whose `FRESH`, `STALE`, `INVALID`, and `SUPERSEDED` semantics are already frozen.

A further distinction is required for workflow currentness. An old immutable relaxed
`StructureSnapshot` may remain scientifically valid and therefore remain `FRESH`, even after a newer
workflow binding generation becomes current. A downstream Calculation bound to that old accepted
snapshot is no longer current for the workflow, even though neither immutable scientific object is
intrinsically invalid. Workflow currentness therefore cannot be represented by mutating the
StructureSnapshot or by changing global freshness propagation semantics.

## Decision

### 1. Block 5 adds a pure derived scientific-gate projection

Block 5 adds workflow-level gate values and pure evaluators. They derive:

- the current binding generation for each logical workflow step;
- historical binding/Calculation generations that are superseded;
- scientific state of the current Calculation;
- readiness of each workflow step;
- scientific gate verdict for each logical workflow edge.

The projection is intentionally not a new persisted workflow lifecycle entity. It does not write
`ProjectStore`, mutate a `Calculation`, mutate a `WorkflowStepBinding`, submit work, recover work, or
materialize a replacement generation.

### 2. The current binding is the unique terminal generation

`resolve_workflow_binding_generations()` considers only bindings belonging to the requested workflow
plan and validates the append-only chain for each step:

- generations begin at 1 and are contiguous;
- generation 1 has no predecessor;
- every later generation explicitly supersedes the immediately previous binding;
- every binding references an existing Calculation whose Project, CalculationType, recipe, and exact
  input StructureSnapshot agree with the workflow step and binding.

The highest contiguous generation is the only current generation. All lower generations are
historical. No gate operation falls back to a lower generation because it happened to converge.

### 3. Reuse the frozen FreshnessEngine

`evaluate_workflow_freshness()` delegates scientific hash drift and invalidity propagation to the
existing `FreshnessEngine`; Block 5 does not alter freshness precedence or dependency-kind behavior.

Calculations attached to historical workflow binding generations are supplied to the existing engine
as explicit `SUPERSEDED` overrides. This records their workflow-generation status without making
`SUPERSEDED` propagate as scientific invalidity. Existing v0.5 semantics remain authoritative:

- `INVALID` is stronger than `STALE`;
- `STALE` propagates only through `SCIENTIFIC` dependencies;
- non-scientific dependency kinds do not invalidate scientific results;
- `SUPERSEDED` remains scientifically valid and non-propagating.

Workflow plans and bindings remain excluded from scientific provenance entities as frozen in
ADR-039.

### 4. Current Calculation status and freshness are both required

A current workflow step is `PASSED` only when its current Calculation is both:

- `CalculationScientificStatus.CONVERGED`; and
- `FreshnessState.FRESH`.

A current Calculation in `DRAFT`, `READY`, `SUBMITTED`, `RUNNING`, or `PARSING` is `IN_PROGRESS`; its
downstream edges wait rather than infer success.

A current Calculation in `BLOCKED`, `COMPLETED_UNCONVERGED`, `FAILED`, or `CANCELLED` closes
scientific downstream progress. Calculation or provenance `STALE`/`INVALID` states also fail closed.

This mapping consumes the centralized v0.5 scientific reconciliation result. Scheduler state is not a
scientific gate input.

### 5. `accepted_structure` opens only from the current promoted structure

For the current canonical recipes, an `accepted_structure` edge opens only when all of the following
are true:

- the upstream step's current binding exists;
- its current Calculation is `CONVERGED` and `FRESH`;
- the caller supplies the exact `AcceptedStructureSource` for that current binding;
- the source still references the same current upstream Calculation;
- the v0.5 promotion contract has already established a converged, explicitly promoted relaxed
  snapshot;
- that promoted StructureSnapshot is itself `FRESH` under the existing scientific provenance graph.

A converged Calculation without explicit structure promotion leaves `accepted_structure` waiting.
An unpromoted/reconstructed CONTCAR never opens the edge. A stale, invalid, or superseded promoted
snapshot closes the edge.

### 6. Workflow currentness is separate from intrinsic StructureSnapshot freshness

If an already-materialized downstream binding consumes an accepted snapshot from an older upstream
binding generation, and a newer upstream generation is now current with a different accepted
snapshot, the downstream step is derived as workflow `STALE`.

This decision does not mutate the old snapshot or claim that its scientific content became invalid.
It means only that the downstream Calculation no longer represents the current logical workflow
lineage.

The currentness test uses exact UUID equality:

`downstream_binding.resolved_input_structure_snapshot_id == current_upstream_accepted_snapshot.id`.

There is no geometry matching, label matching, latest-file lookup, or `StructureVariant` current
pointer inference inside the gate evaluator.

If currentness cannot be proven because the accepted-structure evidence for the current upstream
binding is absent, a materialized downstream step fails closed rather than being treated as current.

### 7. Readiness does not perform recovery or execution

Derived readiness has four values:

- `READY`: the step has no current binding and all required scientific gates are open;
- `WAITING`: an upstream scientific result or promotion is still in progress/not yet available;
- `BLOCKED`: a current binding or upstream gate requires an explicit later policy decision;
- `SATISFIED`: the current binding generation is scientifically passed and current.

`BLOCKED` deliberately does not choose retry, restart, continuation, or rematerialization. Those
policy decisions belong to Block 6. `READY` deliberately does not create an ExecutionPlan or submit a
job; reconciler/execution handoff belongs to Block 7.

### 8. No new schema or durable idempotency contract

Block 5 adds no persisted entity or field and therefore keeps Project schema v3. It does not define a
pre-materialization reuse key, reopen/resume behavior, or exactly-once orchestration semantics. Those
remain Block 8 scope.

The development version remains `0.6.0.dev0`.

## External implementation reference

The design remains consistent with the architectural separation used by mature workflow systems such
as jobflow/atomate2: dependency satisfaction is evaluated from explicit upstream outputs rather than
scheduler completion alone. ECatVASP keeps its own smaller immutable scientific domain and adds the
stricter requirement that a relaxation structure pass its existing v0.5 convergence and promotion
contracts before it can open a scientific workflow edge.

No external workflow code is copied and no new runtime dependency is introduced.

## Reserved later scope

- Block 6: retry, restart, recovery, and continuation policy using these gate results;
- Block 7: orchestration reconciliation and execution handoff;
- Block 8: durable persistence/reopen/resume/idempotency;
- Block 9: end-to-end workflow acceptance and hardening.

## Explicit non-scope

Block 5 does not add:

- automatic `Calculation` or binding materialization/rematerialization;
- automatic CONTCAR reconstruction or promotion;
- mutation of the frozen `FreshnessEngine`;
- scheduler-state-to-scientific-success inference;
- `ExecutionPlan`, `ExecutionAttempt`, `RemoteJob`, scheduler dependency, or job submission creation;
- retry, restart, recovery, continuation, polling loop, daemon, or reconciler behavior;
- durable workflow lifecycle rows, resume cursors, leases, or idempotency keys;
- DOS/PDOS interpretation, Bader, charge-density difference, COHP, band-center, or LOBSTER analysis;
- ZPE, entropy, thermal corrections, reference-energy aggregation, CHE, reaction free energies,
  potential correction, or pH correction;
- schema v4, tag, GitHub Release, or PyPI publication.

## Consequences

ECatVASP can now distinguish intrinsic scientific freshness from workflow-generation currentness and
can make deterministic, fail-closed decisions about whether a downstream scientific edge is open.
Block 6 can build recovery/continuation policy on top of these pure decisions without redefining
convergence, promotion, provenance, or scheduler semantics.

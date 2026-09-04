# ADR-041: v0.6 Deterministic Workflow Planning

- Status: Accepted
- Date: 2026-09-04

## Context

ADR-039 introduced persisted workflow-plan identity and deliberately made `ScientificWorkflowPlan.plan_hash`
independent from the plan UUID. ADR-040 added the source-defined canonical workflow recipe registry.
Block 3 must now provide one pure planning boundary that turns a canonical workflow recipe plus exact
workflow inputs into reproducible DAG intent without starting step materialization or execution.

Planning must remain distinct from later persistence/reopen idempotency. Repeating the same planning
request may create a different UUIDv7 object identity, but it must produce the same scientific plan
identity and the same deterministic traversal order.

## Decision

### 1. Block 3 planner is pure and side-effect free

`plan_scientific_workflow()` accepts only:

- one exact `ProjectId`;
- one full `WorkflowRecipeIdentity` including version;
- one exact immutable root `StructureSnapshotId`;
- an optional opaque content-addressed `parameters_hash`.

It returns `WorkflowPlanningResult`, containing:

- the canonical immutable `ScientificWorkflowPlan`;
- the exact canonical workflow-recipe `definition_hash`;
- deterministic topological step keys;
- a deterministic `planning_hash`.

The planner does not read or write `ProjectStore`, inspect a current structure pointer, create a
`Calculation`, create a `WorkflowStepBinding`, build VASP inputs, create an `ExecutionPlan`, submit a
job, parse results, or promote a structure.

### 2. Storage UUID and scientific planning identity remain separate

`ScientificWorkflowPlan.id` remains the existing UUIDv7 persistence identity. It is intentionally not
derived from scientific content and therefore may differ across repeated planner calls.

`ScientificWorkflowPlan.plan_hash` remains the deterministic scientific workflow-intent identity from
ADR-039. Identical project identity, workflow recipe identity, exact root snapshot identity, canonical
steps/edges, and parameters hash produce the same `plan_hash` regardless of the generated plan UUID.

Block 3 does not replace UUIDv7 entity identity with UUID5/content-derived entity IDs.

### 3. `planning_hash` binds the planner contract and canonical recipe definition

`WorkflowPlanningResult.planning_hash` is the SHA-256 canonical identity of:

- `WORKFLOW_PLANNER_CONTRACT_VERSION`;
- `ScientificWorkflowPlan.plan_hash`;
- the canonical `WorkflowRecipeSpec.definition_hash`;
- deterministic topological step order.

Including the recipe definition hash additionally binds the plan to the versions of the referenced
canonical VASP recipes captured by ADR-040.

`planning_hash` is an audit identity for pure planning. It is not the persisted-plan reuse key,
Calculation reuse key, WorkflowStepBinding reuse key, or execution idempotency key. Persistence,
reopen/resume, and durable idempotency remain Block 8 scope.

### 4. Parameters remain opaque content-addressed workflow input

Block 3 does not invent a second workflow-parameter schema. An optional `parameters_hash` is carried
into the existing `ScientificWorkflowPlan` contract and participates in `plan_hash`.

The planner canonicalizes a supplied SHA-256 digest to lowercase before plan construction so
hexadecimal case cannot create distinct planning identities for the same digest. The planner does not
interpret the content behind that hash and does not synthesize VASP Recipe parameters such as POTIM,
NFREE, NEDOS, or NBANDS.

### 5. Root structure identity is exact, not geometric

The planner binds the exact `StructureSnapshotId` supplied by the caller. A different snapshot UUID
creates a different planning identity even if two structures are geometrically similar or bytewise
identical after external reconstruction.

There is no geometric matching, current-snapshot lookup, CONTCAR following, or implicit promotion in
Block 3.

### 6. Topological ordering is deterministic but carries no scheduler semantics

Block 3 computes a deterministic topological order from the canonical recipe DAG. When multiple steps
are simultaneously ready, lexical step-key ordering is used as the stable tie-breaker.

This order is a planning/audit order only. It does not serialize sibling branches, imply Slurm
`afterok`, define execution priority, or convert logical workflow edges into scheduler dependencies.
The scientific dependency graph and scheduler DAG remain separate.

### 7. Canonical recipe validation remains fail closed

Planning first resolves the full `WorkflowRecipeIdentity` through the Block 2 registry. Unknown IDs or
versions fail closed. The generated plan is then revalidated against the canonical recipe before a
planning result is returned.

A `WorkflowPlanningResult` also rejects a mismatched recipe definition hash or non-deterministic step
order.

### 8. No schema or development-version change

Block 3 adds no persisted entity type and no persisted field. Project schema remains v3 and the
development version remains `0.6.0.dev0`.

## External implementation reference

The planning boundary was cross-checked against `materialsproject/jobflow` (three-clause BSD-style
license), where a `Flow` composes Jobs into a graph separately from the local execution manager.
ECatVASP borrows only the architectural separation between graph composition and execution.

No jobflow code is copied and no jobflow runtime dependency is added. ECatVASP retains its own
canonical workflow recipes, scientific identities, persistence, VASP contracts, and execution model.

## Reserved later scope

### Block 4 — Step Materialization + Structure Binding

Block 4 may resolve a planned step to an exact immutable input `StructureSnapshot`, create/reuse the
appropriate `Calculation`, and create the corresponding `WorkflowStepBinding` generation under the
already-frozen boundary. None of that occurs in Block 3.

### Block 8 — Persistence / Reopen / Resume / Idempotency

Block 8 remains responsible for durable workflow reuse/resume semantics across `ProjectStore` reopen,
including persisted-plan reuse decisions and durable idempotency behavior. `planning_hash` alone does
not perform those operations.

## Explicit non-scope

Block 3 does not add:

- Calculation or WorkflowStepBinding materialization;
- MethodFingerprint creation or VASP input preparation;
- convergence, freshness, or promotion gate evaluation;
- retry, correction, recovery, or continuation policy;
- reconciler, polling loop, daemon, or scheduler submission;
- scheduler dependencies or batch dispatch;
- persistence/reopen/resume behavior;
- DOS/PDOS, Bader, charge-density difference, COHP, or band-center interpretation;
- ZPE, entropy, thermal corrections, reference energies, CHE, reaction free energies, potential, or
  pH corrections;
- schema v4, tag, GitHub Release, or PyPI publication.

## Consequences

ECatVASP now has a reproducible, audit-ready planning layer between canonical workflow recipes and
future step materialization. Block 4 can consume this deterministic plan without changing workflow
recipe semantics or conflating planning identity with execution identity.

# ADR-039: v0.6 Scientific Workflow Domain Contracts and Schema v3

- Status: Accepted
- Date: 2026-09-04

## Context

v0.5 established a complete managed-result handoff from exact execution artifacts to normalized VASP
scientific results, recipe-aware convergence, explicit structure promotion, durable provenance, and
freshness evaluation. A caller still invokes those layers explicitly. v0.6 must add scientific
workflow orchestration without collapsing the boundaries already frozen between `Calculation`,
`ExecutionAttempt`, `RemoteJob`, scientific parsing, convergence, and structure promotion.

A workflow such as relax -> static/frequency/DOS prerequisite/charge prerequisite/LOBSTER
prerequisite is a many-to-many DAG. It cannot be represented safely by adding a single parent or
child pointer to `Calculation`, nor can scheduler dependencies be reused as scientific dependencies.
The workflow layer also must survive ProjectStore reopen without depending on an in-memory Python
coroutine, daemon, or scheduler process.

## Decision

### 1. Workflow is a new orchestration identity above Calculation

`ScientificWorkflowPlan` is the persisted immutable workflow intent. It owns:

- one Project identity;
- one `WorkflowRecipeIdentity`, deliberately distinct from a VASP `RecipeIdentity`;
- one exact immutable root `StructureSnapshot`;
- canonical `WorkflowStepSpec` values;
- canonical logical `WorkflowEdgeSpec` values;
- optional content-addressed workflow parameters.

A workflow step specifies the `CalculationType` and Calculation recipe identity that a later planner
may materialize. It does not contain INCAR/KPOINTS/POTCAR settings and cannot override the existing
VASP recipe contract.

`ScientificWorkflowPlan.plan_hash` excludes the plan UUID and is deterministic for the same canonical
scientific workflow intent. Step and edge ordering are normalized so tuple insertion order does not
create different workflow identities.

### 2. Workflow logical edges are neither execution nor scientific-provenance edges

`WorkflowEdgeSpec` records only logical workflow ordering. It does not mean:

- scheduler `afterok` or another execution dependency;
- a `DependencyRecord(kind=SCIENTIFIC)`;
- successful execution;
- scientific convergence;
- permission to consume an unpromoted CONTCAR.

Those meanings remain separate and will be connected explicitly by later v0.6 blocks.

### 3. Persisted workflow plans must already be valid DAGs

A plan fails closed when:

- it has no steps;
- step keys are duplicated;
- an edge points outside the plan;
- edge semantics are duplicated;
- an edge is self-referential;
- the directed step graph contains a cycle.

This is a storage safety invariant, not an execution policy. Canonical workflow recipe registration and
higher-level recipe validation remain Block 2 scope.

### 4. WorkflowStepBinding preserves historical materialization generations

`WorkflowStepBinding` binds one logical `(workflow plan, step key, generation)` to exactly one
`Calculation` and one resolved immutable input `StructureSnapshot`.

Generation 1 has no predecessor. Generation >1 must explicitly identify the binding it supersedes.
The ProjectBundle validates that a supersession remains on the same plan/step and increments the
generation contiguously. Historical bindings and Calculations are retained rather than mutated.

`binding_hash` is deterministic over the resolved orchestration identity. Human-readable
`materialization_reason` is audit context and intentionally does not alter the scientific binding
hash.

This contract establishes the durable identity required for later idempotent reconciliation; Block 1
does not yet create, resume, or supersede bindings automatically.

### 5. ProjectBundle validates the workflow-to-Calculation boundary

A persisted binding is valid only when:

- its workflow plan exists in the same Project;
- its step exists in that plan;
- its Calculation exists;
- its resolved `StructureSnapshot` exists;
- the Calculation input snapshot exactly equals the binding's resolved snapshot;
- the CalculationType exactly equals the workflow step CalculationType;
- the Calculation recipe ID exactly equals the workflow step recipe ID;
- the supersession chain is present, same-step, contiguous, and non-forking.

There is no geometric structure matching or implicit current-snapshot lookup.

### 6. Workflow orchestration entities are not scientific-result producers

Block 1 persists `ScientificWorkflowPlan` and `WorkflowStepBinding` in `ProjectBundle`, but deliberately
excludes them from `ProjectBundle.provenance_entities()`.

A workflow plan or binding therefore cannot masquerade as the producer/subject of a scientific
`ProvenanceRecord` or `DependencyRecord`. Existing scientific provenance continues to terminate on
real scientific entities such as `StructureSnapshot`, `MethodFingerprint`, `Calculation`, `Artifact`,
and `Analysis`. Later orchestration blocks may create those existing provenance edges while keeping
the workflow layer logical.

### 7. Project schema advances additively from v2 to v3

`SCHEMA_VERSION` advances to 3 because two new permanent top-level entity types are persisted:

- `ScientificWorkflowPlan`;
- `WorkflowStepBinding`.

The built-in v2 -> v3 migration updates only the persisted `Project.schema_version` and database
schema marker. Existing v2 scientific/entity rows are not rewritten. Existing v1 projects migrate
consecutively through v1 -> v2 -> v3.

The human-readable `project.yaml` JSON schema advances to v3 accordingly.

### 8. v0.6 development identity advances to 0.6.0.dev0

Block 1 starts the v0.6 development line at `0.6.0.dev0`. This is a development-version change only;
it does not authorize a tag, GitHub Release, or PyPI publication.

## External implementation references

The boundary was cross-checked against established workflow projects without copying code or adding a
runtime dependency:

- `materialsproject/jobflow` (BSD 3-Clause-style license): its `Flow` keeps workflow composition and
  output references distinct from individual `Job` execution. ECatVASP borrows only the architectural
  lesson that workflow identity should sit above task identity; it does not adopt jobflow's MSON,
  networkx, manager, dynamic replacement, or database abstractions.
- `aiidateam/aiida-core` (MIT License): AiiDA distinguishes logical workflow/process provenance from
  calculation/data provenance. ECatVASP borrows only the separation principle; it does not adopt the
  AiiDA engine, daemon, node database, process state machine, or link model.

## Non-scope

Block 1 does not add:

- predefined workflow recipe registry or product workflow catalog;
- automatic plan creation or step materialization;
- reconciler, daemon, polling loop, or scheduler chaining;
- retry/recovery/continuation automation;
- downstream convergence/freshness policy implementation;
- automatic structure promotion or current-snapshot following;
- DOS/PDOS interpretation, Bader, charge-density difference, COHP, band center, or LOBSTER analysis;
- ZPE, entropy, thermal corrections, reference energies, CHE, reaction free energies, potential, or
  pH corrections;
- GUI or a new scheduler backend;
- tag, GitHub Release, or PyPI publication.

## Consequences

v0.6 now has a small durable vocabulary for multi-Calculation scientific workflows while preserving
all v0.5 scientific-result and v0.4 execution identities. Later blocks can add recipe registration,
planning, scientific gates, recovery integration, reconciliation, and resumability without changing
what a `Calculation` or `ExecutionAttempt` means.

# ADR-040: v0.6 Canonical Workflow Recipe Registry

- Status: Accepted
- Date: 2026-09-04

## Context

ADR-039 introduced durable workflow identities but deliberately left canonical recipe registration
and higher-level recipe validation to Block 2. A persisted `ScientificWorkflowPlan` can therefore be
a valid DAG while still describing a graph that ECatVASP does not recognize as a supported product
workflow.

The existing VASP layer already has a source-defined immutable registry of calculation recipe
contracts. Workflow composition must consume that registry rather than duplicate VASP input semantics
or introduce a second route for deciding `CalculationType`, INCAR controls, frequency displacement
parameters, DOS grids, LOBSTER band counts, or system compatibility.

## Decision

### 1. Canonical workflow recipes are source-defined immutable contracts

Block 2 adds `WorkflowRecipeSpec` and an immutable `WORKFLOW_RECIPE_REGISTRY`. Registry keys are full
`WorkflowRecipeIdentity` values, including version. Unknown recipe IDs or versions fail closed.

The initial product catalog contains three recipes:

- `ECatVASP.Workflow.SlabScientificPreparation`;
- `ECatVASP.Workflow.AdsorbateScientificPreparation`;
- `ECatVASP.Workflow.GasReferencePreparation`.

This is intentionally a small product catalog, not a general-purpose user-authored workflow language.

### 2. Workflow recipes compose existing VASP recipe identities only

Every `WorkflowStepSpec` in a canonical recipe must resolve through the existing VASP recipe registry.
Its `CalculationType` must exactly match the canonical `VaspRecipeSpec.calculation_type`.

The workflow layer does not define or override:

- `MethodDefinition`;
- `ProtocolDefinition`;
- concrete `RecipeIdentity.parameters`;
- `MethodFingerprint`;
- finite-difference `POTIM` or `NFREE`;
- selected-atom frequency UID digests;
- DOS `NEDOS`;
- LOBSTER `NBANDS`;
- numerical-lock or system-context validation.

Those remain owned by the existing VASP scientific-input contracts and later materialization logic.

`WorkflowRecipeSpec.definition_hash` includes the canonical referenced VASP recipe identities and their
versions. A future semantic change to a referenced VASP recipe must therefore be accompanied by an
explicit workflow-recipe version decision rather than silently changing the catalog meaning.

### 3. Recipe graphs are canonical DAGs

Recipe construction fails closed on:

- blank recipe identity;
- empty step set;
- duplicate step keys;
- duplicate edge semantics;
- edges outside the same recipe;
- cycles;
- unknown VASP recipe IDs;
- `CalculationType` mismatches against the VASP registry.

Steps and edges are canonicalized before they become registry contracts.

### 4. Accepted structure fan-out is logical orchestration, not automatic promotion

The initial recipes use the logical edge role `accepted_structure`.

For slab and adsorbate workflows, relaxation is followed by a fan-out from the accepted relaxed
structure. Ground-state static, frequency where applicable, DOS prerequisite, charge-density static,
and LOBSTER prerequisite are siblings unless a later architecture decision adds another scientifically
necessary gate.

In particular, adsorbate frequency is not encoded as a prerequisite for DOS, charge-density, or
LOBSTER work. Display order is not scientific dependency.

The `accepted_structure` edge does not itself mean that a CONTCAR has been accepted or promoted. It
only declares the recipe's logical input relationship. Later v0.6 blocks must use the existing v0.5
convergence and explicit structure-promotion contracts before resolving such an edge to a concrete
`StructureSnapshot`.

### 5. Canonical plan validation is exact and side-effect free

`validate_workflow_plan_recipe_contract()` resolves the plan's full workflow recipe identity and
requires the plan's canonical steps and edges to match the source-defined recipe exactly.

`ScientificWorkflowPlan.parameters_hash` remains opaque to Block 2. It may distinguish plan-level
scientific inputs, but Block 2 neither interprets nor generates those inputs.

Validation does not create a `Calculation`, `ExecutionPlan`, `ExecutionAttempt`, scheduler job,
artifact, analysis, or promoted structure.

### 6. Block 2 does not require another project schema version

No new persisted entity type or persisted field is introduced. The canonical registry is application
code, while `ScientificWorkflowPlan` and `WorkflowStepBinding` continue to persist under schema v3.
The development version remains `0.6.0.dev0`.

## External implementation reference

The design was cross-checked against `materialsproject/atomate2`, whose VASP flow makers compose named,
source-defined calculation makers and pass outputs between jobs. Its repository uses a three-clause
BSD-style license. ECatVASP borrows only the architectural lesson that supported scientific flows
should be explicit source-defined compositions above individual calculations.

No atomate2 or jobflow code is copied, and neither project is added as a runtime dependency. ECatVASP
retains its own lightweight domain, VASP recipe, persistence, provenance, and execution boundaries.

## Non-scope

Block 2 does not add:

- automatic workflow-plan creation;
- step materialization or `Calculation` creation;
- pre-materialization idempotency keys;
- convergence/freshness gate evaluation;
- automatic CONTCAR promotion or current-snapshot following;
- reconciler, polling loop, daemon, or scheduler chaining;
- recovery/retry/continuation automation;
- DOS/PDOS interpretation, Bader, charge difference, COHP, or band-center analysis;
- ZPE, entropy, thermal corrections, reference-energy aggregation, CHE, reaction free energies,
  potential correction, or pH correction;
- user-loaded Python callables, dynamic import paths, or plugin execution from serialized recipe IDs;
- schema v4, tag, GitHub Release, or PyPI publication.

## Consequences

ECatVASP now has a stable product-level workflow vocabulary that can be validated independently from
runtime execution. Block 3 can build deterministic planning and materialization against these recipes
without inventing graph semantics or VASP calculation identities on the fly.

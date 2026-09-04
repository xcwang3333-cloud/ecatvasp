# ADR-042: v0.6 Step Materialization and Structure Binding

- Status: Accepted
- Date: 2026-09-04

## Context

ADR-039 introduced durable workflow-step binding generations. ADR-040 defined canonical workflow
recipes and the logical `accepted_structure` edge. ADR-041 added deterministic workflow planning while
explicitly reserving Calculation and binding creation for Block 4.

A workflow step must now be converted into one concrete `Calculation` without weakening the v0.5
scientific-result boundary. In particular, a downstream workflow edge must not be interpreted as
permission to read the newest CONTCAR, follow a `StructureVariant.current_structure_snapshot_id`, or
silently treat a merely reconstructed relaxation candidate as accepted.

## Decision

### 1. Materialization is an explicit pure boundary above Calculation

Block 4 adds `materialize_workflow_step()`. It creates in memory:

- one new `Calculation`;
- one new `WorkflowStepBinding` generation;
- one `WorkflowStepMaterialization` result carrying the exact resolved input snapshot.

The materializer does not persist `ProjectStore`, create VASP input files, create an `ExecutionPlan`,
create an `ExecutionAttempt`, submit a scheduler job, parse results, or promote a structure.

Every invocation is an explicit creation request. Block 4 never searches for, reuses, or resumes an
existing Calculation or binding automatically.

### 2. Root-step input is the exact plan root snapshot

A workflow step with no incoming logical edge can be materialized only from an explicitly supplied
`StructureSnapshot` whose UUID exactly equals `ScientificWorkflowPlan.root_structure_snapshot_id`.

There is no geometric matching, label matching, parent-lineage inference, or current-snapshot lookup.
A root step cannot consume an `accepted_structure` source.

### 3. `accepted_structure` consumes an explicit v0.5 promotion result

For the initial canonical product workflows, a downstream step has exactly one incoming
`accepted_structure` edge. Block 4 resolves that edge only from an explicit `AcceptedStructureSource`
containing:

- the exact upstream `WorkflowStepBinding`;
- the exact upstream relaxation `Calculation`;
- a v0.5 `VaspStructurePromotionResult`.

The source fails closed unless:

- the binding references the supplied upstream Calculation and exact Calculation input snapshot;
- the upstream Calculation is a relaxation task;
- the promotion convergence type matches the upstream Calculation type;
- the promotion verdict is scientifically `CONVERGED`;
- the promoted snapshot has `StructureOrigin.RELAXED`;
- the promoted snapshot directly descends from the upstream Calculation input snapshot;
- the returned promoted `StructureVariant` points at that exact promoted snapshot;
- the upstream binding belongs to the same workflow plan and required upstream step;
- the upstream Calculation Project, CalculationType, and recipe ID match that workflow step.

Therefore the logical edge never becomes a shortcut around v0.5 convergence and explicit promotion.
A reconstructed but unpromoted CONTCAR is insufficient.

### 4. Materialization consumes an already-defined MethodFingerprint

The caller supplies the exact `MethodFingerprint` for the step. Block 4 requires:

- fingerprint recipe ID equals the workflow step recipe ID;
- fingerprint recipe version equals the canonical VASP recipe version;
- the step CalculationType still matches the canonical VASP recipe contract.

The workflow layer does not construct or reinterpret `MethodDefinition`, `ProtocolDefinition`,
`RecipeIdentity.parameters`, selected-atom frequency parameters, POTIM, NFREE, NEDOS, NBANDS, POTCAR
identity, or other VASP numerical semantics.

After constructing the Calculation identity, Block 4 delegates task/system/lock validation to the
existing `validate_calculation_recipe_contract()` using the caller's explicit `VaspSystemContext` and
`ProjectNumericalLock`. System kind is never inferred from geometry or atom types.

Deeper fingerprint/lock/input consistency remains owned by the existing VASP input-preparation
pipelines rather than being reimplemented in workflow code.

### 5. Binding generations are explicit and append-only

With no `previous_binding`, materialization creates generation 1 with no predecessor.

When the caller explicitly supplies `previous_binding`, it must belong to the same workflow plan and
step. The new binding uses `generation = previous.generation + 1` and identifies the previous binding
through `supersedes_binding_id`.

The operation always creates a new Calculation and a new binding. It does not mutate or replace the
previous Calculation/binding and does not decide whether rematerialization was necessary.

### 6. Block 4 does not define durable idempotency

`WorkflowStepBinding.binding_hash` remains a post-materialization audit identity because it includes
the created Calculation UUID. Block 4 does not introduce a pre-materialization reuse key and does not
use ADR-041 `planning_hash` as a Calculation-reuse key.

Durable lookup, reopen, resume, reuse, and idempotency decisions remain Block 8 scope.

### 7. Only currently canonical downstream edge semantics are materialized

Block 4 supports the current product catalog's single incoming `accepted_structure` edge. A downstream
step with multiple incoming edges or another edge role fails closed rather than guessing merge/input
semantics.

A future recipe requiring multiple scientific inputs must receive an explicit architecture decision
before materialization behavior is added.

### 8. Existing ProjectBundle integrity remains authoritative

Block 4 adds no new persisted entity or field. When the returned Calculation and binding are added to a
`ProjectBundle`, the existing schema-v3 integrity checks remain authoritative for:

- workflow plan / step existence;
- Calculation and StructureSnapshot existence;
- exact resolved-snapshot equality;
- CalculationType and recipe equality;
- unique `(plan, step, generation)` identity;
- contiguous, non-forking supersession chains.

Schema therefore remains v3 and the development version remains `0.6.0.dev0`.

## External implementation reference

The boundary was cross-checked against `materialsproject/atomate2` (three-clause BSD-style license).
Its VASP flows explicitly pass a relaxation job's structure output into downstream makers instead of
rediscovering an ambient current structure. ECatVASP borrows only this architectural lesson of
explicit upstream-output to downstream-input binding.

ECatVASP intentionally adds a stricter scientific gate: downstream workflow materialization consumes
only its own v0.5 explicit structure-promotion result, not an arbitrary relaxation output. No atomate2
code is copied and no atomate2/jobflow runtime dependency is added.

## Reserved later scope

Later v0.6 blocks may evaluate downstream readiness, integrate recovery/continuation policies, and
reconcile workflow state. Block 8 remains responsible for durable persistence/reopen/resume/idempotency
semantics.

## Explicit non-scope

Block 4 does not add:

- automatic ProjectStore writes or persisted-plan lookup;
- pre-materialization Calculation/binding reuse or idempotency keys;
- automatic convergence evaluation;
- automatic CONTCAR reconstruction or structure promotion;
- current-StructureVariant following;
- VASP INCAR/KPOINTS/POTCAR materialization;
- `ExecutionPlan`, `ExecutionAttempt`, `RemoteJob`, scheduler dependency, or submission creation;
- retry, restart, recovery, continuation, reconciler, polling loop, or daemon behavior;
- Bader, charge-density difference, DOS/PDOS, COHP, band-center, or LOBSTER interpretation;
- ZPE, entropy, thermal corrections, reference-energy aggregation, CHE, reaction free energies,
  potential correction, or pH correction;
- schema v4, tag, GitHub Release, or PyPI publication.

## Consequences

ECatVASP now has an explicit bridge from deterministic workflow intent to concrete Calculation and
WorkflowStepBinding identities while preserving the v0.5 accepted-structure gate. The next workflow
blocks can reason about step readiness and scientific state without guessing which structure a
Calculation consumed or conflating logical DAG edges with execution dependencies.

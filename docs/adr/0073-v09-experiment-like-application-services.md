# ADR-073: v0.9 Experiment-like Application Services

- Status: Accepted
- Date: 2026-09-07

## Context

v0.9 Blocks 1 through 5 established deterministic workspace, scientific inventory,
workflow-readiness, presentation, and reporting contracts over the existing ECatVASP scientific
core. Those contracts make project state inspectable, but a client still has to know a large number
of lower-level modules to perform common research-workbench operations safely.

The next boundary is therefore an application service layer for operations that a scientist thinks
of as "prepare", "run", "inspect", "promote", "analyze", and "report". The layer must improve
operability without becoming a second scientific engine, a second workflow state machine, or a
session/task-board persistence model.

Several lower-level authorities already exist and must remain authoritative:

- `ProjectStore` is the durable metadata and integrity authority;
- workflow planning, materialization, generation resolution, gates, orchestration, and durable
  dispatch are owned by the v0.6 workflow modules;
- execution-attempt allocation is distinct from scheduler side effects;
- VASP result parsing, convergence classification, CONTCAR reconstruction, structure promotion, and
  scientific-result materialization are distinct v0.5 operations;
- provenance/freshness is owned by the existing provenance graph and `FreshnessEngine`;
- workspace and reporting outputs are transient projections.

## Decision

### 1. Add one project-scoped application service over `ProjectStore`

`ProjectApplicationService` is bound to one `ProjectStore`. Every public operation reopens the store
at call time and derives its work from the current validated `ProjectBundle`.

The service must not cache a mutable `ProjectBundle` between calls. A client that keeps an old
application result cannot make it authoritative over newer durable project state.

### 2. `inspect` is a projection operation

`inspect()` returns the existing deterministic `WorkspaceProjection` and
`WorkspaceScientificInventory`.

Caller-supplied observed scientific hashes and explicit invalid/superseded ids are forwarded to the
existing inventory/freshness authority. The service does not create a second freshness evaluator.

### 3. `prepare` composes canonical workflow planning and durable materialization

`prepare_workflow()` calls `plan_scientific_workflow()` using the currently reopened Project id and
an exact persisted root `StructureSnapshot`, then calls `persist_or_reuse_workflow_plan()`.

`prepare_workflow_step()` resolves the currently persisted plan and `MethodFingerprint` and delegates
to `persist_or_reuse_workflow_materialization()`.

For a root workflow step, the service may resolve the exact
`ScientificWorkflowPlan.root_structure_snapshot_id` automatically because that identity is already
canonical plan state. For a downstream step, an explicit `AcceptedStructureSource` is still required;
the application layer must not infer an accepted structure from filenames, geometry, a
`StructureVariant.current_structure_snapshot_id`, or a historical workflow generation.

### 4. `run` means durable dispatch handoff, not scheduler success

`run_workflow()` delegates to `persist_workflow_dispatch_wave()`. New `ExecutionAttempt` records are
therefore persisted before the method exposes a scheduler dispatch wave.

The application service does not submit a scheduler job, fabricate a `RemoteJob`, or mark a
Calculation scientifically successful. External scheduler submission remains an execution-layer
side effect downstream of the returned dispatch wave.

### 5. `analyze` uses the existing managed VASP scientific-result materializer

Block 6 exposes one representative high-level scientific analysis operation:
`analyze_vasp_result()`.

It accepts an exact persisted `Calculation`, an already constructed VASP result intake, an already
normalized `VaspResultDocument`, and an already determined `VaspConvergenceAssessment`. It delegates
to `materialize_vasp_scientific_result()` and atomically persists exactly the returned
Calculation/Analysis/Artifact/provenance/dependency graph in `ProjectStore`.

The application service does **not** parse VASP files or classify convergence. For a result carrying
UID-bound forces, magnetization, or frequency eigenvectors, an exact `ExecutionPlan` is required and
the existing `bind_vasp_atom_identity_result_provenance()` contract is applied before persistence.
No atom identity is inferred geometrically.

This representative operation does not create a generic cross-analysis mutation protocol. Existing
DOS/PDOS, Bader, charge-difference, COHP, descriptor, thermochemistry, and reaction materializers
retain their own scientific contracts and can be surfaced incrementally through the same service
pattern without erasing their type-specific semantics.

### 6. `promote` preserves reconstruction/promotion separation

`promote_vasp_structure()` first delegates to `reconstruct_vasp_contcar_snapshot()` using the exact
managed `ExecutionPlan`, result intake, persisted Calculation, and immutable Calculation input
snapshot. It then builds the existing reconstruction provenance graph and separately calls
`promote_vasp_contcar_snapshot()` with exact `VaspConvergenceEvidence`.

Only after the pure promotion gate succeeds does the service atomically persist:

- the new immutable reconstructed `StructureSnapshot`;
- the updated `StructureVariant.current_structure_snapshot_id`;
- the reconstruction `ProvenanceRecord` and scientific `DependencyRecord` edges.

The pure promotion gate still rejects unconverged/indeterminate evidence and rejects a stale
StructureVariant whose current snapshot moved after the Calculation started. A second replay after a
successful promotion therefore fails closed rather than creating another accepted revision.

### 7. `report` composes the Block 5 reporting authority

`report()` reopens current project state and calls `build_scientific_report()`. It can render one of
the already-defined deterministic JSON, CSV, or Markdown exports.

The report and application result are transient. `report_hash` remains a non-scientific export/cache
identity and never enters scientific provenance, dependencies, workflow gates, or persistence.

### 8. Application results are receipts, not durable state machines

Application result dataclasses may return exact lower-level receipts and transient projections for
clients. They are not added to `ProjectBundle`, are not provenance subjects, and are not later used
as a fallback when current ProjectStore state disagrees.

The service deliberately has no persisted `Session`, `Task`, `Run`, `Experiment`, `WorkspaceState`,
or application command log entity.

### 9. Mutation methods fail closed before or during `ProjectStore.save()`

Every mutation method resolves ids against the current reopened bundle, rejects foreign/missing
objects, rejects entity-id collisions, and lets existing lower-level scientific validation errors
remain authoritative. `ProjectStore.save()` validates the complete resulting graph before replacing
durable metadata.

The layer does not weaken or catch-and-relabel scientific errors into a generic success/failure
status.

### 10. Schema and dependencies remain frozen

Block 6 adds no permanent top-level entity, no schema migration, and no runtime dependency.

- package version remains `0.9.0.dev0`;
- `SCHEMA_VERSION` remains 3.

## Public contract

The Block 6 application layer provides a project-scoped service with these operation families:

- `inspect(...)`;
- `prepare_workflow(...)`;
- `prepare_workflow_step(...)`;
- `run_workflow(...)`;
- `analyze_vasp_result(...)`;
- `promote_vasp_structure(...)`;
- `report(...)`.

Names describe user-level intent; lower-level scientific and execution receipts remain visible in
return values so callers can audit exactly what happened.

## Fail-closed rules

The application layer must reject or propagate rejection when:

- a referenced Project entity is absent from the currently reopened bundle;
- a cached/stale plan, orchestration handoff, generation, or accepted-structure source no longer
  matches current durable workflow history;
- a scheduler dispatch would allocate work outside existing orchestration/durability gates;
- VASP result materialization references foreign/missing raw or staging Artifacts;
- UID-bound scientific results lack the exact staged atom-index provenance;
- CONTCAR reconstruction inputs drift from the exact managed ExecutionPlan/result intake;
- convergence does not authorize promotion;
- a StructureVariant moved since the relaxation Calculation began;
- a returned scientific materialization collides with an already-persisted entity id;
- the final candidate `ProjectBundle` fails graph validation.

## Acceptance contract

Block 6 is accepted only if tests prove that:

1. `inspect()` reopens the latest ProjectStore state and preserves separate scientific, analysis,
   execution-attempt, scheduler, provenance, and freshness information;
2. repeated canonical workflow preparation reuses the exact durable plan identity instead of
   creating duplicate scientific intent;
3. workflow-step preparation consumes current orchestration/generation state and fails closed on a
   stale handoff;
4. `run_workflow()` persists ExecutionAttempts before returning a scheduler wave and replay does not
   duplicate attempts;
5. `analyze_vasp_result()` persists only an already-normalized/already-classified scientific result
   graph and requires atom-index provenance for UID-bound values;
6. `promote_vasp_structure()` keeps reconstruction and convergence-aware promotion separate and an
   unconverged or stale promotion does not advance a StructureVariant;
7. `report()` is deterministic over current project state and retains stale/blocked/provenance
   visibility from Blocks 2 and 5;
8. representative mutations survive `ProjectStore` reopen with the exact durable identities;
9. no application/session state appears in `ProjectBundle`, and `SCHEMA_VERSION` remains 3.

## Non-scope

Block 6 does not add:

- CLI argument parsing or command-line packaging (Block 7);
- frontend/desktop transport contracts (Block 8);
- automatic scheduler submission or daemon control;
- a generic plugin protocol for arbitrary Analysis mutation;
- background task/session persistence;
- high-throughput `Study` entities;
- NEB/barrier, solvation, constant-potential, microkinetic, ML, or other new scientific methods;
- tag, GitHub Release, or PyPI publication.

## Consequences

ECatVASP gains a stable experiment-like application seam that notebooks, a future CLI, and a future
desktop client can share. The seam reduces client orchestration boilerplate while keeping the
scientific source of truth in the existing typed domain, provenance, workflow, VASP, analysis, and
ProjectStore contracts.
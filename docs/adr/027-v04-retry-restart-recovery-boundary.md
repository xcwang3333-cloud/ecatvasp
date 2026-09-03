# ADR-027: v0.4 Retry, Restart, and Recovery Identity Boundary

- Status: Accepted
- Date: 2026-09-03

## Context

v0.4 Blocks 1-7 establish exact ExecutionPlan provenance, local and SSH/Slurm execution,
scheduler monitoring, and output retrieval. Recovery now needs an explicit identity policy so that
transport retries, scheduler resubmissions, execution tuning, scientific input changes, and
structure continuation cannot silently collapse `Calculation`, `ExecutionAttempt`, and `RemoteJob`.

ADR-007 permits custodian-style recovery mechanisms only when every correction is recorded and no
scientific change occurs silently. ADR-019 keeps execution tuning outside `MethodFingerprint` while
pinning it in `ExecutionPlan`. Block 8 therefore classifies recovery before any later workflow or
scheduler orchestration is allowed to act.

## Decision

### Evidence is explicit and fail closed

Recovery classification uses positive evidence about remote side effects and VASP launch state:

- `NO_REMOTE_SIDE_EFFECT_CONFIRMED`: the failed operation is known to have created no remote side
  effect;
- `NO_VASP_LAUNCH_CONFIRMED`: a scheduler-side operation may have happened, but positive evidence
  establishes that VASP did not launch;
- `VASP_LAUNCH_CONFIRMED`: VASP launched;
- `EXECUTION_UNCERTAIN`: launch or side-effect history cannot be established safely.

Absence of a bounded monitoring marker is not automatically promoted to proof that VASP never
launched. When execution history is uncertain, ECatVASP chooses the stricter identity boundary.

### Same ExecutionAttempt reuse

The same immutable `ExecutionAttempt` may be reused only in two cases:

1. retrying a transport/control operation with `NO_REMOTE_SIDE_EFFECT_CONFIRMED`;
2. creating another scheduler `RemoteJob` when `NO_VASP_LAUNCH_CONFIRMED` is positively established.

This preserves the Block 1 cardinality rule that one `ExecutionAttempt` may have multiple
`RemoteJob` records only when no additional VASP launch occurred.

If VASP launch is confirmed or uncertain, recovery requires a new `ExecutionAttempt`, even when
the same `Calculation` and same `ExecutionPlan` are reused.

### Execution-only changes

Changes confined to `ExecutionSettings` or execution-only INCAR keys `NCORE`, `KPAR`, and `NPAR`
preserve `Calculation` and `MethodFingerprint` identity, but require:

- a new `ExecutionPlan` hash;
- a new `ExecutionAttempt` pinned to that new plan.

This includes scheduler resources, memory, walltime, partition, MPI/OpenMP topology, logical VASP
executable selection, and other `ExecutionSettings` values. Block 8 provides a deterministic helper
that derives a new plan by replacing only `execution_settings`; all scientific handoff fields remain
unchanged.

### Scientific restart/input changes

`ISTART` and `ICHARG` are conservatively treated as scientific initialization changes. They require
a new `Calculation`/`MethodFingerprint` rather than an attempt-only mutation.

`ENCUT`, `EDIFF`, `ALGO`, and every other non-execution INCAR change also require a new scientific
`Calculation`/`MethodFingerprint`. Unknown future INCAR tags fail closed at this stricter scientific
boundary instead of being assumed execution-only.

If execution-only and scientific changes are mixed, the scientific boundary wins.

### CONTCAR continuation

Continuing from a prior `CONTCAR` changes the scientific structure input. It therefore requires:

1. a new immutable `StructureSnapshot` imported from the selected continuation geometry;
2. a new `Calculation` referencing that snapshot;
3. a corresponding new `MethodFingerprint` instance because the scientific input digest changes.

Block 8 classifies this boundary but does not perform CONTCAR parsing/import.

### Automatic correction is forbidden

Block 8 does not implement error-string-to-INCAR handlers and does not silently mutate VASP
scientific or execution parameters. Proposed automatic tuning/correction is returned as
`MANUAL_REVIEW_REQUIRED` when it would change inputs/settings.

Transport retries with proven no side effect may be automated by a later execution orchestrator,
because they do not change the plan, attempt identity, or scientific request.

### RecoveryDecision

`RecoveryDecision` is an immutable execution-layer value containing:

- selected recovery action and highest changed identity layer;
- source ExecutionPlan and execution-setting hashes;
- proposed target execution hash where applicable;
- changed `ExecutionSettings` field names and proposed INCAR tags;
- whether scientific identity is preserved;
- whether a new plan, attempt, Calculation, or StructureSnapshot is required;
- deterministic `decision_hash` and a human-readable reason.

It is not a new scientific Domain entity and does not change project schema version 2.

## Reference implementation note

The architecture was cross-checked against Materials Project `custodian`, whose VASP job layer can
apply restart/correction settings around repeated runs. ECatVASP intentionally does not adopt that
behavior as an automatic scientific policy: correction proposals must first cross this explicit
identity classifier, preserving the stronger Calculation/MethodFingerprint boundary defined here.

## Explicit non-scope

Block 8 does not implement:

- automatic VASP error correction;
- automatic INCAR edits;
- CONTCAR parsing or StructureSnapshot creation;
- scheduler batch DAG/concurrency;
- Slurm arrays;
- concrete PBS/LSF adapters;
- scientific OUTCAR/vasprun convergence parsing;
- GUI recovery controls;
- tag, GitHub Release, or PyPI publication.

## Consequences

- uncertain execution history creates a new attempt instead of risking double execution under one
  attempt identity;
- execution resource/tuning changes remain scientifically comparable but are fully visible in plan
  and attempt provenance;
- initialization/protocol/input changes cannot masquerade as scheduler retries;
- continuation geometries become explicit new scientific structure inputs;
- future batch and retry orchestration must consume this classifier rather than inventing its own
  identity rules.

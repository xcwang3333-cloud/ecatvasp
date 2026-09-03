# ADR-029: v0.4 Final Execution Acceptance and Handoff Boundary

- Status: Accepted
- Date: 2026-09-03

## Context

Blocks 1-9 established the individual v0.4 execution contracts: attempt provenance, target and
credential boundaries, local runtime materialization, SSH staging, Slurm submission, scheduler
monitoring, retrieval, recovery classification, and scheduler-only batch orchestration.

The final v0.4 block must prove that these contracts compose without creating a second scientific
workflow engine or weakening the scientific/execution boundary frozen in v0.3 and ADR-020.
Individual unit tests are necessary but not sufficient for this final gate: the handoff must also
reject cross-layer snapshots that are locally valid but globally inconsistent.

## Decision

### 1. Final acceptance is a pure validation layer

`validate_v04_execution_handoff()` reads already-created execution facts and returns an immutable
`ExecutionAcceptanceReport`. It performs no transport command, scheduler action, file movement,
retry, restart, parser invocation, or scientific-state mutation.

The accepted chain is:

`Calculation -> ExecutionPlan -> ExecutionAttempt -> target/runtime -> [RemoteJob] -> artifacts -> [retrieval] -> [scheduler-DAG observation]`

Square-bracketed elements are conditional: local execution has no `RemoteJob`; retrieval and batch
state are supplied only when that stage has actually been reached.

### 2. Acceptance never means scientific convergence

`ExecutionHandoffStage` is execution-only. In particular:

- Slurm `COMPLETED` means that the scheduled process exited;
- `ExecutionAttempt.EXITED` means execution exit, not a scientifically converged VASP result;
- `ExecutionAttempt.RETRIEVING` means retrieval is in progress or recorded;
- scheduler-DAG `COMPLETE` means execution-order dependencies are satisfied;
- OUTCAR markers observed by Block 6 remain bounded telemetry and are not promoted into a
  convergence verdict.

`ExecutionAcceptanceReport.scientific_convergence_assessed` is permanently `False`. Scientific
parsing and convergence remain downstream scientific responsibilities.

### 3. Calculation/plan/attempt provenance remains exact

Final acceptance reuses `validate_execution_attempt_plan()` and therefore requires the supplied
attempt to pin the exact `ExecutionPlan.plan_hash` and input-manifest hash for the same
`Calculation`.

An execution acceptance hash is derived only from execution-handoff facts. It is not a
`MethodFingerprint`, scientific identity, or replacement for the immutable v0.3 input manifest.

### 4. Target and RemoteJob must remain coherent

For LOCAL targets:

- scheduler must remain `None`;
- no `RemoteJob` is permitted.

For the concrete v0.4 remote path:

- transport is SSH;
- scheduler is Slurm;
- submitted and later attempt states require a persisted `RemoteJob`;
- `RemoteJob.execution_attempt_id` must equal the supplied attempt id;
- `RemoteJob.scheduler` must equal the target scheduler;
- the remote directory must be the isolated `execution/<attempt-id>` stage;
- scheduler state and attempt state must be a valid persisted pair.

`UNKNOWN` and `LOST` remain uncertain scheduler truths and cannot be relabelled as terminal
execution success.

### 5. Required execution provenance is stage-dependent

A staged SSH attempt must retain attempt-produced metadata for:

- `EXECUTION_PLAN`;
- runtime `INCAR`;
- `REMOTE_STAGE_MANIFEST`.

A submitted or later SSH attempt must additionally retain:

- `JOB_SCRIPT`;
- `SCHEDULER_RECORD`.

All execution artifacts supplied to the final gate must be produced by the exact
`ExecutionAttempt`. Scientific Calculation artifacts and Analysis artifacts are not accepted as
attempt provenance.

### 6. Retrieval acceptance requires exact expected-output coverage

When a `RemoteRetrievalPackage` is supplied:

- it must refer to the same attempt and RemoteJob;
- its manifest must pin the same plan hash and sanitized target hash;
- its file records must cover exactly the `ExecutionPlan.expected_outputs` roles, artifact types,
  retrieval policies, and required flags;
- all retrieval artifacts must be attempt-produced.

This does not force `ON_DEMAND` or `REMOTE_ONLY` artifacts to be downloaded. The Block 7 lifecycle
policy remains authoritative.

### 7. Batch acceptance is observational only

When a Block 9 `BatchDispatchSnapshot` is supplied, final acceptance checks that the named node
references the exact latest attempt and plan and that its scheduler-DAG state is compatible with
the execution attempt.

The acceptance layer cannot create dependencies, allocate attempts, authorize recovery, or submit
work. Recovery authorization remains exclusively governed by ADR-027 and Block 8.

### 8. Licensed POTCAR boundary remains unchanged

The final acceptance path must be demonstrably executable without persisting or redistributing a
POTCAR body. Remote staging resolves the licensed body on the execution host and records only its
verified identity/provenance. The E2E acceptance test explicitly asserts that no local attempt
artifact named `POTCAR` is created.

## Final E2E acceptance path

The v0.4 acceptance test uses one deterministic in-memory SSH/Slurm transport and executes the
public contracts in order:

1. create a provenance-pinned `ExecutionAttempt`;
2. stage immutable scientific inputs plus runtime-only INCAR overlay;
3. resolve and verify a licensed POTCAR only on the simulated remote host;
4. resolve resources, render and verify the Slurm script, then submit;
5. reconcile a Slurm `COMPLETED` observation and bounded VASP telemetry;
6. retrieve required outputs while leaving an `ON_DEMAND` WAVECAR remote;
7. reconstruct the scheduler-only batch DAG from persisted facts;
8. run final execution-handoff acceptance;
9. prove that the source scientific INCAR is unchanged, POTCAR was not persisted locally, and the
   report still carries no scientific-convergence claim.

The test is an integration acceptance of ECatVASP contracts, not a real SSH/HPC or licensed VASP
execution test.

## External implementation references

The design was cross-checked against two established workflow systems without importing their
domain models:

- AiiDA keeps explicit persisted process states and reconstructable execution state instead of
  treating transport/scheduler observations as scientific truth.
- jobflow-remote separates graph representation, batch state, runner/daemon orchestration, and
  remote execution concerns.

ECatVASP intentionally remains smaller: v0.4 accepts one execution handoff and scheduler-only DAG
while keeping scientific identity in its existing Domain/Calculation/MethodFingerprint layer.

## Non-scope

Block 10 does not add:

- a workflow daemon or persistent service;
- distributed locks or multi-process queue ownership;
- Slurm arrays or scheduler-native dependency flags;
- concrete PBS/LSF execution;
- automatic VASP correction;
- automatic restart or CONTCAR continuation;
- scientific output parsing or convergence classification;
- DOS/Bader/COHP execution;
- thermodynamics or free-energy workflows;
- GUI functionality;
- schema migration;
- a tag, GitHub Release, or PyPI publication.

## Consequences

After Block 10, v0.4 has a deterministic final execution-handoff gate that can be used by later UI,
workflow, or scientific-analysis phases without granting those layers permission to reinterpret or
mutate the frozen execution provenance. Any future feature that changes this boundary requires a
new ADR.

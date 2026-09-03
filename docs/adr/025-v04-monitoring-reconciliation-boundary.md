# ADR-025: v0.4 Slurm Monitoring and Reconciliation Boundary

- Status: Accepted for v0.4 Block 6
- Scope: scheduler observation, state reconciliation, cancellation, and bounded VASP runtime telemetry

## Context

ADR-020 separates `ExecutionAttempt` from `RemoteJob`. ADR-023 establishes an integrity-verified remote runtime stage, and ADR-024 submits that stage to Slurm without treating scheduler acceptance as VASP or scientific success.

Block 6 must observe a submitted job without collapsing scheduler truth, process/runtime truth, and scientific truth into one status. It must also distinguish an unrecognized scheduler state from a job that can no longer be found, and it must support cancellation without pretending that a cancellation request has already taken effect.

## Decision

### 1. Scheduler observation failures are errors, not states

A failed `squeue`, failed `sacct`, transport error, malformed scheduler response, or conflicting scheduler response raises `SlurmObservationError`.

These failures are **not** normalized to `UNKNOWN` or `LOST`.

`UNKNOWN` means Slurm returned a concrete job record whose state token is syntactically valid but not mapped by this ECatVASP version.

`LOST` means both scheduler queries succeeded and ECatVASP could not find an allocation-level record for the requested job id.

This distinction prevents a temporary SSH/controller/accounting failure from being misclassified as job loss.

### 2. `squeue` is the live source; `sacct` is the accounting fallback

`SlurmAdapter.query()` first runs `squeue` for the numeric scheduler job id.

For one job id, the same id is supplied twice to `--jobs`. This follows the robust pattern used by AiiDA's Slurm plugin: some Slurm versions return a non-zero `Invalid job id specified` result when querying one job that has just left the queue, whereas duplicate ids permit a zero-exit empty result that can be followed by accounting lookup.

If `squeue` returns no live record, Block 6 runs `sacct --parsable2` and accepts only the exact allocation job id, excluding `.batch`, `.extern`, and other job-step rows.

If both commands succeed and no exact record exists, the normalized state is `LOST`.

### 3. Slurm states are normalized conservatively

Representative mappings are:

- pending/configuring/requeue states -> `PENDING`;
- running/completing/suspended/stage-out states -> `RUNNING`;
- `COMPLETED` -> `COMPLETED`;
- scheduler failure/preemption/deadline states -> `FAILED`;
- `TIMEOUT` -> `TIMEOUT`;
- `CANCELLED` -> `CANCELLED`;
- `NODE_FAIL` -> `NODE_FAIL`;
- `OUT_OF_MEMORY` -> `OUT_OF_MEMORY`;
- a future syntactically valid token -> `UNKNOWN`.

The raw value persisted in monitoring provenance is restricted to the normalized safe state token. Scheduler reasons, hostnames, node lists, controller messages, or cluster names are not written into portable project provenance.

### 4. Scheduler state maps only to execution-attempt state

Reconciliation updates `RemoteJob.state` and an immutable view of `ExecutionAttempt.status`.

The mapping is:

- `PENDING` -> `QUEUED`;
- `RUNNING` -> `RUNNING`;
- `COMPLETED` -> `EXITED`;
- `FAILED`, `TIMEOUT`, `NODE_FAIL`, `OUT_OF_MEMORY` -> `FAILED`;
- `CANCELLED` -> `CANCELLED`;
- `UNKNOWN`, `LOST` -> preserve the existing attempt status.

State regressions such as a previously `RUNNING` attempt becoming scheduler `PENDING` fail closed.

No Block 6 operation changes `CalculationScientificStatus`.

Most importantly, scheduler `COMPLETED` means only that the scheduler considers the allocation/process lifecycle complete. It does **not** mean a VASP calculation converged scientifically.

### 5. Cancellation is request + observation, not an invented terminal state

`SlurmAdapter.cancel()` sends `scancel` and then performs a normal scheduler query.

A successful `scancel` command does not itself produce `CANCELLED`. If the immediate scheduler observation is still `RUNNING`, the attempt remains `RUNNING`. Only an observed scheduler `CANCELLED` state reconciles the attempt to `CANCELLED`.

If `scancel` fails, Block 6 raises `SlurmObservationError` and does not fabricate a cancellation result.

### 6. VASP progress is bounded telemetry, not retrieval or scientific parsing

For non-pending, non-unknown, and non-lost jobs, Block 6 may inspect only bounded remote tails:

- last 40 lines of `OSZICAR`;
- last 200 lines of `OUTCAR`.

The telemetry may record:

- whether those files exist;
- latest visible ionic step;
- latest visible electronic iteration;
- whether the text marker `reached required accuracy` appears;
- whether the OUTCAR timing footer appears.

These markers are observational only. They do not set `Calculation` convergence, do not create parsed scientific results, and do not replace the later retrieval/parsing pipeline.

Full OUTCAR/OSZICAR download remains outside Block 6.

### 7. Monitoring observations are portable execution provenance

Each reconciliation persists a new `SCHEDULER_RECORD` Artifact under the existing `ExecutionAttempt` provenance directory.

The record contains only:

- attempt and remote-job ids;
- normalized scheduler job id and state token;
- sanitized target environment;
- target-relative remote directory;
- observation timestamp;
- bounded VASP runtime telemetry.

It does not persist SSH host aliases, absolute remote roots, node names, scheduler cluster names, credentials, POTCAR paths/bodies, or full VASP output files.

## Failure semantics

Block 6 fails closed on:

- non-numeric scheduler job ids;
- target/transport/scheduler mismatch;
- failed `squeue`, `sacct`, or `scancel` commands;
- malformed or conflicting scheduler state output;
- attempt/RemoteJob identity mismatch;
- impossible attempt-state regressions;
- telemetry probe errors other than a file simply not existing;
- duplicate local observation artifact paths.

## Scientific identity boundary

Scheduler state, cancellation, observation timestamps, OSZICAR/OUTCAR runtime markers, and monitoring artifacts are execution provenance only. None enter `MethodFingerprint`, modify scientific VASP input artifacts, change permanent `atom_uid`, or alter the scientific DAG.

## Explicit non-scope

Block 6 does not implement:

- retrieval of expected output Artifacts;
- output checksum/retrieval policy enforcement;
- full OUTCAR/vasprun parsing;
- scientific convergence classification;
- Bader, DOS/PDOS, charge-density, or COHP analysis;
- automatic error correction;
- retry/restart policy;
- batch scheduler DAG or Slurm arrays.

## Implementation references

- Slurm scheduler state and command behavior are aligned with the Slurm command-line interfaces used by `squeue`, `sacct`, and `scancel`.
- AiiDA's `SlurmScheduler` provides an external reference for treating queue disappearance separately from command failure and for normalizing scheduler states without equating scheduler completion with scientific success.

These references inform execution-adapter robustness only; ECatVASP retains its own frozen scientific-domain boundaries.

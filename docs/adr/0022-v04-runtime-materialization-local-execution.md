# ADR-022 — v0.4 Runtime Materialization and Local Execution Boundary

## Status

Accepted for v0.4 Block 3.

## Context

ADR-019 freezes `ExecutionPlan` as the immutable scientific-to-execution handoff. ADR-020
pins that plan to an `ExecutionAttempt`, and ADR-021 separates user-local execution targets,
credentials, transports, and schedulers from the scientific Domain.

The first concrete execution backend must prove that these boundaries can produce a runnable
VASP directory without mutating the scientific input set or persisting licensed POTCAR bodies
inside a portable project.

## Decision

### Transient runtime versus persistent provenance

Local execution uses two distinct directories:

1. a **transient run directory** outside the project root; and
2. an **ExecutionAttempt provenance directory** inside the project root.

The transient directory contains the exact VASP runtime files, including the locally licensed
concatenated `POTCAR`. The persistent directory contains only redistribution-safe provenance
artifacts.

A local run directory inside the project root is rejected. Existing run or attempt-artifact
directories are not overwritten.

### Runtime INCAR overlay

The Calculation-produced scientific `INCAR` remains immutable.

Block 3 may create an ExecutionAttempt-produced runtime `INCAR` by applying only the frozen
execution keys:

- `NCORE`
- `KPAR`
- `NPAR`

`NCORE` and `KPAR` come from `ExecutionSettings`; `NPAR` may be supplied through its
forward-compatible execution parameters. Any other execution extra parameter is rejected by
Block 3 rather than ignored.

If the scientific `INCAR` already contains `NCORE`, `KPAR`, or `NPAR`, materialization fails
closed. `NCORE` and `NPAR` may not both be set.

The runtime `INCAR` is mirrored into the persistent attempt provenance directory and recorded
as an `ExecutionAttempt`-produced `ArtifactType.INCAR`.

### Local POTCAR resolution

Block 3 consumes an already resolved local licensed `ResolvedPotcarSet` together with the
logical resolver identity selected by the `ExecutionTargetProfile`.

Before concatenation, ECatVASP verifies:

- resolver identity;
- POTCAR family;
- core-method hash;
- metadata hash;
- ordered element/symbol identities; and
- every licensed POTCAR file SHA-256.

The concatenated `POTCAR` exists only in the transient run directory. No POTCAR body, local
licensed path, or credential is written to the project provenance artifacts.

Remote POTCAR resolution remains Block 4 scope.

### Runtime provenance

Block 3 persists:

- `execution-plan.json` as `ArtifactType.EXECUTION_PLAN`;
- the runtime `INCAR` as `ArtifactType.INCAR`;
- `runtime-input-manifest.json` as an ExecutionAttempt-produced
  `ArtifactType.DERIVED_DATASET`;
- local process `stdout` as `ArtifactType.STDOUT`; and
- local process `stderr` as `ArtifactType.STDERR`.

The runtime manifest records digest-level identities for every transient input, including the
licensed concatenated POTCAR digest, but never its body or source filesystem path.

### LocalExecutor

`LocalExecutor` is scheduler-free and invokes the VASP executable with an argv vector and
`subprocess.run(..., shell=False)`.

Block 3 deliberately supports only a single MPI rank and does not synthesize launcher or module
commands. Scheduler resources, MPI launcher synthesis, and cluster module setup are deferred to
later blocks.

`OMP_NUM_THREADS` may be set from `ExecutionSettings.omp_threads` for the child process.

A process that successfully launches and returns transitions the attempt to `EXITED` regardless
of exit code. A non-zero process exit is an execution fact, not a scientific convergence
judgment. Failure to launch transitions the attempt to `FAILED`.

VASP outputs such as `OUTCAR`, `CONTCAR`, `CHGCAR`, `WAVECAR`, and `DOSCAR` remain in the
transient run directory in Block 3. They are not automatically parsed or promoted to project
Artifacts; retrieval is Block 7 scope.

## Consequences

- scientific MethodFingerprint identity is unchanged;
- licensed POTCAR bodies remain outside portable project state;
- local execution becomes a reference backend for later SSH/Slurm work;
- process exit and scientific convergence remain separate;
- runtime materialization is fail-closed and non-overwriting;
- Block 3 does not introduce scheduler, SSH, retrieval, retry, or automatic scientific
  correction behavior.

# ADR-024: v0.4 Slurm Resource and Submission Boundary

- Status: Accepted for v0.4 Block 5
- Scope: scheduler resource resolution, immutable Slurm job scripts, and first submission only

## Context

ADR-020 separates `ExecutionAttempt` from `RemoteJob`. ADR-021 keeps execution targets and credentials outside scientific identity. ADR-022 defines immutable attempt runtime materialization, and ADR-023 creates an integrity-verified SSH stage without transferring licensed POTCAR bodies.

Block 5 must convert the already-pinned `ExecutionSettings` and verified `RemoteStagePackage` into one scheduler submission without creating a second resource model, mutating scientific inputs, or treating scheduler acceptance as scientific success.

Slurm's native resource flags have scheduler-specific semantics. In particular, `--mem` is memory per allocated node, while `--ntasks`, `--ntasks-per-node`, and `--cpus-per-task` describe task and CPU topology. `sbatch --parsable` is the machine-readable submission interface and may return `job_id;cluster`.

## Decision

### 1. `ExecutionSettings` remains the portable execution intent

No Slurm settings are added to `MethodFingerprint`, `ProtocolDefinition`, or `RecipeIdentity`.

Block 5 resolves one immutable `ExecutionSettings` into `ResolvedSchedulerResources`. The resolved resource value is attempt-level execution provenance and has its own deterministic `resource_hash`.

A Slurm submission requires explicit:

- `nodes`;
- `cores`;
- `mpi_ranks`;
- `walltime_seconds`.

`partition` and `memory_mb` may be omitted so that a target's scheduler defaults apply, but the omission remains visible in the original `ExecutionSettings` and resolved resource value.

### 2. CPU topology is fail-closed and exact in v0.4

Block 5 does not guess unused-core layouts or hidden OpenMP settings.

The resolver requires:

- `mpi_ranks % nodes == 0`;
- `cores % nodes == 0`;
- if `cores != mpi_ranks`, `omp_threads` must be explicit;
- `mpi_ranks * omp_threads == cores`;
- if `KPAR` is set, `mpi_ranks % KPAR == 0`;
- if `NCORE` is set, `(mpi_ranks / KPAR) % NCORE == 0`.

When `cores == mpi_ranks` and `omp_threads` is omitted, the only exact topology is one thread per rank, so Block 5 resolves `omp_threads=1`.

This intentionally rejects resource slack that Slurm could technically allocate. Broader topology policies require a later explicit architecture decision rather than silent inference.

### 3. Portable `memory_mb` is total job memory

`ExecutionSettings.memory_mb` is interpreted as total requested memory for the execution attempt.

Because Slurm `--mem` is memory per node, Block 5 requires `memory_mb % nodes == 0` and emits:

`#SBATCH --mem=<memory_mb / nodes>M`

The resolver never rounds memory upward or downward. If the total cannot be represented exactly per node, submission fails closed.

### 4. Slurm job scripts are immutable `ExecutionAttempt` artifacts

The script fixes:

- job name derived from the attempt id;
- nodes;
- total MPI ranks;
- ranks per node;
- CPUs per task;
- walltime;
- optional per-node memory;
- optional single partition;
- stdout/stderr filenames;
- configured module loads;
- `OMP_NUM_THREADS`;
- logical launcher and VASP executable.

The script is persisted as `ArtifactType.JOB_SCRIPT`, uploaded to the verified remote stage, and verified by SHA-256 and byte size before `sbatch` is allowed.

The script deliberately does **not** embed the absolute remote work root. Submission uses `sbatch --chdir=<absolute verified stage> <absolute script>` so the immutable script remains free of host-specific work-root data while the transport operation still selects the exact stage.

### 5. Launcher behavior is explicit

For more than one MPI rank, `ExecutionTargetProfile.launcher` is required. Block 5 does not silently choose between `srun`, `mpirun`, or site-specific launchers.

For one MPI rank, a missing launcher means direct execution of the configured VASP executable.

Module identifiers, launcher names, executable names, partition names, and remote paths remain restricted by the security contracts from ADR-021/023.

### 6. Submission uses only machine-readable Slurm identity

`SlurmAdapter.submit()` invokes `sbatch --parsable` through the same `TransportAdapter` used for staging.

Accepted stdout is exactly one nonblank line containing:

- a numeric job id; or
- a numeric job id followed by `;cluster`.

Human-formatted output such as `Submitted batch job 123` is rejected rather than heuristically parsed.

A successful submission creates:

- a `RemoteJob` with scheduler `SLURM` and normalized state `PENDING`;
- an `ExecutionAttempt` view transitioned from `STAGING` to `QUEUED`;
- a local `SCHEDULER_RECORD` artifact containing scheduler job id, sanitized target snapshot, resource hash/resources, job-script SHA, relative remote stage, timestamp, and raw submit stdout/stderr.

Scheduler acceptance does not change scientific calculation status and does not imply VASP launch, convergence, or success.

### 7. Monitoring and cancellation remain Block 6

The concrete Slurm adapter exposes the existing scheduler protocol but `query()` and `cancel()` fail explicitly in Block 5. They must not return fabricated scheduler observations.

State reconciliation, raw-state normalization, `UNKNOWN` versus `LOST`, cancellation, accounting, and VASP runtime progress are deferred to Block 6.

## Failure semantics

Before `sbatch`, submission fails closed on:

- missing or ambiguous resource topology;
- incompatible `KPAR`/`NCORE` divisibility;
- unsafe partition name;
- target/scheduler/transport mismatch;
- executable mismatch;
- missing launcher for multi-rank execution;
- job-script local collision;
- uploaded job-script SHA or size mismatch;
- naive submission timestamp supplied by a caller.

If `sbatch` returns nonzero, no `RemoteJob` or scheduler-submission record is created. The immutable job script may remain as attempt provenance for a later infrastructure-level retry, consistent with ADR-020.

## Scientific identity boundary

Changing nodes, cores, MPI ranks, OpenMP threads, walltime, memory, partition, module loads, launcher, or target changes execution provenance and may require a new `ExecutionPlan` / `ExecutionAttempt` according to the v0.4 recovery policy. None of these values enter `MethodFingerprint`.

No Block 5 operation changes POSCAR, scientific INCAR content, KPOINTS, POTCAR identity, permanent `atom_uid`, Calculation identity, or the scientific DAG.

## Alternatives rejected

- **Put Slurm directives in the scientific Recipe** — rejected because scheduler resources are execution-only.
- **Infer MPI/OpenMP topology from allocated cores** — rejected because multiple valid layouts exist.
- **Use a default launcher such as `srun` for every cluster** — rejected because HPC sites differ.
- **Store absolute remote work roots in the immutable job script** — rejected because target-local path configuration is not portable scientific provenance.
- **Parse human-readable `sbatch` output heuristically** — rejected in favor of `--parsable`.
- **Treat accepted submission as Calculation success** — rejected because scheduler truth and scientific truth remain separate.

## Implementation references

- Slurm `sbatch` documentation: <https://slurm.schedmd.com/sbatch.html>
- Slurm multi-core/multi-thread support: <https://slurm.schedmd.com/mc_support.html>
- AiiDA Slurm handling provides a useful external reference for keeping scheduler submission failures and scheduler-state interpretation separate from scientific provenance. In particular, aiida-core PR #5850 documents invalid account/partition submission as a non-transient submission error rather than something that should mutate an immutable script: <https://github.com/aiidateam/aiida-core/pull/5850>

These references inform adapter behavior only; ECatVASP retains its own frozen Domain and scientific-identity contracts.

# ADR-026 — v0.4 Retrieval and Remote Artifact Lifecycle Boundary

Status: Accepted for v0.4 Block 7

## Context

Blocks 4–6 establish immutable remote staging, Slurm submission, scheduler monitoring, and bounded runtime telemetry. The next boundary is movement of concrete VASP outputs from an execution target into portable project artifacts without confusing scheduler completion with scientific success or silently deleting remote data.

`ExecutionPlan.expected_outputs` already defines the output role, artifact type, relative path, retrieval policy, and required/optional contract. `Artifact` already separates `LOCAL`, `REMOTE`, `BOTH`, and `MISSING` availability. Block 7 must use these existing contracts rather than introduce a second output model.

## Decision

### 1. Retrieval consumes immutable ExecutionPlan output contracts

Remote retrieval requires exact provenance continuity:

- `ExecutionAttempt.calculation_id == ExecutionPlan.calculation_id`;
- `ExecutionAttempt.execution_plan_hash == ExecutionPlan.plan_hash`;
- `ExecutionAttempt.input_manifest_hash == ExecutionPlan.input_manifest_sha256`;
- `RemoteJob.execution_attempt_id == ExecutionAttempt.id`;
- the remote directory remains target-relative and confined below the configured execution root.

No retrieval operation changes MethodFingerprint, scientific inputs, permanent `atom_uid`, or the scientific DAG.

### 2. Scheduler completion is not scientific success

Retrieval is allowed only after a terminal scheduler observation. A normally exited attempt may move `EXITED -> RETRIEVING`. Failed or cancelled attempts retain their terminal execution status while forensic outputs may still be retrieved.

Required-output enforcement is strict for normally exited/retrieving attempts. Failed/cancelled attempts may legitimately lack otherwise-required files, so missing files are recorded rather than converted into a false scientific conclusion.

Block 7 does not set `CalculationScientificStatus.CONVERGED`, `COMPLETED_UNCONVERGED`, or `FAILED` from file presence alone.

### 3. RetrievalPolicy is preserved exactly

- `ALWAYS`: retrieve locally whenever the remote file exists.
- `ON_DEMAND`: retain remotely by default; retrieve only when the role is explicitly requested.
- `REMOTE_ONLY`: never cross the transport boundary into local project storage.
- `DISCARDABLE`: retain remotely by default. It may be retrieved explicitly, or explicitly discarded after integrity observation.

`DISCARDABLE` does not mean automatic deletion.

### 4. Remote inspection precedes all movement

Retrieval is two-phase.

Phase A performs a complete read-only preflight of every expected output:

1. remote file existence;
2. SHA-256;
3. byte size;
4. required-output gate.

No download or remote deletion occurs until this full preflight succeeds. This prevents partial destructive retention changes if a later required output is missing or a remote integrity probe fails.

### 5. Download is checksum-gated

A selected output is downloaded to a temporary local path. The local byte size and SHA-256 must match the preflight remote observation before the file is atomically promoted to its project artifact location.

An already-present local output may be reused only when its size and SHA-256 exactly match the current remote observation. Retrieval never silently overwrites a divergent local artifact.

### 6. Remote retention is opt-in and fail closed

Default behavior is to retain remote outputs.

`release_remote_roles` may remove a remote copy only after a verified local copy exists. `REMOTE_ONLY` outputs cannot be released by this mechanism.

`discard_remote_roles` is accepted only for `DISCARDABLE` outputs and means deletion without requiring a local copy.

Immediately before deletion, the remote file is re-observed and its SHA-256/size must still match the original preflight observation. Deletion is then verified by a follow-up existence check. A changed or ambiguous remote file is never deleted.

### 7. Artifact availability records final location truth

For an observed output:

- verified local + retained remote -> `BOTH`;
- verified local + released remote -> `LOCAL`;
- remote only -> `REMOTE`;
- explicitly discarded remote or missing output -> `MISSING`.

Missing expected outputs retain the target-relative expected remote path in Artifact metadata so `REMOTE_ONLY` policy remains representable without asserting current existence.

All output Artifacts are produced by the exact `ExecutionAttempt`.

### 8. Retrieval manifest is portable provenance

Each retrieval operation produces an attempt-level `RETRIEVAL_MANIFEST` Artifact containing:

- attempt and RemoteJob ids;
- exact ExecutionPlan hash;
- sanitized target snapshot;
- target-relative remote directory;
- explicit request/release/discard role sets;
- timestamp;
- per-output policy, required flag, remote presence, remote SHA-256/size, local retrieval state, remote retention state, and final Artifact availability.

The manifest does not contain SSH host aliases, absolute remote work roots, credentials, licensed POTCAR paths/bodies, scheduler cluster details, or full VASP output bodies.

### 9. Scientific parsing remains outside Block 7

OUTCAR, CONTCAR, DOSCAR, CHGCAR, WAVECAR, and related files are retrieved as execution-produced Artifacts only. Energy extraction, convergence classification, structure import, DOS parsing, charge analysis, and other scientific interpretation remain downstream responsibilities.

## Consequences

- Project storage can represent partial retrieval without losing remote provenance.
- Large files such as WAVECAR can remain remote until explicitly needed.
- Remote cleanup is explicit, checksum-gated, and auditable.
- Failed attempts can preserve useful forensic outputs without pretending the calculation succeeded.
- Retrieval can be rerun safely when a previously downloaded local file is byte-identical to the current remote output.

## Explicit non-scope

Block 7 does not implement full scientific parsing, convergence classification, automatic retry/restart, VASP error correction, batch dispatch, scheduler DAGs, Slurm arrays, concrete PBS/LSF adapters, GUI behavior, tags, GitHub Releases, or PyPI publication.

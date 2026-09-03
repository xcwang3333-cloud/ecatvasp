# ADR-029: v0.4 Final Execution Result Handoff and Acceptance Boundary

- Status: Accepted
- Date: 2026-09-03

## Context

v0.4 now has explicit contracts for ExecutionAttempt provenance, execution targets, local runtime
materialization, SSH staging, Slurm submission and monitoring, output retrieval, recovery, and batch
dispatch. The phase still needs one final boundary that answers a narrower question: what exact
execution evidence may cross from the execution layer into future parsing and analysis?

A scheduler terminal state, a zero process return code, or the existence of an OUTCAR is not by
itself a scientific convergence statement. Conversely, future parsers need stable, integrity-backed
Artifact identities rather than transient run-directory paths or scheduler implementation details.

The design was cross-checked against provenance-oriented workflow systems such as AiiDA, which
separate process status/exit status from the data outputs recorded in provenance. ECatVASP retains
its smaller execution-only model and does not adopt AiiDA's database or workflow engine.

## Decision

### 1. `ExecutionResultHandoff` is an execution-layer value object

The final v0.4 handoff is `ExecutionResultHandoff`. It is not a Domain entity and is not added to
ProjectBundle schema. It records:

- exact Calculation id;
- exact ExecutionAttempt id and attempt number;
- exact ExecutionPlan hash and input-manifest hash;
- execution-settings hash;
- sanitized execution-target snapshot;
- local or remote execution source;
- attempt-produced output Artifacts;
- local process exit code when applicable;
- RemoteJob id and scheduler terminal state when applicable;
- retrieval-manifest Artifact id for remote execution;
- deterministic handoff hash.

It deliberately has no `scientific_success`, `converged`, energy, geometry, or parsed-result field.

### 2. Output Artifacts, not transient paths, cross the parsing boundary

Future parsers consume attempt-produced Artifacts from the handoff. Every available output must
carry SHA-256 and size provenance. Required outputs must exist for EXITED / RETRIEVING / PARSED
attempts. FAILED / CANCELLED attempts may still expose partial evidence, but the handoff never
upgrades that evidence into scientific success.

The current v0.4 handoff requires one unique ArtifactType per expected output. This matches every
v0.3 ExecutionPlan output contract currently shipped and fails closed if a future recipe introduces
ambiguous duplicate ArtifactTypes.

### 3. Local VASP outputs are persisted after process exit

`LocalExecutor` intentionally leaves VASP scientific outputs in a transient run directory. Block 10
adds `collect_local_outputs()` to close that lifecycle:

1. verify exact Calculation / ExecutionPlan / ExecutionAttempt identity;
2. inspect only paths declared by `ExecutionPlan.expected_outputs`;
3. require declared required outputs after an EXITED attempt;
4. calculate file size and SHA-256;
5. copy to `artifacts/execution/<attempt>/outputs/...` through a temporary file;
6. verify the persisted copy before atomic replacement;
7. create ExecutionAttempt-produced Artifacts;
8. never parse VASP scientific content.

Existing persisted output files are accepted only if their size and digest exactly match. Otherwise
collection fails closed.

### 4. Licensed POTCAR remains transient

The final handoff never contains a POTCAR body or licensed POTCAR path. Local runtime
materialization may place POTCAR in the transient run directory, but Block 10 persists only declared
VASP outputs and existing redistribution-safe execution provenance.

### 5. Local and remote results share one contract

Local handoff:

- target transport must be LOCAL;
- no RemoteJob or scheduler state is present;
- process exit code is retained as execution evidence;
- a non-zero process exit code is not converted into a scientific conclusion.

Remote handoff:

- target transport must be SSH;
- RemoteJob id and scheduler state are required;
- retrieval-manifest Artifact provenance is required;
- no synthetic local process return code is invented.

### 6. v0.4 acceptance boundary

The accepted v0.4 execution path is:

`ExecutionPlan -> ExecutionAttempt -> runtime/staging -> execution/submission -> monitoring ->`
`retrieval/output persistence -> ExecutionResultHandoff`

Batch scheduling may select which exact ExecutionPlan/ExecutionAttempt proceeds, but it cannot
change the scientific DAG or bypass Block 8 recovery classification.

A completed execution handoff means only that execution evidence is ready for a future parser. The
parser and scientific convergence assessment remain later-layer responsibilities.

## Consequences

- v0.4 now has a single auditable boundary for future parser integration.
- local and remote outputs expose the same Artifact-level provenance model.
- transient local outputs no longer need to be referenced by scratch-directory paths after handoff.
- scheduler success and process exit codes remain execution facts, not scientific results.
- no ProjectBundle schema migration is required.
- no MethodFingerprint or scientific Calculation identity changes are introduced.

## Explicit non-scope

Block 10 does not add:

- VASP OUTCAR/vasprun parsing;
- convergence or energy interpretation;
- automatic scientific error correction;
- CONTCAR continuation;
- Bader / DOS / PDOS / COHP analysis execution;
- LOBSTER execution;
- thermodynamics or free-energy workflows;
- Slurm arrays;
- concrete PBS/LSF adapters;
- a persistent daemon or distributed workflow engine;
- GUI work;
- tag, GitHub Release, or PyPI publication.

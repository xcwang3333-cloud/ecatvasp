# ADR-023: v0.4 SSH Staging and Remote POTCAR Integrity Boundary

Status: Accepted

## Context

ECatVASP v0.4 must move immutable `ExecutionPlan` inputs to an HPC execution host without weakening the v0.3 scientific-input, provenance, credential, or POTCAR licensing boundaries. SSH introduces two additional risks: remote paths and commands pass through the server login shell, and licensed POTCAR files must already exist on the execution host rather than being copied from the project or local workstation.

## Decision

Block 4 implements remote staging only for `ExecutionTargetProfile` values using `TransportKind.SSH` and an explicit scheduler family. Scheduler submission remains outside this block.

### System OpenSSH boundary

ECatVASP uses the system `ssh` / `scp` clients. Authentication is delegated to the user's OpenSSH configuration and agent. ECatVASP does not accept or persist passwords, private-key bodies, passphrases, or tokens.

SSH calls require batch mode and strict host-key checking. Host aliases, work-root components, target-relative remote path components, and remote command arguments must be literal allowlisted tokens. Shell expansion, interpolation, whitespace, quoting syntax, command separators, globbing, and parent traversal are rejected rather than escaped or guessed.

### Isolated stage directory

Each `ExecutionAttempt` stages into one isolated directory beneath the configured remote work root:

`execution/<execution_attempt_id>`

The parent may be created idempotently, but the attempt directory itself must be newly created. Existing attempt directories are not overwritten or reused. A failed integrity gate may leave a partial isolated directory for forensic inspection; it is not treated as a successful stage.

### Redistribution-safe staging

Only the redistribution-safe files named by the immutable `ExecutionPlan.staging_inputs` are uploaded. Every source file is re-read and re-hashed locally before upload. Every uploaded remote file is then checked by remote SHA-256 and byte size before staging may continue.

The Calculation-produced scientific INCAR remains immutable. The remote runtime INCAR is derived using the already-frozen Block 3 execution overlay, limited to `NCORE`, `KPAR`, and `NPAR`, and is verified after upload.

`execution-plan.json` is staged as execution provenance. The project persists an ExecutionAttempt-produced copy of the execution plan, runtime INCAR, and remote-stage manifest.

### Remote-only licensed POTCAR resolution

A `RemotePotcarLibrary` is user-local execution configuration identified by the target's logical `potcar_resolver_id`. Its licensed root path is not scientific project data and is not persisted in portable provenance.

For each ordered `PotcarResolutionRequest` entry, the execution host resolves:

`<remote licensed root>/<symbol>/POTCAR`

The remote member SHA-256 must exactly equal the immutable digest requested by the ExecutionPlan. Any missing member, order mismatch, resolver mismatch, family mismatch, or digest mismatch fails closed.

After all members pass verification, they are concatenated only on the remote execution host into the staged `POTCAR`. POTCAR bodies are never uploaded from the project, never downloaded by Block 4, and never stored as portable project Artifacts. The remote-stage manifest may record only the resulting staged POTCAR digest and size with `licensed=true`; it does not contain licensed source paths or bodies.

### Remote stage manifest

A successful stage produces a deterministic `REMOTE_STAGE_MANIFEST` containing:

- ExecutionAttempt id;
- ExecutionPlan hash;
- execution settings hash;
- sanitized execution environment snapshot;
- target-relative attempt directory;
- staged file roles, relative paths, SHA-256 digests, sizes, and license flag.

The manifest must not contain SSH host aliases, absolute remote work roots, remote licensed POTCAR roots, credential material, or POTCAR bodies.

## Consequences

- Staging cannot silently accept network, filesystem, or licensed-data drift.
- `$USER`, `~`, shell metacharacters, and ambiguous remote path expansion are intentionally unsupported in managed remote paths.
- Block 4 does not create a `RemoteJob`, render a scheduler script, submit Slurm, monitor jobs, retrieve outputs, or decide scientific convergence.
- Remote directory cleanup and retention policy remain deferred to the retrieval/lifecycle block.
